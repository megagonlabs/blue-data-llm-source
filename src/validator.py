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
    FIX_DATA_PROMPT
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
            "Embedding": EmbeddingValidator(**params)
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
                # logger.info(f"Running Validator: {validator.__class__.__name__}")
                df = await validator.run(df, context)
            except Exception as e:
                logger.error(f"Validator {validator.__class__.__name__} failed: {e}")
        return df


# --- Concrete Strategies ---

class ChunkValidator(BaseValidator):
    """
    Enforces partition constraints (e.g. Year=2020).
    """
    async def detect(self, df: pd.DataFrame, context: Dict[str, Any] = None) -> List[int]:
        """Returns list of indices violating constraints."""
        constraints = context.get('constraints', {})
        if not constraints:
            return []
            
        invalid_indices = []
        for col, val in constraints.items():
            if col in df.columns:
                val_str = str(val).lower()
                
                def check_match(x):
                    x_str = str(x).lower()
                    # 1. Alphabetic Range Check (A-D)
                    alpha_range = re.match(r'^([a-z])-([a-z])$', val_str.strip())
                    if alpha_range:
                        start_char, end_char = alpha_range.groups()
                        if x_str:
                            first_char = x_str[0].lower()
                            return start_char <= first_char <= end_char

                    # 2. Numerical Range Check (100-200, 0.5-1.0)
                    # Supports integers and floats
                    num_range = re.match(r'^(\d+(?:\.\d+)?)-(\d+(?:\.\d+)?)$', val_str.strip())
                    if num_range:
                        try:
                            min_val, max_val = float(num_range.group(1)), float(num_range.group(2))
                            # Try to convert x to float
                            x_val = float(x)
                            return min_val <= x_val <= max_val
                        except (ValueError, TypeError):
                            # If x is not a number, it fails the range check? 
                            # Or strict fail? Let's assume fail.
                            pass

                    # 3. Exact Match (Case Insensitive)
                    return x_str == val_str

                matches = df[col].apply(check_match)
                failed = df[~matches].index.tolist()
                invalid_indices.extend(failed)
        
        unique_invalid = sorted(list(set(invalid_indices)))
        
        if context and "output_dir" in context:
            # [LOGGING] Log actual content of invalid rows
            try:
                invalid_rows_data = df.iloc[unique_invalid].to_dict(orient='records')
            except Exception as e:
                invalid_rows_data = str(e)

            self._log_event(context["output_dir"], "detect", {
                "validator": "ChunkValidator",
                "constraints": constraints,
                "total_rows": len(df),
                "invalid_count": len(unique_invalid),
                "invalid_indices": unique_invalid,
                "invalid_rows": invalid_rows_data  # <--- NEW: Actual Data
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
            
            # [LOGGING] Save LLM I/O for Validation
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

            # [LOGGING]
            if context and "output_dir" in context:
                self._log_event(context["output_dir"], "fix", {
                    "validator": "ChunkValidator",
                    "attempted_fix_count": len(report),
                    "successfully_fixed_count": len(fixed_rows),
                    "dropped_count": len(report) - len(fixed_rows),
                    "fixed_rows": fixed_rows  # <--- NEW: Actual Data
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
    """
    Semantic Deduplication using Entity Resolution.
    """
    async def detect(self, df: pd.DataFrame, context: Dict[str, Any] = None) -> List[List[int]]:
        """
        Returns list of GROUPS of indices that are potential duplicates.
        Logic: Group by content columns -> If size > 1 -> Candidate.
        """
        # Exact Dedupe first (implicit fix for exact matches)
        # But detect() expects to return report. 
        # For simplicity, we DO structural cleanup here or assume it's done. 
        # Let's check for SEMANTIC duplicates.
        
        primary_keys = context.get('primary_keys', [])
        
        # Identify Content Columns
        content_cols = []
        name_candidates = ['Name', 'Entity', 'Title', 'CountryName', 'CityName', 'CommonName', 'ScientificName', 'Symbol', 'PokemonName']
        for col in df.columns:
            if col in name_candidates:
                content_cols.append(col)
        
        if not content_cols:
            content_cols = [c for c in df.columns if not c.lower().endswith('id')]
            
        if not content_cols:
            return []

        # Find duplicates based on exact content match but potentially different IDs
        # or we just group by content cols.
        duplicates_groups = []
        duplicated_subset = df.duplicated(subset=content_cols, keep=False)
        if duplicated_subset.any():
            result = df[duplicated_subset].groupby(content_cols).groups
            # result is dict: {val: Index([...])}
            for indices in result.values():
                if len(indices) > 1:
                    duplicates_groups.append(list(indices))
                    
        return duplicates_groups

    async def fix(self, df: pd.DataFrame, report: List[List[int]], context: Dict[str, Any] = None) -> pd.DataFrame:
        """
        Asks LLM to merge groups of duplicates.
        """
        if not report:
            return df
            
        logger.info(f"DeduplicationValidator: Resolving {len(report)} clusters of duplicates.")
        
        # We need to process clusters. Rows to drop are all indices in report.
        all_indices_to_drop = [idx for group in report for idx in group]
        
        resolved_rows = []
        for group_indices in report:
            rows = df.loc[group_indices].to_dict('records')
            
            prompt = ENTITY_RESOLUTION_PROMPT.format(rows=json.dumps(rows, indent=2))
            try:
                response = await self.llm.agenerate(prompt, temperature=0.0, response_format={"type": "json_object"})
                content = response.content
                if "```json" in content:
                    content = content.split("```json")[1].split("```")[0]
                data = json.loads(content)
                merged = data.get("resolved_rows", rows)
                resolved_rows.extend(merged)
            except Exception as e:
                logger.warning(f"Entity Resolution failed for group: {e}")
                resolved_rows.extend(rows) # Fallback keep original

        # Drop original duplicates, append resolved
        df_clean = df.drop(all_indices_to_drop)
        if resolved_rows:
            df_resolved = pd.DataFrame(resolved_rows)
            # Align columns
            df_resolved = df_resolved.reindex(columns=df_clean.columns)
            return pd.concat([df_clean, df_resolved], ignore_index=True)
            
        return df_clean


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
