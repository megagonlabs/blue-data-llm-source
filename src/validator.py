import logging
import pandas as pd
import json
import asyncio
from typing import List, Dict, Any, Optional
from abc import ABC, abstractmethod
import re

from src.prompts import (
    CHUNK_REFINEMENT_PROMPT,
    ENTITY_RESOLUTION_PROMPT,
    GEN_ASSERTION_PROMPT,
    FIX_DATA_PROMPT,
    VALUE_VALIDATION_PROMPT
)

logger = logging.getLogger(__name__)

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
    async def fix(self, df: pd.DataFrame, report: Any, context: Dict[str, Any] = None) -> pd.DataFrame:
        """
        Fixes the issues identified in the report.
        Returns the cleaned DataFrame.
        """
        pass

    async def run(self, df: pd.DataFrame, context: Dict[str, Any] = None) -> pd.DataFrame:
        """
        Orchestrates Detect -> Fix.
        """
        if df.empty:
            return df
            
        report = await self.detect(df, context)
        # If report indicates no errors (implementation dependent check), return early
        if not report: 
            return df
            
        return await self.fix(df, report, context)


class GlobalValidator:
    """
    Unified Orchestrator for Validation Pipelines.
    """
    def __init__(self, llm_client, strategies: List[str] = None):
        self.llm = llm_client
        self.strategies = strategies or []
        self.validators = self._initialize_validators()

    def _initialize_validators(self) -> List[BaseValidator]:
        params = {"llm_client": self.llm}
        validator_map = {
            "ChunkConstraints": ChunkValidator(**params),
            "Deduplication": DeduplicationValidator(**params),
            "Code": CodeValidator(**params),
            "Embedding": EmbeddingValidator(**params),
            "FormatNormalization": FormatNormalizationValidator(**params),
            "EntityResolution": EntityResolutionValidator(**params),
            "PrimaryKeyValidation": DeduplicationValidator(**params), # Alias for now
            "Completeness": ChunkValidator(**params) # Placeholder, logic in Engine
        }
        
        active = []
        for s in self.strategies:
            if s in validator_map:
                active.append(validator_map[s])
            else:
                logger.warning(f"Unknown validator strategy: {s}")
        return active

    async def run(self, df: pd.DataFrame, context: Dict[str, Any] = None) -> pd.DataFrame:
        """
        Sequentially runs all configured validators.
        """
        context = context or {}
        for validator in self.validators:
            try:
                logger.info(f"Running Validator: {validator.__class__.__name__}")
                df = await validator.run(df, context)
            except Exception as e:
                logger.error(f"Validator {validator.__class__.__name__} failed: {e}")
        return df


# --- Concrete Strategies ---

