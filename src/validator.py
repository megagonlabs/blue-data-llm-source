import logging
import pandas as pd
import json
import asyncio
from typing import List, Dict, Any, Optional, Tuple
from abc import ABC, abstractmethod
import re

from utils.llm_utils import LLMMetrics, sum_metrics

from src.prompts import (
    CHUNK_REFINEMENT_PROMPT,
    ENTITY_RESOLUTION_PROMPT,
    GEN_ASSERTION_PROMPT,
    FIX_DATA_PROMPT,
    VALUE_VALIDATION_PROMPT
)

logger = logging.getLogger(__name__)

def _append_validation_log(output_dir: Optional[str], entry: Dict[str, Any]) -> None:
    import os
    import time

    if not output_dir:
        return

    try:
        with open(os.path.join(output_dir, "validation_log.jsonl"), "a") as f:
            f.write(json.dumps({"timestamp": time.time(), **entry}) + "\n")
    except Exception as e:
        logger.warning(f"Failed to write validation log: {e}")

def _is_blank_value(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, (list, tuple, set, dict)):
        return len(value) == 0
    try:
        is_na = pd.isna(value)
        if hasattr(is_na, "all"):
            return bool(is_na.all())
        if bool(is_na):
            return True
    except Exception:
        pass
    if isinstance(value, str) and not value.strip():
        return True
    return False

def _label_tokens(label: str) -> List[str]:
    return [tok for tok in re.split(r'[^a-z0-9]+', str(label).lower()) if tok]

def _normalize_label(label: str) -> str:
    return "".join(_label_tokens(label))

def _align_row_to_target_columns(row: Dict[str, Any], target_columns: List[str]) -> Dict[str, Any]:
    """
    Align LLM-repaired row keys back to schema/dataframe columns.

    This protects against validator repair drift such as returning "Laureate Name"
    when the actual schema column is "Laureate".
    """
    if not row or not target_columns:
        return row

    exact_targets = {col: col for col in target_columns}
    lower_targets = {str(col).lower(): col for col in target_columns}

    norm_targets: Dict[str, List[str]] = {}
    token_targets: Dict[str, set] = {}
    for col in target_columns:
        norm_targets.setdefault(_normalize_label(col), []).append(col)
        token_targets[col] = set(_label_tokens(col))

    aligned: Dict[str, Any] = {}
    unmatched: List[Tuple[str, Any]] = []

    for key, value in row.items():
        mapped = None
        if key in exact_targets:
            mapped = exact_targets[key]
        else:
            lower_key = str(key).lower()
            if lower_key in lower_targets:
                mapped = lower_targets[lower_key]
            else:
                norm_key = _normalize_label(key)
                norm_candidates = norm_targets.get(norm_key, [])
                if len(norm_candidates) == 1:
                    mapped = norm_candidates[0]

        if mapped and mapped not in aligned:
            aligned[mapped] = value
        else:
            unmatched.append((key, value))

    for key, value in unmatched:
        key_tokens = set(_label_tokens(key))
        if not key_tokens:
            continue

        scored = []
        for target in target_columns:
            if target in aligned:
                continue
            target_tokens = token_targets[target]
            overlap = len(key_tokens & target_tokens)
            if overlap == 0:
                continue
            score = (
                overlap,
                int(key_tokens <= target_tokens or target_tokens <= key_tokens),
                -abs(len(key_tokens) - len(target_tokens))
            )
            scored.append((score, target))

        if not scored:
            continue

        scored.sort(reverse=True)
        best_score, best_target = scored[0]
        if len(scored) == 1 or best_score > scored[1][0]:
            aligned[best_target] = value

    return aligned

def _align_rows_to_target_columns(rows: List[Dict[str, Any]], target_columns: List[str]) -> List[Dict[str, Any]]:
    return [_align_row_to_target_columns(row, target_columns) for row in rows]

# --- Base Interfaces ---

class BaseValidator(ABC):
    """
    Abstract Base Validator.
    Follows the Detect -> Fix pattern.
    """
    def __init__(self, llm_client=None):
        self.llm = llm_client

    @abstractmethod
    async def detect(self, df: pd.DataFrame, context: Dict[str, Any] = None) -> Any:
        """
        Identifies issues in the DataFrame.
        Returns a 'report' object (can be list of indices, dict of errors, etc).
        """
        pass

    @abstractmethod
    async def fix(self, df: pd.DataFrame, report: Any, context: Dict[str, Any] = None) -> Tuple[pd.DataFrame, LLMMetrics]:
        """
        Fixes the issues identified in the report.
        Returns the cleaned DataFrame and the metrics.
        """
        pass

    async def run(self, df: pd.DataFrame, context: Dict[str, Any] = None) -> Tuple[pd.DataFrame, LLMMetrics]:
        """
        Orchestrates Detect -> Fix.
        Returns (DataFrame, Metrics).
        """
        metrics = LLMMetrics()
        if df.empty:
            return df, metrics
            
        report, detect_metrics = await self.detect(df, context)
        metrics = metrics + detect_metrics
        
        # If report indicates no errors, return early
        if not report: 
            return df, metrics
            
        fixed_df, fix_metrics = await self.fix(df, report, context)
        metrics = metrics + fix_metrics
        
        return fixed_df, metrics


