import asyncio
import json
import logging
from typing import List, Dict, Any, Set, Tuple, Optional
import pandas as pd
from dataclasses import asdict

from src.dataplan import DataPlanRequest, DataTask
from src.schema import Column
from utils.llm_utils import LLMClient, LLMMetrics, sum_metrics
from src.validator import Validator
from src.prompts import (
    ROW_BY_ROW_KEYS_PROMPT,
    ROW_BY_ROW_FETCH_PROMPT
)

logger = logging.getLogger(__name__)

class MapReduceEngine:
    """
    Runtime engine for the Auto-Partitioning framework.
    1. Map: Executes DataTasks (Partition/Leaf nodes) in parallel.
    2. Reduce: Aggregates results into a single DataFrame.
    """
    
    def __init__(self, llm_client: LLMClient, extraction_temperature: float = 0.0):
        self.llm = llm_client
        self.extraction_temperature = extraction_temperature
        self.validator = Validator(llm_client)
        
    async def execute(self, plan: DataPlanRequest, context: dict = None) -> Tuple[pd.DataFrame, LLMMetrics]:
        context = context or {}
        logger.info(f"Starting execution of {len(plan.map_tasks)} map tasks.")
        
        # Artifact Persistence: Save Tasks
        if "output_dir" in context:
            import os
            try:
                # Need to be careful with circular refs in DataTask.parent if using asdict
                # Creating a simplified dict representation for logging
                tasks_log = []
                for t in plan.map_tasks:
                    t_dict = asdict(t)
                    if t.parent: t_dict["parent"] = t.parent.query # Flatten parent
                    tasks_log.append(t_dict)
                    
                tasks_artifact = {
                    "tasks": tasks_log
                }
                with open(os.path.join(context["output_dir"], "tasks.json"), "w") as f:
                    json.dump(tasks_artifact, f, indent=2)
            except Exception as e:
                logger.warning(f"Failed to save tasks artifact: {e}")
        
        # 1. Map Phase
        # 1. Map Phase
        # Concurrency managed by LLMClient internally
        
        async def run_task(task: DataTask) -> Tuple[List[Dict], Optional[LLMMetrics]]:
            return await self._execute_map_task(task, context=context)
                
        tasks = [run_task(t) for t in plan.map_tasks]
        results_and_metrics = await asyncio.gather(*tasks)
        
        results = [r[0] for r in results_and_metrics]
        metrics_list = [r[1] for r in results_and_metrics if r[1]]
        total_metrics = sum_metrics(metrics_list) if metrics_list else LLMMetrics()
        
        # 2. Reduce Phase
        logger.info("Aggregating results...")
        df = self._reduce(results, plan.schema.columns)
        
        # 3. Global Validation
        df = await self.validator.clean_data(df, primary_keys=plan.schema.primary_keys, context=context)
        
        logger.info(f"Execution complete. Generated {len(df)} rows. Total Tokens: {total_metrics.total_tokens}")
        return df, total_metrics
        
    async def _execute_map_task(self, task: DataTask, context: dict = None) -> Tuple[List[Dict], Optional[LLMMetrics]]:
        """Runs a single partition query"""
        context = context or {}
        prompt = task.to_prompt()
        
        try:
            # Dispatch based on mode
            if hasattr(task, 'execution_mode') and task.execution_mode == 'row_by_row':
                return await self._execute_row_by_row(task, context)
                
            # Default: Batch Mode
            # Using low temp for extraction
            response = await self.llm.agenerate(prompt, temperature=self.extraction_temperature, response_format={"type": "json_object"})
            
            content = response.content
            metrics = response.metrics
            
            # Artifact Persistence: Trace
            if "output_dir" in context:
                # ... existing trace logic ...
                self._save_trace(
                    task=task,
                    prompt=prompt,
                    response=content,
                    tokens=metrics.total_tokens,
                    context=context
                )
            
            # Basic cleaning
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0]
            elif "```" in content:
                content = content.split("```")[1].split("```")[0]
                
            # Auto-unwrap if "items" or "rows" key exists (or repair if truncated)
            data = []
            
            # --- PAGINATION & COMPLETENESS LOOP ---
            current_prompt = prompt
            all_data = [] # Accumulate rows across pages
            attempts = 0
            max_attempts = 3
            
            # Helper to accumulate cost across attempts
            if metrics is None:
                metrics = LLMMetrics() # Base metrics if any
                
            while attempts < max_attempts:
                attempts += 1
                
                # Execute LLM Call
                response = await self.llm.agenerate(current_prompt, temperature=self.extraction_temperature, response_format={"type": "json_object"})
                content = response.content
                
                # Accumulate Metrics (Cost of all attempts)
                if response.metrics:
                    metrics = metrics + response.metrics
                
                # ... [Parsing Logic Reuse] ...
                chunk_data = []
                try:
                    # 1. Fast path: Valid JSON
                    parsed = json.loads(content)
                    if isinstance(parsed, dict):
                        for key in ["items", "rows", "data"]:
                            if key in parsed and isinstance(parsed[key], list):
                                parsed = parsed[key]
                                break
                                
                    if isinstance(parsed, list):
                        chunk_data = parsed
                    else:
                        logger.warning(f"Task {task.trace_id} returned non-list JSON: {type(parsed)}")
                        
                except json.JSONDecodeError:
                    # 2. Robust path: Handle Truncation (Common with "List ALL" prompt)
                    logger.warning(f"Task {task.trace_id} JSON Decode Error (likely truncation). Attempting repair...")
                    
                    s = content.strip()
                    if s.startswith("{") and '"items":' in s:
                        last_comma = s.rfind("},")
                        if last_comma != -1:
                            fixed = s[:last_comma+1] + "]}" 
                            try:
                                parsed = json.loads(fixed)
                                if isinstance(parsed, dict) and "items" in parsed:
                                    chunk_data = parsed["items"]
                                    logger.info(f"Task {task.trace_id}: Repair successful (Wrapped). Recovered {len(chunk_data)} items.")
                            except:
                                pass
                    elif s.startswith("["):
                        last_comma = s.rfind("},")
                        if last_comma != -1:
                            fixed = s[:last_comma+1] + "]"
                            try:
                                chunk_data = json.loads(fixed)
                                logger.info(f"Task {task.trace_id}: Repair successful (List). Recovered {len(chunk_data)} items.")
                            except:
                                pass

                if chunk_data:
                    all_data.extend(chunk_data)
                
                # COMPLETENESS CHECK
                # Only check if we have an estimate and it's substantial
                if getattr(task, 'estimated_rows', 0) and task.estimated_rows > 10:
                    if len(all_data) < 0.5 * task.estimated_rows:
                        logger.info(f"Task {task.trace_id}: Incomplete Result ({len(all_data)}/{task.estimated_rows}). Reprompting...")
                        # Construct Reprompt
                        current_prompt = prompt + f"\n\nPREVIOUSLY EXTRACTED: {len(all_data)} rows. We expected {task.estimated_rows}.\nCONTINUE extraction from where you left off. Do not repeat previous items. Return next batch."
                        continue # Loop again
                    else:
                        break # Good enough
                else:
                    break # No estimate, single pass
            
            data = all_data

            if not data and "Unterminated string" not in str(content):
                # Only warn if we really failed to get anything
                logger.warning(f"Task {task.trace_id} failed to parse any data.")

            if data:
                # Enrich with partition info if missing
                for item in data:
                    if task.partition_key not in item and task.partition_key != "All":
                        if task.partition_key: # Ensure it's not None
                            item[task.partition_key] = task.partition_value
                
                # CHUNK VALIDATION
                if task.partition_key and task.partition_key != "All":
                    # Create constraint dict
                    constraints = {task.partition_key: task.partition_value}
                    
                    # Convert list to DF for validation
                    chunk_df = pd.DataFrame(data)
                    chunk_df = await self.validator.validate_chunk(chunk_df, constraints)
                    
                    # Convert back to list
                    data = chunk_df.to_dict('records')
                    
                return data, metrics
            else:
                return [], metrics

        except Exception as e:
            logger.error(f"Task {task.trace_id} CRITICAL FAILURE: {e}")
            return [], None

    def _save_trace(self, task, prompt, response, tokens, context):
        import os
        try:
            trace_entry = {
                "task_id": task.trace_id,
                "mode": getattr(task, 'execution_mode', 'batch'),
                "prompt": prompt,
                "response": response,
                "tokens": tokens
            }
            with open(os.path.join(context["output_dir"], "trace.jsonl"), "a") as f:
                f.write(json.dumps(trace_entry) + "\n")
        except Exception as e:
            pass

    async def _execute_row_by_row(self, task: DataTask, context: dict) -> Tuple[List[Dict], Optional[LLMMetrics]]:
        """
        Executes a task in two steps using SOTA prompts:
        1. Generate list of keys (Primary Key entities).
        2. Generate full row for each key (in parallel).
        """
        # A. Identify Key Column(s)
        # If schema exists, use schema keys. Else fallback.
        keys = ["Entity"]
        all_cols = ["Entity", "Description"]
        
        if task.schema:
            if task.schema.primary_keys:
                keys = task.schema.primary_keys
            all_cols = [c.name for c in task.schema.columns]
        
        key_col_str = keys[0] # For single key simple logic
        
        # Helper for "key_desc"
        if len(keys) == 1:
            key_desc = f"The key column in the table is {keys[0]}"
        else:
            key_desc = f"The key columns in the table are {', '.join(keys)}"
            
        columns_str = ", ".join(all_cols)

        # B. Step 1: Generate Keys
        # Build prompt using SOTA template
        keys_prompt = ROW_BY_ROW_KEYS_PROMPT.format(
            query=task.query,
            key_desc=key_desc,
            key_col=keys[0] # Focusing on primary key for list generation
        )
        
        keys_resp = await self.llm.agenerate(keys_prompt, temperature=self.extraction_temperature, response_format={"type": "json_object"})
        keys_content = keys_resp.content
        total_metrics = keys_resp.metrics
        
        self._save_trace(task, keys_prompt, keys_content, keys_resp.metrics.total_tokens, context)
        
        keys_data = [] # List of dicts
        try:
            parsed = json.loads(keys_content)
            # SOTA Logic: Handle wrapped JSON or list
            if isinstance(parsed, dict):
                # New Optimized SOTA Prompt returns {"keys": [...]}
                if "keys" in parsed and isinstance(parsed["keys"], list):
                    keys_data = parsed["keys"]
                elif len(parsed) == 1 and isinstance(list(parsed.values())[0], list):
                    # Fallback for "items" or prompt hallucination
                    keys_data = list(parsed.values())[0]
                else: 
                    # Maybe it's just 'items': [...]
                    for v in parsed.values():
                        if isinstance(v, list):
                            keys_data = v
                            break
            elif isinstance(parsed, list):
                keys_data = parsed
                
            # Normalize keys to dicts
            normalized_keys = []
            for k in keys_data:
                if isinstance(k, dict):
                    normalized_keys.append(k)
                elif isinstance(k, str):
                    normalized_keys.append({keys[0]: k})
            
            keys_data = normalized_keys
            
        except Exception as e:
            logger.error(f"Row-by-Row key parse error: {e}")
            return [], total_metrics

        if not keys_data:
            logger.warning("No keys found in response.")
            return [], total_metrics

        # C. Step 2: Generate Rows (Parallel)
        async def fetch_row(key_instance: dict):
            # Format key representation for prompt: "(Name = Key, ...)"
            # SOTA Logic
            key_repr_parts = []
            for k, v in key_instance.items():
                key_repr_parts.append(f"{k} = {v}")
            row_key_str = f"({', '.join(key_repr_parts)})"
            
            # Use specific value for single key case in prompt variable if needed, 
            # but template takes {key_val} which maps to row_key which is the parenthesized list
            # Wait, SOTA template uses %s for key value.
            # "Retrieve a single row whose key is %s" -> "(Name = Foo)"
            
            row_prompt = ROW_BY_ROW_FETCH_PROMPT.format(
                query=task.query,
                columns=columns_str,
                key_desc=key_desc,
                key_col=keys[0], # Not strictly used in new template logic but passed for safety
                key_val=row_key_str
            )
            
            resp = await self.llm.agenerate(row_prompt, temperature=0.0, response_format={"type": "json_object"})
            return resp, row_prompt, key_instance
        
        row_tasks = [fetch_row(k) for k in keys_data]
        row_results = await asyncio.gather(*row_tasks, return_exceptions=True)
        
        final_rows = []
        for res in row_results:
            if isinstance(res, Exception):
                logger.error(f"Row failure detected: {res}")
                continue
                
            resp, prompt, key_inst = res
            if resp.metrics:
                total_metrics = total_metrics + resp.metrics
            self._save_trace(task, prompt, resp.content, resp.metrics.total_tokens, context)
            
            try:
                row_obj = json.loads(resp.content)
                # Handle wrapping
                if isinstance(row_obj, dict) and len(row_obj) == 1:
                    # Check if value is dict (wrapped)
                    first_val = list(row_obj.values())[0]
                    if isinstance(first_val, dict):
                        row_obj = first_val
                
                # Enforce Key Consistency (Overwrite with prompted key)
                for k, v in key_inst.items():
                    row_obj[k] = v
                    
                final_rows.append(row_obj)
            except:
                pass
                
        return final_rows, total_metrics
            
    def _reduce(self, results: List[List[Dict]], columns: List[Column]) -> pd.DataFrame:
        flat_list = []
        for batch in results:
            flat_list.extend(batch)
            
        if not flat_list:
            return pd.DataFrame(columns=[c.name for c in columns])
            
        df = pd.DataFrame(flat_list)
        
        # Ensure schema structure
        for col in columns:
            if col.name not in df.columns:
                df[col.name] = None
                
        # Select ordered columns
        df = df[[c.name for c in columns]]
        
        # Simple deduplication (Validator can do more advanced stuff)
        # Fix for unhashable types (lists/dicts) returned by LLM
        for col in df.columns:
            df[col] = df[col].apply(lambda x: str(x) if isinstance(x, (list, dict)) else x)
            
        # Drop strict duplicates
        df = df.drop_duplicates()
        
        return df