class ChunkValidator(BaseValidator):
    """
    Enforces partition constraints (e.g. Year=2020) and General Data Quality.
    Uses LLM to validate values against constraints, types, and schema.
    """
    async def detect(self, df: pd.DataFrame, context: Dict[str, Any] = None) -> List[int]:
        """Returns list of indices violating constraints using LLM-based value validation."""
        constraints = context.get('constraints', {})
        
        # Determine columns to validate (Constraints + PKs)
        columns_to_validate = list(constraints.keys())
        
        schema = context.get('schema')
        primary_keys = schema.primary_keys if schema else []
        for pk in primary_keys:
            if pk not in columns_to_validate and pk in df.columns:
                columns_to_validate.append(pk)
        
        if not columns_to_validate:
            return []
            
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
            
        return unique_invalid

    async def fix(self, df: pd.DataFrame, report: List[int], context: Dict[str, Any] = None) -> pd.DataFrame:
        """Attempts to refine violating rows via LLM, or drops them."""
        constraints = context.get('constraints', {})
        try:
            invalid_rows = df.loc[report].to_dict('records')
        except:
            invalid_rows = []

        if not self.llm:
            return df.drop(report)

        logger.info(f"ChunkValidator: Attempting to refine {len(report)} rows.")
        
        prompt = CHUNK_REFINEMENT_PROMPT.format(
            constraints=json.dumps(constraints),
            rows=json.dumps(invalid_rows, indent=2)
        )
        
        try:
            response = await self.llm.agenerate(prompt, temperature=0.0, response_format={"type": "json_object"})
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
            
            # Reconstruct DataFrame
            df_dropped = df.drop(report)
            if fixed_rows:
                df_fixed = pd.DataFrame(fixed_rows)
                final_df = pd.concat([df_dropped, df_fixed], ignore_index=True)
            else:
                final_df = df_dropped

            # Log Event
            if context and "output_dir" in context:
                self._log_event(context["output_dir"], "fix", {
                    "validator": "ChunkValidator",
                    "attempted_fix_count": len(report),
                    "successfully_fixed_count": len(fixed_rows),
                    "dropped_count": len(report) - len(fixed_rows),
                    "fixed_rows": fixed_rows,
                    "prompt": prompt,
                    "llm_output": content
                })

            return final_df
            
        except Exception as e:
            logger.warning(f"ChunkValidator Fix failed: {e}")
            return df.drop(report)

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

    async def detect(self, df: pd.DataFrame, context: Dict[str, Any] = None) -> Any:
        # Returns:
        # - List[int] (indices to drop) if strategy="first"
        # - List[List[int]] (groups of duplicates) if strategy="llm-based"
        
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
            return sorted(list(set(invalid_indices)))
            
        elif self.strategy == "llm-based":
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
                    logger.info(f"DeduplicationValidator (llm-based): Found {len(duplicate_groups)} duplicate groups based on PK.")
            else:
                # Fallback to exact match? Exact match doesn't need LLM usually.
                # Only strictly identical rows.
                # If rows are strictly identical, LLM can't distinguish.
                # So we fallback to 'first' logic for non-PK duplicates if they are identical content.
                # But if we want to be safe, let's just return nothing or use 'first' logic.
                pass
            return duplicate_groups
            
        return []

    async def fix(self, df: pd.DataFrame, report: Any, context: Dict[str, Any] = None) -> pd.DataFrame:
        if not report:
            return df
            
        if self.strategy == "first":
            # Report is List[int]
            logger.info(f"DeduplicationValidator: Dropping {len(report)} rows (Strategy: first).")
            return df.drop(report)
            
        elif self.strategy == "llm-based":
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
                logger.info(f"DeduplicationValidator: Dropping {len(indices_to_drop)} rows (Strategy: llm-based).")
                return df.drop(indices_to_drop)
                
        return df