class DataValidator:
    """
    Unified Orchestrator for Validation Pipelines.
    """
    def __init__(self, llm_client, strategies: List[str] = None, dedup_strategy: str = "first"):
        self.llm = llm_client
        self.strategies = strategies or []
        self.dedup_strategy = dedup_strategy
        self.validators = self._initialize_validators()

    def _initialize_validators(self) -> List[BaseValidator]:
        params = {"llm_client": self.llm}
        validator_map = {
            "ChunkConstraints": ChunkValidator(**params),
            "Deduplication": DeduplicationValidator(llm_client=self.llm, strategy=self.dedup_strategy),
            "Code": CodeValidator(**params),
            "Embedding": EmbeddingValidator(**params),
            "FormatNormalization": FormatNormalizationValidator(**params),
            "EntityResolution": EntityResolutionValidator(**params),
            "PrimaryKeyValidation": PrimaryKeyValidationValidator(**params),
            "Completeness": CompletenessValidator(**params)
        }
        
        active = []
        for s in self.strategies:
            if s in validator_map:
                active.append(validator_map[s])
            else:
                logger.warning(f"Unknown validator strategy: {s}")
        return active

    async def run(self, df: pd.DataFrame, context: Dict[str, Any] = None) -> Tuple[pd.DataFrame, LLMMetrics]:
        """
        Sequentially runs all configured validators.
        """
        context = context or {}
        total_metrics = LLMMetrics()
        for validator in self.validators:
            try:
                rows_before = len(df)
                logger.info(f"Running Validator: {validator.__class__.__name__} ({rows_before} rows in)")
                df, metrics = await validator.run(df, context)
                rows_after = len(df)
                if rows_before != rows_after:
                    logger.warning(
                        f"Validator {validator.__class__.__name__}: "
                        f"dropped {rows_before - rows_after} rows "
                        f"({rows_before} → {rows_after})"
                    )
                else:
                    logger.info(f"Validator {validator.__class__.__name__}: no rows dropped ({rows_after} rows out)")
                _append_validation_log(context.get("output_dir"), {
                    "event_type": "validator_run",
                    "validator": validator.__class__.__name__,
                    "rows_before": rows_before,
                    "rows_after": rows_after
                })
                if metrics:
                    total_metrics = total_metrics + metrics
            except Exception as e:
                logger.error(f"Validator {validator.__class__.__name__} failed: {e}")
        return df, total_metrics


# --- Concrete Strategies ---

class ChunkValidator(BaseValidator):
    """
    Enforces partition constraints (e.g. Year=2020) and General Data Quality.
    Uses LLM to validate values against constraints, types, and schema.
    """
    async def detect(self, df: pd.DataFrame, context: Dict[str, Any] = None) -> Tuple[List[int], LLMMetrics]:
        """Returns list of indices violating constraints using LLM-based value validation."""
        metrics = LLMMetrics()
        constraints = context.get('constraints', {})
        
        # Determine columns to validate (Constraints + PKs)
        columns_to_validate = list(constraints.keys())
        
        schema = context.get('schema')
        primary_keys = schema.primary_keys if schema else []
        for pk in primary_keys:
            if pk not in columns_to_validate and pk in df.columns:
                columns_to_validate.append(pk)
        
        if not columns_to_validate:
            return [], metrics
            
        invalid_indices = set()
        
        for col in columns_to_validate:
            if col not in df.columns:
                continue
                
            # 1. Extract Unique Values to reduce LLM tokens
            unique_values = df[col].dropna().unique().tolist()
            if len(unique_values) > 100:
                # heuristic: take a mix
                unique_values = unique_values[:50] + unique_values[-50:]
                
            if not unique_values:
                continue
            
            # Extract Schema Metadata
            semantic_type = "Unknown"
            formatting = "None"
            if schema:
                for c in schema.columns:
                    if c.name == col:
                        semantic_type = c.semantic_type
                        formatting = c.formatting
                        break
                
            # 2. Query LLM
            prompt = VALUE_VALIDATION_PROMPT.format(
                constraint=constraints.get(col, "General Quality Check (PK/Type)"),
                column=col,
                dtype=str(df[col].dtype),
                semantic_type=semantic_type,
                formatting=formatting,
                values=json.dumps(unique_values, default=str)
            )
            
            try:
                response = await self.llm.agenerate(prompt, temperature=0.0, response_format={"type": "json_object"})
                if response.metrics:
                    metrics = metrics + response.metrics
                content = response.content
                
                # Save I/O
                if context and "output_dir" in context:
                    self._log_val_io(context["output_dir"], "ChunkValidator.detect_val", prompt, content, len(unique_values))
                
                if "```json" in content:
                    content = content.split("```json")[1].split("```")[0]
                data = json.loads(content)
                
                invalid_list = data.get("invalid_values", [])
                
                # 3. Map back to indices
                invalid_vals = set(invalid_list)
                
                # Check for string match (safer for LLM output)
                mask = df[col].apply(lambda x: str(x) in invalid_vals)
                col_invalid_indices = df[mask].index.tolist()
                
                invalid_indices.update(col_invalid_indices)
                
            except Exception as e:
                logger.warning(f"ChunkValidator value validation failed for {col}: {e}")
                
        unique_invalid = sorted(list(invalid_indices))
        
        if context and "output_dir" in context:
            # Log actual content of invalid rows
            try:
                invalid_rows_data = df.loc[unique_invalid].to_dict(orient='records')
            except Exception as e:
                invalid_rows_data = str(e)

            self._log_event(context["output_dir"], "detect", {
                "validator": "ChunkValidator",
                "constraints": constraints,
                "total_rows": len(df),
                "invalid_count": len(unique_invalid),
                "invalid_indices": unique_invalid,
                "invalid_rows": invalid_rows_data
            })
            
        return unique_invalid, metrics

    async def fix(self, df: pd.DataFrame, report: List[int], context: Dict[str, Any] = None) -> Tuple[pd.DataFrame, LLMMetrics]:
        """Attempts to refine violating rows via LLM, or drops them."""
        metrics = LLMMetrics()
        context = context or {}
        constraints = context.get('constraints', {})
        schema = context.get('schema')
        target_columns = [c.name for c in schema.columns] if schema and getattr(schema, "columns", None) else list(df.columns)
        try:
            invalid_rows = df.loc[report].to_dict('records')
        except:
            invalid_rows = []

        if not self.llm:
            return df.drop(report), metrics

        logger.info(f"ChunkValidator: Attempting to refine {len(report)} rows.")
        
        prompt = CHUNK_REFINEMENT_PROMPT.format(
            constraints=json.dumps(constraints),
            rows=json.dumps(invalid_rows, indent=2)
        )
        if target_columns:
            prompt += (
                "\n\nIMPORTANT: Preserve these exact output column names when returning fixed rows:\n"
                f"{json.dumps(target_columns)}\n"
                "Do not invent renamed columns or synonyms."
            )
        
        try:
            response = await self.llm.agenerate(prompt, temperature=0.0, response_format={"type": "json_object"})
            if response.metrics:
                metrics = metrics + response.metrics
            content = response.content
            
            # Save LLM I/O for Validation
            if context and "output_dir" in context:
                self._log_val_io(
                    output_dir=context["output_dir"],
                    task_type="ChunkValidator.fix",
                    prompt=prompt,
                    response=content,
                    input_count=len(invalid_rows)
                )

            if "```json" in content:
                content = content.split("```json")[1].split("```")[0]
            data = json.loads(content)
            fixed_rows = data.get("fixed_rows", [])
            aligned_fixed_rows = _align_rows_to_target_columns(fixed_rows, target_columns)
            
            # Reconstruct DataFrame
            df_dropped = df.drop(report)
            if aligned_fixed_rows:
                df_fixed = pd.DataFrame(aligned_fixed_rows)
                final_df = pd.concat([df_dropped, df_fixed], ignore_index=True)
            else:
                final_df = df_dropped

            # Log Event
            if context and "output_dir" in context:
                self._log_event(context["output_dir"], "fix", {
                    "validator": "ChunkValidator",
                    "attempted_fix_count": len(report),
                    "successfully_fixed_count": len(fixed_rows),
                    "aligned_fixed_rows": aligned_fixed_rows,
                    "dropped_count": len(report) - len(fixed_rows),
                    "fixed_rows": fixed_rows,
                    "prompt": prompt,
                    "llm_output": content
                })

            return final_df, metrics
            
        except Exception as e:
            logger.warning(f"ChunkValidator Fix failed: {e}")
            return df.drop(report), metrics

    def _log_val_io(self, output_dir: str, task_type: str, prompt: str, response: str, input_count: int):
        import os
        import time
        try:
            trace_entry = {
                "timestamp": time.time(),
                "task_type": task_type,
                "prompt": prompt,
                "raw_response": response,
                "input_item_count": input_count
            }
            with open(os.path.join(output_dir, "tasks_io.jsonl"), "a") as f:
                f.write(json.dumps(trace_entry) + "\n")
        except Exception as e:
            logger.warning(f"Failed to log validation I/O: {e}")

    def _log_event(self, output_dir: str, event_type: str, details: Dict):
        import os
        import time
        try:
            log_entry = {
                "timestamp": time.time(),
                "event_type": event_type,
                **details
            }
            with open(os.path.join(output_dir, "validation_log.jsonl"), "a") as f:
                f.write(json.dumps(log_entry) + "\n")
        except Exception as e:
            logger.warning(f"Failed to write validation log: {e}")


class DeduplicationValidator(BaseValidator):
    def __init__(self, llm_client=None, strategy: str = "first"):
        super().__init__(llm_client)
        self.strategy = strategy
        from src.prompts import DEDUPLICATION_RESOLUTION_PROMPT
        self.prompt_template = DEDUPLICATION_RESOLUTION_PROMPT

    async def detect(self, df: pd.DataFrame, context: Dict[str, Any] = None) -> Tuple[Any, LLMMetrics]:
        # Returns:
        # - List[int] (indices to drop) if strategy="first"
        # - List[List[int]] (groups of duplicates) if strategy="llm_based"
        # - LLMMetrics
        
        metrics = LLMMetrics()
        schema = context.get('schema')
        primary_keys = schema.primary_keys if schema else []
        
        if self.strategy == "first":
            # Original Logic: Return indices to drop
            invalid_indices = []
            if primary_keys:
                is_dup = df.duplicated(subset=primary_keys, keep='first')
                invalid_indices.extend(df[is_dup].index.tolist())
                if any(is_dup):
                    logger.info(f"DeduplicationValidator (first): Found {sum(is_dup)} duplicates based on PK: {primary_keys}")
            else:
                try:
                    is_dup = df.astype(str).duplicated(keep='first')
                    invalid_indices.extend(df[is_dup].index.tolist())
                    if any(is_dup):
                        logger.info(f"DeduplicationValidator (first): Found {sum(is_dup)} exact duplicates (No PK).")
                except:
                    pass
            return sorted(list(set(invalid_indices))), metrics
            
        elif self.strategy == "llm_based":
            # New Logic: Return clusters of duplicates
            duplicate_groups = []
            if primary_keys:
                # Find all duplicates (mark all)
                dup_mask = df.duplicated(subset=primary_keys, keep=False)
                if any(dup_mask):
                    # Group by PK
                    dups = df[dup_mask]
                    grouped = dups.groupby(primary_keys)
                    for _, group in grouped:
                        duplicate_groups.append(group.index.tolist())
                    logger.info(f"DeduplicationValidator (llm_based): Found {len(duplicate_groups)} duplicate groups based on PK.")
            else:
                # Fallback to exact match? Exact match doesn't need LLM usually.
                # Only strictly identical rows.
                # If rows are strictly identical, LLM can't distinguish.
                # So we fallback to 'first' logic for non-PK duplicates if they are identical content.
                # But if we want to be safe, let's just return nothing or use 'first' logic.
                pass
            return duplicate_groups, metrics
            
        return [], metrics

    async def fix(self, df: pd.DataFrame, report: Any, context: Dict[str, Any] = None) -> Tuple[pd.DataFrame, LLMMetrics]:
        metrics = LLMMetrics()
        if not report:
            return df, metrics
            
        if self.strategy == "first":
            # Report is List[int]
            logger.info(f"DeduplicationValidator: Dropping {len(report)} rows (Strategy: first).")
            return df.drop(report), metrics
            
        elif self.strategy == "llm_based":
            # Report is List[List[int]]
            indices_to_drop = []
            
            for group_indices in report:
                if len(group_indices) < 2: 
                    continue
                    
                group = df.loc[group_indices]
                
                # optimization: if rows are identical content-wise, pick first
                # Check for strict equality
                # Convert to dicts hashable?
                # Just check if all rows equal first row
                first_row = group.iloc[0]
                all_equal = True
                for i in range(1, len(group)):
                    if not group.iloc[i].equals(first_row):
                        all_equal = False
                        break
                        
                if all_equal:
                    # No need for LLM
                    indices_to_drop.extend(group.index[1:].tolist())
                    continue
                    
                # Content differs -> Conflict Resolution via LLM
                conflicts = group.to_dict(orient='records')
                # Map local index
                labeled_conflicts = [{**row, "_local_idx": i} for i, row in enumerate(conflicts)]
                
                entity_desc = f"PK: {group_indices}" # Or better valid description if possible
                
                prompt = self.prompt_template.format(
                    entity=entity_desc,
                    conflicts=json.dumps(labeled_conflicts, indent=2, default=str),
                    source=context.get('source', 'Wikipedia')
                )
                
                try:
                    response = await self.llm.agenerate(prompt, temperature=0.0, response_format={"type": "json_object"})
                    content = response.content
                    if "```json" in content:
                        content = content.split("```json")[1].split("```")[0]
                    data = json.loads(content)
                    
                    keep_idx = data.get("keep_index", 0)
                    
                    # Map back to DF index
                    # keep_idx is index in 'conflicts' list
                    if 0 <= keep_idx < len(group):
                        df_keep_idx = group.index[keep_idx]
                        # Drop others
                        for idx in group.index:
                            if idx != df_keep_idx:
                                indices_to_drop.append(idx)
                    else:
                        # Fallback
                        indices_to_drop.extend(group.index[1:].tolist())
                        
                except Exception as e:
                    logger.warning(f"DeduplicationValidator Resolution Failed: {e}")
                    indices_to_drop.extend(group.index[1:].tolist())
                    
            if indices_to_drop:
                logger.info(f"DeduplicationValidator: Dropping {len(indices_to_drop)} rows (Strategy: llm_based).")
                return df.drop(indices_to_drop), metrics
                
        return df, metrics