class CodeValidator(BaseValidator):
    """
    Gen-T Style Validation: Generate Code -> Execute -> Fix.
    """
    async def detect(self, df: pd.DataFrame, context: Dict[str, Any] = None) -> List[Dict]:
        """
        Returns list of invalid rows with error messages.
        Format: [{'index': i, 'row': dict, 'error': str}]
        """
        schema = context.get('schema')
        if not schema:
            return []
            
        # 1. Generate Assertions (if not active cached?)
        # Ideally we generate once per schema. For now, gen every time or pass in context.
        # We'll use the first row to scout.
        try:
            schema_desc = str(schema) # Or formatted string
            # Use columns metadata
            if hasattr(schema, 'columns'):
                schema_desc = ", ".join([f"{c.name} ({c.semantic_type}): {c.description}" for c in schema.columns])
            
            assertions = await self._generate_assertions(schema_desc, df.iloc[0].to_dict())
            logger.info(f"Generated Assertions: {assertions}")
        except Exception as e:
            logger.error(f"Assertion Generation Failed: {e}")
            return []

        # 2. Execute
        invalid_items = []
        
        # Pre-compile
        try:
            compile(assertions, '<string>', 'exec')
        except:
            return []

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
                
        return invalid_items

    async def fix(self, df: pd.DataFrame, report: List[Dict], context: Dict[str, Any] = None) -> pd.DataFrame:
        """Fixes invalid rows based on error report."""
        if not report:
            return df
            
        # Limit batch size
        target_errors = report[:20] 
        error_lines = [f"Row {item['index']}: {item['row']} => Error: {item['error']}" for item in target_errors]
        
        logger.info(f"CodeValidator: Attempting to fix {len(target_errors)} rows.")
        
        prompt = FIX_DATA_PROMPT.format(error_report="\n".join(error_lines))
        
        try:
            response = await self.llm.agenerate(prompt, temperature=0.0, response_format={"type": "json_object"})
            content = response.content
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0]
            data = json.loads(content)
            
            # robust extraction
            result_list = []
            if isinstance(data, list): result_list = data
            elif isinstance(data, dict):
                 for k, v in data.items():
                    if isinstance(v, list):
                        result_list = v
                        break
            
            if not result_list:
                return df # Fix failed, keep original? OR drop? Let's keep original for now (Soft Fail)
                
            # Update DataFrame
            # We need to map back to indices. But the fix might not return indices.
            # Assuming the LLM returns fixed rows in order or we replace by matching content? 
            # Simple approach: Drop invalid indices, Append fixed.
            
            indices_to_drop = [item['index'] for item in target_errors]
            df_dropped = df.drop(indices_to_drop)
            
            df_fixed = pd.DataFrame(result_list)
            return pd.concat([df_dropped, df_fixed], ignore_index=True)
            
        except Exception as e:
            logger.error(f"CodeValidator Fix Failed: {e}")
            return df

    async def _generate_assertions(self, schema_desc: str, sample_row: Dict) -> str:
        prompt = GEN_ASSERTION_PROMPT.format(
            schema_desc=schema_desc,
            sample_row=json.dumps(sample_row, default=str)[:500]
        )
        response = await self.llm.agenerate(prompt, temperature=0.0)
        code = response.content
        if "```python" in code:
            code = code.split("```python")[1].split("```")[0]
        elif "```" in code:
            code = code.split("```")[1].split("```")[0]
        return code


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

    async def detect(self, df: pd.DataFrame, context: Dict[str, Any] = None) -> List[int]:
        self._lazy_load()
        if not self.encoder or df.empty:
            return []
            
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
            
        return outlier_indices

    async def fix(self, df: pd.DataFrame, report: List[int], context: Dict[str, Any] = None) -> pd.DataFrame:
        """
        Asks LLM to verify if the outliers are actually invalid.
        """
        if not report:
            return df
            
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
        prompt_template = """
        I have a list of rows extracted for the query: "{query}".
        I suspect these might be outliers or irrelevant.
        
        Rows:
        {rows}
        
        For each row, determine if it is a VALID extraction for the query.
        Return JSON: {{ "valid_indices": [0, 2], "invalid_indices": [1] }}
        Indices correspond to the list above.
        """
        
        # We need the query. It might be in context.
        # If not, we can guess or use table name?
        query = context.get('query', "Unknown Query") 
        if 'schema' in context and context['schema']:
            query = f"Extract {context['schema'].table_name}"
            
        # Batching (chunk of 10)
        final_valid_params = []
        
        # Only process actual indices present in df (safety)
        report = [r for r in report if r in df.index]
        
        # If we can't verify easily (no query), we skip dropping to be safe.
        if query == "Unknown Query":
            return df
            
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
            
            prompt = prompt_template.format(query=query, rows=json.dumps(batch, indent=2))
            try:
                response = await self.llm.agenerate(prompt, temperature=0.0, response_format={"type": "json_object"})
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
            return df.drop(confirmed_invalid_indices)
            
        return df


class FormatNormalizationValidator(BaseValidator):
    """
    Normalizes column formats (e.g., "Generation 1" -> 1).
    """
    def __init__(self, llm_client=None, model_name="all-MiniLM-L6-v2"):
        super().__init__(llm_client)
        from src.prompts import FORMAT_NORMALIZATION_PROMPT
        self.prompt_template = FORMAT_NORMALIZATION_PROMPT

    async def detect(self, df: pd.DataFrame, context: Dict[str, Any] = None) -> List[int]:
        # This validator modifies in-place during fix(), so detect() is a pass-through or identification step.
        # We identify columns that need normalization.
        # For simplicity, we just return a "flag" in the report to trigger fix.
        return [1] if not df.empty else []

    async def fix(self, df: pd.DataFrame, report: List[int], context: Dict[str, Any] = None) -> pd.DataFrame:
        if df.empty or not report:
            return df
            
        schema = context.get('schema')
        if not schema:
            return df

        # Iterate over columns with specific types
        for col in schema.columns:
            if col.name not in df.columns:
                continue
                
            # Heuristic: Check if column content matches type
            # If INT but has strings -> Normalize
            # If DATE but has raw text -> Normalize
            
            target_type = col.data_type.lower() if col.data_type else "string"
            formatting = col.formatting
            
            # Skip if explicitly string/text and no specific format
            if target_type in ["string", "text", "str"] and not formatting:
                continue
                
            # Sample values
            unique_values = df[col.name].dropna().unique().tolist()
            if not unique_values:
                continue
                
            # Quick check: Are they already valid?
            # e.g. if target is INT, are all values integers?
            needs_fix = False
            if target_type in ["int", "integer"]:
                try:
                    # check if any value is not convertible to clean int or has extra text
                    # "5" is fine. "Gen 5" is not. 
                    # We look for non-digit chars (ignoring dot for float-int)
                    for v in unique_values:
                        s = str(v)
                        if not s.replace('.', '', 1).isdigit() and not (s.startswith('-') and s[1:].isdigit()):
                            needs_fix = True
                            break
                except:
                    needs_fix = True
            elif formatting:
                needs_fix = True # Always check formatted columns
                
            if needs_fix:
                logger.info(f"FormatValidator: Normalizing column '{col.name}' for type '{target_type}'...")
                
                # LLM Call
                prompt = self.prompt_template.format(
                    column=col.name,
                    dtype=target_type,
                    formatting=formatting or "Standard",
                    values=json.dumps(unique_values[:50], default=str) # Limit samples
                )
                
                try:
                    response = await self.llm.agenerate(prompt, temperature=0.0, response_format={"type": "json_object"})
                    content = response.content
                    if "```json" in content:
                        content = content.split("```json")[1].split("```")[0]
                    data = json.loads(content)
                    corrections = data.get("corrections", {})
                    
                    if corrections:
                        # Apply mapping
                        # Use map with fallback to original
                        df[col.name] = df[col.name].map(lambda x: corrections.get(str(x), x))
                        
                        # Post-process: Attempt cast
                        if target_type in ["int", "integer"]:
                            try:
                                df[col.name] = pd.to_numeric(df[col.name], errors='ignore')
                            except:
                                pass
                                
                except Exception as e:
                    logger.warning(f"FormatNormalization failed for {col.name}: {e}")
                    
        return df