class PrimaryKeyValidationValidator(BaseValidator):
    """
    Enforces primary-key presence and uniqueness after reduction. This is stricter than
    plain deduplication because rows with missing PK parts are invalid even if unique.
    """
    async def detect(self, df: pd.DataFrame, context: Dict[str, Any] = None) -> Tuple[Dict[str, Any], LLMMetrics]:
        metrics = LLMMetrics()
        context = context or {}
        schema = context.get('schema')
        primary_keys = schema.primary_keys if schema else []

        if not primary_keys or df.empty:
            return {}, metrics

        missing_columns = [pk for pk in primary_keys if pk not in df.columns]
        if missing_columns:
            report = {
                "missing_columns": missing_columns,
                "missing_pk_rows": [],
                "duplicate_groups": []
            }
            _append_validation_log(context.get("output_dir"), {
                "event_type": "detect",
                "validator": "PrimaryKeyValidationValidator",
                "missing_columns": missing_columns,
                "total_rows": len(df)
            })
            return report, metrics

        missing_pk_rows = []
        for idx, row in df[primary_keys].iterrows():
            if any(_is_blank_value(row[pk]) for pk in primary_keys):
                missing_pk_rows.append(idx)

        dup_mask = df.duplicated(subset=primary_keys, keep=False)
        duplicate_groups = []
        if dup_mask.any():
            dup_df = df[dup_mask]
            duplicate_groups = [group.index.tolist() for _, group in dup_df.groupby(primary_keys, dropna=False)]

        report = {
            "missing_columns": [],
            "missing_pk_rows": sorted(missing_pk_rows),
            "duplicate_groups": duplicate_groups
        }
        _append_validation_log(context.get("output_dir"), {
            "event_type": "detect",
            "validator": "PrimaryKeyValidationValidator",
            "primary_keys": primary_keys,
            "missing_pk_row_count": len(missing_pk_rows),
            "duplicate_group_count": len(duplicate_groups)
        })

        if not missing_pk_rows and not duplicate_groups:
            return {}, metrics
        return report, metrics

    async def fix(self, df: pd.DataFrame, report: Dict[str, Any], context: Dict[str, Any] = None) -> Tuple[pd.DataFrame, LLMMetrics]:
        metrics = LLMMetrics()
        context = context or {}
        if not report:
            return df, metrics

        if report.get("missing_columns"):
            logger.warning(
                "PrimaryKeyValidationValidator: missing PK columns %s. Returning empty frame.",
                report["missing_columns"]
            )
            _append_validation_log(context.get("output_dir"), {
                "event_type": "fix",
                "validator": "PrimaryKeyValidationValidator",
                "action": "drop_all_rows_missing_pk_columns",
                "missing_columns": report["missing_columns"],
                "rows_before": len(df),
                "rows_after": 0
            })
            return df.iloc[0:0], metrics

        rows_to_drop = set(report.get("missing_pk_rows", []))
        for group in report.get("duplicate_groups", []):
            rows_to_drop.update(group[1:])

        if not rows_to_drop:
            return df, metrics

        cleaned = df.drop(sorted(rows_to_drop))
        logger.info("PrimaryKeyValidationValidator: Dropping %d invalid PK rows.", len(rows_to_drop))
        _append_validation_log(context.get("output_dir"), {
            "event_type": "fix",
            "validator": "PrimaryKeyValidationValidator",
            "rows_dropped": len(rows_to_drop),
            "rows_before": len(df),
            "rows_after": len(cleaned)
        })
        return cleaned, metrics