class EntityResolutionValidator(BaseValidator):
    """
    Resolves duplicates based on Entity Name/Identity, overriding strict PK differences.
    Fixes hallucinations where same entity appears with different IDs.
    """
    def __init__(self, llm_client=None):
        super().__init__(llm_client)
        from src.prompts import ENTITY_RESOLUTION_DECISION_PROMPT
        self.prompt_template = ENTITY_RESOLUTION_DECISION_PROMPT

    async def detect(self, df: pd.DataFrame, context: Dict[str, Any] = None) -> List[int]:
        # Returns indices of DUPLICATE entities that need resolution.
        # We group by specific "Entity Name" columns.
        
        logger.info("EntityResolution: Starting Detection...")
        logger.info(f"EntityResolution: DF Shape: {df.shape}")
        
        schema = context.get('schema')
        if not schema:
            logger.warning("EntityResolution: No schema found in context.")
            return []
            
        # 1. Identify "Name" / "Title" column
        name_col = None
        schema_cols = [c.name for c in schema.columns]
        logger.info(f"EntityResolution: Schema Columns: {schema_cols}")
        
        for col in schema.columns:
            if col.name.lower() in ["name", "title", "entity", "pokemon"]:
                name_col = col.name
                break
        
        if not name_col or name_col not in df.columns:
            logger.warning(f"EntityResolution: Name column not found. target='{name_col}'. DF Cols: {list(df.columns)}")
            return []
            
        # 2. Find duplicates in Name
        # We look for Name duplicates where other fields (like ID) differ.
        dup_mask = df.duplicated(subset=[name_col], keep=False)
        num_dups = dup_mask.sum()
        logger.info(f"EntityResolution: Duplicates Mask Sum: {num_dups}")
        
        if not any(dup_mask):
            return []
            
        candidates = df[dup_mask].index.tolist()
        logger.info(f"EntityResolution: Found {len(candidates)} duplicates for resolution in column '{name_col}'.")
        return candidates

    async def fix(self, df: pd.DataFrame, report: List[int], context: Dict[str, Any] = None) -> pd.DataFrame:
        if not report:
            return df
            
        # Determine Name col again
        schema = context.get('schema')
        name_col = None
        for col in schema.columns:
            if col.name.lower() in ["name", "title", "entity", "pokemon"]:
                name_col = col.name
                break
                
        # Group duplicates
        subset = df.loc[report]
        groups = subset.groupby(name_col)
        
        indices_to_drop = []
        
        for name, group in groups:
            if len(group) < 2:
                continue
                
            # If they are EXACT duplicates in all columns, standard dedup (pandas) handles it.
            # We assume GlobalValidator calls DeduplicationValidator first or after.
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
            return df.drop(indices_to_drop)
            
        return df