class CompletenessValidator(BaseValidator):
    """
    Drops rows that are structurally incomplete: missing PKs, missing almost all
    non-key fields, or containing only blank values outside the key.
    """
    async def detect(self, df: pd.DataFrame, context: Dict[str, Any] = None) -> Tuple[List[int], LLMMetrics]:
        metrics = LLMMetrics()
        context = context or {}
        schema = context.get('schema')
        if df.empty or not schema or not schema.columns:
            return [], metrics

        schema_cols = [c.name for c in schema.columns if c.name in df.columns]
        primary_keys = schema.primary_keys if schema.primary_keys else []
        non_key_cols = [c for c in schema_cols if c not in primary_keys]
        invalid_indices = []

        for idx, row in df.iterrows():
            if primary_keys and any(pk not in df.columns or _is_blank_value(row.get(pk)) for pk in primary_keys):
                invalid_indices.append(idx)
                continue

            if non_key_cols:
                filled_non_keys = sum(0 if _is_blank_value(row.get(col)) else 1 for col in non_key_cols)
                if filled_non_keys == 0:
                    invalid_indices.append(idx)
                    continue

                if len(non_key_cols) >= 3 and filled_non_keys < max(1, len(non_key_cols) // 2):
                    invalid_indices.append(idx)

        invalid_indices = sorted(set(invalid_indices))
        _append_validation_log(context.get("output_dir"), {
            "event_type": "detect",
            "validator": "CompletenessValidator",
            "total_rows": len(df),
            "invalid_count": len(invalid_indices)
        })
        return invalid_indices, metrics

    async def fix(self, df: pd.DataFrame, report: List[int], context: Dict[str, Any] = None) -> Tuple[pd.DataFrame, LLMMetrics]:
        metrics = LLMMetrics()
        context = context or {}
        if not report:
            return df, metrics

        cleaned = df.drop(report)
        logger.info("CompletenessValidator: Dropping %d incomplete rows.", len(report))
        _append_validation_log(context.get("output_dir"), {
            "event_type": "fix",
            "validator": "CompletenessValidator",
            "rows_dropped": len(report),
            "rows_before": len(df),
            "rows_after": len(cleaned)
        })
        return cleaned, metrics

class CodeValidator(BaseValidator):
    """
    Gen-T Style Validation: Generate Code -> Execute -> Fix.
    """
    async def detect(self, df: pd.DataFrame, context: Dict[str, Any] = None) -> Tuple[List[Dict], LLMMetrics]:
        """
        Returns list of invalid rows with error messages and metrics.
        Format: [{'index': i, 'row': dict, 'error': str}]
        """
        metrics = LLMMetrics()
        schema = context.get('schema')
        if not schema:
            return [], metrics
            
        # 1. Generate Assertions (if not active cached?)
        # Ideally we generate once per schema. For now, gen every time or pass in context.
        # We'll use the first row to scout.
        try:
            schema_desc = str(schema) # Or formatted string
            # Use columns metadata
            if hasattr(schema, 'columns'):
                schema_desc = ", ".join([f"{c.name} ({c.semantic_type}): {c.description}" for c in schema.columns])
            
            assertions, gen_metrics = await self._generate_assertions(schema_desc, df.iloc[0].to_dict())
            if gen_metrics:
                metrics = metrics + gen_metrics
            logger.info(f"Generated Assertions: {assertions}")
        except Exception as e:
            logger.error(f"Assertion Generation Failed: {e}")
            return [], metrics

        # 2. Execute
        invalid_items = []
        
        # Pre-compile
        try:
            compile(assertions, '<string>', 'exec')
        except:
            return [], metrics

        import re
        import datetime
        data = df.to_dict('records')
        
        for i, row in enumerate(data):
            try:
                local_scope = {'row': row, 're': re, 'datetime': datetime}
                exec(assertions, {}, local_scope)
            except AssertionError as e:
                invalid_items.append({"index": df.index[i], "row": row, "error": str(e)})
            except Exception as e:
                invalid_items.append({"index": df.index[i], "row": row, "error": str(e)})
                
        return invalid_items, metrics

    async def fix(self, df: pd.DataFrame, report: List[Dict], context: Dict[str, Any] = None) -> Tuple[pd.DataFrame, LLMMetrics]:
        """Fixes invalid rows based on error report, processing all errors in batches of 20."""
        metrics = LLMMetrics()
        if not report:
            return df, metrics

        BATCH_SIZE = 20
        all_indices_to_drop = []
        all_fixed_rows = []

        for batch_start in range(0, len(report), BATCH_SIZE):
            batch = report[batch_start : batch_start + BATCH_SIZE]
            error_lines = [
                f"Row {item['index']}: {item['row']} => Error: {item['error']}"
                for item in batch
            ]
            logger.info(f"CodeValidator: Fixing batch {batch_start // BATCH_SIZE + 1} "
                        f"({len(batch)} rows, total errors={len(report)}).")

            prompt = FIX_DATA_PROMPT.format(error_report="\n".join(error_lines))

            try:
                response = await self.llm.agenerate(prompt, temperature=0.0, response_format={"type": "json_object"})
                if response.metrics:
                    metrics = metrics + response.metrics
                content = response.content
                if "```json" in content:
                    content = content.split("```json")[1].split("```")[0]
                data = json.loads(content)

                result_list = []
                if isinstance(data, list):
                    result_list = data
                elif isinstance(data, dict):
                    for k, v in data.items():
                        if isinstance(v, list):
                            result_list = v
                            break

                schema = context.get('schema') if context else None
                target_columns = [c.name for c in schema.columns] if schema and getattr(schema, "columns", None) else list(df.columns)
                result_list = _align_rows_to_target_columns(result_list, target_columns)

                all_indices_to_drop.extend([item['index'] for item in batch])
                all_fixed_rows.extend(result_list)

            except Exception as e:
                logger.error(f"CodeValidator Fix Failed for batch starting at {batch_start}: {e}")
                # Drop the invalid rows even if the fix call failed (soft fail)
                all_indices_to_drop.extend([item['index'] for item in batch])

        if all_indices_to_drop:
            df_dropped = df.drop(all_indices_to_drop)
            if all_fixed_rows:
                df_fixed = pd.DataFrame(all_fixed_rows)
                return pd.concat([df_dropped, df_fixed], ignore_index=True), metrics
            return df_dropped, metrics

        return df, metrics

    async def _generate_assertions(self, schema_desc: str, sample_row: Dict) -> Tuple[str, Optional[LLMMetrics]]:
        prompt = GEN_ASSERTION_PROMPT.format(
            schema_desc=schema_desc,
            sample_row=json.dumps(sample_row, default=str)[:500]
        )
        response = await self.llm.agenerate(prompt, temperature=0.0)
        code = response.content
        metrics = response.metrics
        if "```python" in code:
            code = code.split("```python")[1].split("```")[0]
        elif "```" in code:
            code = code.split("```")[1].split("```")[0]
        return code, metrics


class EmbeddingValidator(BaseValidator):
    """
    Outlier Detection using Embeddings (Sentence-Transformers).
    """
    def __init__(self, llm_client=None, model_name="all-MiniLM-L6-v2"):
        super().__init__(llm_client)
        self.model_name = model_name
        self.encoder = None
        self.iso_forest = None
        
    def _lazy_load(self):
        if not self.encoder:
            try:
                from sentence_transformers import SentenceTransformer
                from sklearn.ensemble import IsolationForest
                import numpy as np
                self.encoder = SentenceTransformer(self.model_name)
                self.iso_forest = IsolationForest(contamination=0.05, random_state=42) # Assume 5% outliers
            except ImportError:
                logger.warning("EmbeddingValidator: sentence-transformers or scikit-learn not installed. Skipping.")
                self.encoder = False

    async def detect(self, df: pd.DataFrame, context: Dict[str, Any] = None) -> Tuple[List[int], LLMMetrics]:
        self._lazy_load()
        metrics = LLMMetrics()
        if not self.encoder or df.empty:
            return [], metrics
            
        # 1. Serialize rows to text
        # We want to catch rows that are semantically distinct from the majority.
        # e.g. "Paris, France" vs "Pikachu, Pokemon" in a Cities list.
        text_rows = df.astype(str).agg(' '.join, axis=1).tolist()
        
        # 2. Encode
        embeddings = self.encoder.encode(text_rows)
        
        # 3. Detect Outliers (Isolation Forest)
        # -1 for outliers, 1 for inliers
        preds = self.iso_forest.fit_predict(embeddings)
        
        outlier_indices = []
        for i, pred in enumerate(preds):
            if pred == -1:
                idx = df.index[i]
                outlier_indices.append(idx)
                
        if outlier_indices:
            logger.info(f"EmbeddingValidator: Detected {len(outlier_indices)} outliers: {outlier_indices}")
            
        return outlier_indices, metrics

    async def fix(self, df: pd.DataFrame, report: List[int], context: Dict[str, Any] = None) -> Tuple[pd.DataFrame, LLMMetrics]:
        """
        Asks LLM to verify if the outliers are actually invalid.
        """
        metrics = LLMMetrics()
        if not report:
            return df, metrics
            
        # Optimization: If too many outliers, maybe just drop? 
        # But maybe the model is wrong. Let's ask LLM for a sample.
        
        # TODO: Batch verification
        # For V1, we just drop them? No, that's dangerous.
        # Let's verify.
        
        outlier_rows = df.loc[report].to_dict('records')
        logger.info(f"EmbeddingValidator: Verifying {len(outlier_rows)} outliers with LLM.")
        
        # Reuse Chunk Refinement or specific outlier prompt?
        # Let's make a quick "Is this valid?" check.
        # Construct a prompt.
        
        valid_indices = []
        
        # Simple Logic: Check Top 5. If they are all valid, assume the Forest is hallucinating and keep all.
        # If they are invalid, drop all? 
        # Safer: Verified Drop.
        
        # Let's perform a bulk check
        prompt_template = """Task: Filter invalid rows from an extraction result.

Query: "{query}"
{schema_context}

Candidate Rows (may contain errors, hallucinations, or off-topic entries):
{rows}

For each row, mark it INVALID if ANY of:
- The row is clearly about a different topic than the query
- A key field is hallucinated (e.g. a name that doesn't exist for this query)
- The row violates a hard constraint (e.g. wrong year, wrong country)
- The row is a duplicate of another in the list (keep the first occurrence)

Mark it VALID if the row is plausibly a real extraction for this query.
When uncertain, default to VALID (false positives are safer than false negatives).

Return JSON: {{ "valid_indices": [0, 2], "invalid_indices": [1] }}
Indices correspond to the list above."""

        # Use real query; fall back to table name only if query is absent.
        query = context.get('query') or (
            f"Extract {context['schema'].table_name}" if context.get('schema') else "Unknown Query"
        )
        schema_context = ""
        if context.get('schema'):
            cols = ", ".join(c.name for c in context['schema'].columns)
            schema_context = f"Table schema: {context['schema'].table_name} ({cols})."
        if context.get('constraints'):
            schema_context += f" Constraints: {context['constraints']}."
            
        # Batching (chunk of 10)
        final_valid_params = []
        
        # Only process actual indices present in df (safety)
        report = [r for r in report if r in df.index]
        
        # If we can't verify easily (no query), we skip dropping to be safe.
        if query == "Unknown Query":
            return df, metrics
            
        batch_size = 10
        import math
        
        rows_to_check = df.loc[report]
        # We need local 0-indexed list for the LLM prompt
        rows_list = rows_to_check.to_dict('records')
        real_indices = rows_to_check.index.tolist()
        
        # We will iterate batches
        confirmed_invalid_indices = []
        
        for i in range(0, len(rows_list), batch_size):
            batch = rows_list[i:i+batch_size]
            batch_real_indices = real_indices[i:i+batch_size]
            
            prompt = prompt_template.format(query=query, schema_context=schema_context, rows=json.dumps(batch, indent=2))
            try:
                response = await self.llm.agenerate(prompt, temperature=0.0, response_format={"type": "json_object"})
                if response.metrics:
                    metrics = metrics + response.metrics
                content = response.content
                if "```json" in content:
                    content = content.split("```json")[1].split("```")[0]
                data = json.loads(content)
                
                # invalid_indices refers to the index in the 'batch' list (0..batch_size-1)
                batch_invalid_local = data.get("invalid_indices", [])
                
                for local_idx in batch_invalid_local:
                    if 0 <= local_idx < len(batch_real_indices):
                        confirmed_invalid_indices.append(batch_real_indices[local_idx])
                        
            except Exception as e:
                logger.warning(f"Embedding Verify Failed: {e}")
                
        # Drop confirmed invalid
        if confirmed_invalid_indices:
            logger.info(f"EmbeddingValidator: Dropping {len(confirmed_invalid_indices)} confirmed invalid rows.")
            return df.drop(confirmed_invalid_indices), metrics
            
        return df, metrics


class FormatNormalizationValidator(BaseValidator):
    """
    Normalizes column formats (e.g., "Generation 1" -> 1).
    detect() identifies which columns need normalization; fix() calls LLM only for those.
    """
    def __init__(self, llm_client=None, model_name="all-MiniLM-L6-v2"):
        super().__init__(llm_client)
        from src.prompts import FORMAT_NORMALIZATION_PROMPT
        self.prompt_template = FORMAT_NORMALIZATION_PROMPT

    async def detect(self, df: pd.DataFrame, context: Dict[str, Any] = None) -> Tuple[List[tuple], LLMMetrics]:
        """
        Returns a list of (col_name, target_type, formatting, unique_values) tuples
        for every column whose values don't match the declared type/format.
        An empty list means nothing needs normalization and fix() will be skipped entirely.
        """
        metrics = LLMMetrics()
        schema = context.get('schema') if context else None
        if not schema or df.empty:
            return [], metrics

        needs_normalization = []
        for col in schema.columns:
            if col.name not in df.columns:
                continue

            target_type = col.data_type.lower() if col.data_type else "string"
            formatting = col.formatting

            # Plain text columns with no special format never need normalization
            if target_type in ["string", "text", "str"] and not formatting:
                continue

            unique_values = df[col.name].dropna().unique().tolist()
            if not unique_values:
                continue

            needs_fix = False
            if target_type in ["int", "integer"]:
                for v in unique_values:
                    s = str(v)
                    if not s.replace('.', '', 1).isdigit() and not (s.startswith('-') and s[1:].isdigit()):
                        needs_fix = True
                        break
            elif formatting:
                needs_fix = True  # Formatted columns always get checked

            if needs_fix:
                needs_normalization.append((col.name, target_type, formatting, unique_values[:50]))

        if needs_normalization:
            logger.info(f"FormatNormalizationValidator: {len(needs_normalization)} column(s) need normalization: "
                        f"{[t[0] for t in needs_normalization]}")
        return needs_normalization, metrics

    async def fix(self, df: pd.DataFrame, report: List[tuple], context: Dict[str, Any] = None) -> Tuple[pd.DataFrame, LLMMetrics]:
        """
        Calls LLM once per column in the report to produce a value-correction mapping,
        then applies corrections in-place.
        """
        metrics = LLMMetrics()
        if df.empty or not report:
            return df, metrics

        for (col_name, target_type, formatting, unique_values) in report:
            if col_name not in df.columns:
                continue

            logger.info(f"FormatValidator: Normalizing '{col_name}' for type '{target_type}'...")
            prompt = self.prompt_template.format(
                column=col_name,
                dtype=target_type,
                formatting=formatting or "Standard",
                values=json.dumps(unique_values, default=str)
            )

            try:
                response = await self.llm.agenerate(prompt, temperature=0.0, response_format={"type": "json_object"})
                if response.metrics:
                    metrics = metrics + response.metrics
                content = response.content
                if "```json" in content:
                    content = content.split("```json")[1].split("```")[0]
                data = json.loads(content)
                corrections = data.get("corrections", {})

                if corrections:
                    df[col_name] = df[col_name].map(lambda x: corrections.get(str(x), x))
                    if target_type in ["int", "integer"]:
                        try:
                            df[col_name] = pd.to_numeric(df[col_name], errors='ignore')
                        except:
                            pass

            except Exception as e:
                logger.warning(f"FormatNormalization failed for {col_name}: {e}")

        return df, metrics


class EntityResolutionValidator(BaseValidator):
    """
    Resolves duplicates based on Entity Name/Identity, overriding strict PK differences.
    Fixes hallucinations where same entity appears with different IDs.
    """
    def __init__(self, llm_client=None):
        super().__init__(llm_client)
        from src.prompts import ENTITY_RESOLUTION_DECISION_PROMPT
        self.prompt_template = ENTITY_RESOLUTION_DECISION_PROMPT

    def _find_entity_identity_column(self, schema, df: pd.DataFrame) -> Optional[str]:
        preferred_exact = {"name", "title", "entity", "pokemon"}
        preferred_keywords = [
            "name", "title", "entity", "author", "artist", "composer", "country",
            "team", "song", "game", "book", "work", "city", "state", "console"
        ]

        scored_candidates = []
        for col in schema.columns:
            if col.name not in df.columns:
                continue

            name_lower = col.name.lower()
            desc_lower = (col.description or "").lower()
            sem_type = (col.semantic_type or "").lower()
            data_type = (col.data_type or "").lower()

            if sem_type not in {"entity", "text", "categorical"} and data_type not in {"str", "string", "text"}:
                continue

            score = 0
            if name_lower in preferred_exact:
                score += 100
            if any(k in name_lower for k in preferred_keywords):
                score += 40
            if any(k in desc_lower for k in preferred_keywords):
                score += 20
            if sem_type == "entity":
                score += 10

            scored_candidates.append((score, col.name))

        if not scored_candidates:
            return None

        scored_candidates.sort(reverse=True)
        return scored_candidates[0][1]

    async def detect(self, df: pd.DataFrame, context: Dict[str, Any] = None) -> Tuple[List[int], LLMMetrics]:
        # Returns indices of DUPLICATE entities that need resolution.
        # We group by specific "Entity Name" columns.
        
        metrics = LLMMetrics()
        logger.info("EntityResolution: Starting Detection...")
        logger.info(f"EntityResolution: DF Shape: {df.shape}")
        
        schema = context.get('schema')
        if not schema:
            logger.warning("EntityResolution: No schema found in context.")
            return [], metrics
        primary_keys = schema.primary_keys if schema.primary_keys else []
            
        schema_cols = [c.name for c in schema.columns]
        logger.info(f"EntityResolution: Schema Columns: {schema_cols}")
        name_col = self._find_entity_identity_column(schema, df)
        
        if not name_col or name_col not in df.columns:
            logger.warning(f"EntityResolution: Name column not found. target='{name_col}'. DF DF Cols: {list(df.columns)}")
            return [], metrics

        # If the inferred identity column is already part of the PK, duplicates in this
        # column are often legitimate rows for a composite key (e.g. Game across Consoles).
        if name_col in primary_keys:
            logger.info(
                "EntityResolution: Skipping because identity column '%s' is part of primary key %s.",
                name_col,
                primary_keys
            )
            return [], metrics
            
        # 2. Find duplicates in Name
        # We look for Name duplicates where other fields (like ID) differ.
        dup_mask = df.duplicated(subset=[name_col], keep=False)
        num_dups = dup_mask.sum()
        logger.info(f"EntityResolution: Duplicates Mask Sum: {num_dups}")
        
        if not any(dup_mask):
            return [], metrics
            
        candidates = df[dup_mask].index.tolist()
        logger.info(f"EntityResolution: Found {len(candidates)} duplicates for resolution in column '{name_col}'.")
        return candidates, metrics

    async def fix(self, df: pd.DataFrame, report: List[int], context: Dict[str, Any] = None) -> Tuple[pd.DataFrame, LLMMetrics]:
        metrics = LLMMetrics()
        if not report:
            return df, metrics
            
        # Determine Name col again
        schema = context.get('schema')
        name_col = self._find_entity_identity_column(schema, df)
        if not name_col:
            return df, metrics
                
        # Group duplicates
        subset = df.loc[report]
        groups = subset.groupby(name_col)
        
        indices_to_drop = []
        
        for name, group in groups:
            if len(group) < 2:
                continue
                
            # If they are EXACT duplicates in all columns, standard dedup (pandas) handles it.
            # We assume DataValidator calls DeduplicationValidator first or after.
            # But let's check if rows are identical.
            # if group.iloc[0].equals(group.iloc[1]): ...
            
            # If they differ (e.g. ID=51 vs ID=101), we need LLM or Heuristic.
            
            # Heuristic 1: If one ID matches constraints and other doesn't?
            # Complexity is high. Let's simple ask LLM "Which one is right?"
            
            # Prepare conflict string
            conflicts = group.to_dict(orient='records')
            
            # Optimization: If many duplicates, this is slow. 
            # Limit scope? 
            
            # Let's try simple heuristic first: Majority vote? 
            # Or just keep the one with lowest ID? (Data bias)
            
            # Let's use LLM as designed for accuracy.
            prompt = self.prompt_template.format(
                entity_name=name,
                conflicts=json.dumps(conflicts, indent=2, default=str),
                constraints=context.get('constraints', {}),
                source=context.get('source', 'Wikipedia')
            )
            
            try:
                # We expect the LLM to return the INDEX of the keeper? 
                # Or we can just ask for "keep_index": int
                # But indices are DF indices.
                
                # Let's map internal index 0..N
                labeled_conflicts = [{**row, "_local_idx": i} for i, row in enumerate(conflicts)]
                
                response = await self.llm.agenerate(prompt, temperature=0.0, response_format={"type": "json_object"})
                if response.metrics:
                    metrics = metrics + response.metrics
                content = response.content
                if "```json" in content:
                    content = content.split("```json")[1].split("```")[0]
                
                try:
                    data = json.loads(content)
                    # Expecting: {"keep_index": 0} or {"keep_indices": [0]} relative to the conflict list
                    keep = data.get("keep_index", 0)
                    
                    # Convert local index to DF index
                    # conflict list was created from iterating 'groups', but we need to map back to original indices.
                    # The 'conflicts' dict list should have preserved order relative to 'group'.
                    # Let's verify 'group' index access.
                    
                    # group is a DataFrame slice.
                    # If LLM says "keep index 0", it means group.iloc[0].
                    
                    df_keep_idx = group.index[keep]
                    
                    # Mark others as drop
                    for idx in group.index:
                        if idx != df_keep_idx:
                            indices_to_drop.append(idx)
                            
                except Exception as parse_err:
                    logger.warning(f"EntityResolution: Parse failed '{parse_err}' for {name}. Defaulting to first.")
                    # Fallback: Keep First
                    indices_to_drop.extend(group.index[1:].tolist())

            except Exception as e:
                logger.error(f"EntityResolution: Fix failed for {name}: {e}")
                # On error, keep first
                indices_to_drop.extend(group.index[1:].tolist())

        if indices_to_drop:
            logger.info(f"EntityResolution: Dropping {len(indices_to_drop)} resolved duplicates.")
            return df.drop(indices_to_drop), metrics

        return df, metrics
