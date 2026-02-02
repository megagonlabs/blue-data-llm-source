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
                
            data = json.loads(content)
            
            # Auto-unwrap if "items" or "rows" key exists
            if isinstance(data, dict):
                for key in ["items", "rows", "data"]:
                    if key in data and isinstance(data[key], list):
                        data = data[key]
                        break
            
            if isinstance(data, list):
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
                logger.warning(f"Task {task.trace_id} returned non-list JSON. Type: {type(data)}. Content snippet: {str(data)[:200]}")
                return [], metrics
                
        except Exception as e:
            logger.error(f"Task {task.trace_id} failed: {e}")
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
        Executes a task in two steps:
        1. Generate list of keys (Primary Key entities).
        2. Generate full row for each key (in parallel).
        """
        # A. Identify Key Column
        # If schema exists, use first primary key. Else ask LLM or use 'Entity'.
        key_col = "Entity"
        other_cols = ["..."]
        if task.schema and task.schema.primary_keys:
            key_col = task.schema.primary_keys[0] # Driver, Country, etc.
            other_cols = [c.name for c in task.schema.columns if c.name != key_col]
        else:
             # Try to find a reasonable key from constraints or query?
             # For now fallback to "Entity"
             pass

        # B. Step 1: Generate Keys
        # Build prompt: "List all [KeyColumn] for [Query]"
        keys_prompt = ROW_BY_ROW_KEYS_PROMPT.format(
            query=task.query,
            key_col=key_col,
            constraints=json.dumps(task.constraints)
        )
        
        keys_resp = await self.llm.agenerate(keys_prompt, temperature=self.extraction_temperature, response_format={"type": "json_object"})
        keys_content = keys_resp.content
        total_metrics = keys_resp.metrics
        
        self._save_trace(task, keys_prompt, keys_content, keys_resp.metrics.total_tokens, context)
        
        try:
            keys_data = json.loads(keys_content)
            # Unwrap
            if isinstance(keys_data, dict):
                 # Look for list values
                 for v in keys_data.values():
                     if isinstance(v, list):
                         keys_data = v
                         break
            
            if not isinstance(keys_data, list):
                 logger.warning(f"Row-by-Row: Failed to parse keys list. Got {type(keys_data)}")
                 return [], total_metrics
                 
        except Exception as e:
            logger.error(f"Row-by-Row key parse error: {e}")
            return [], total_metrics

        # C. Step 2: Generate Rows (Parallel)
        # We need a semaphore for row generation as well if not already bounded
        # But _execute_map_task is already bounded by self.max_concurrent (e.g. 10).
        # If we launch N tasks here, we might flood.
        # C. Step 2: Generate Rows (Parallel)
        # Concurrency managed by LLMClient internally
        
        async def fetch_row(key_val):
            row_prompt = ROW_BY_ROW_FETCH_PROMPT.format(
                query=task.query,
                key_col=key_col,
                key_val=key_val,
                other_cols=', '.join(other_cols)
            )
            
            resp = await self.llm.agenerate(row_prompt, temperature=0.0, response_format={"type": "json_object"})
            return resp, row_prompt
        
        row_tasks = [fetch_row(k) for k in keys_data]
        row_results = await asyncio.gather(*row_tasks, return_exceptions=True)
        
        final_rows = []
        for i, res in enumerate(row_results):
            if isinstance(res, Exception):
                logger.error(f"Row failure for {keys_data[i]}: {res}")
                continue
                
            resp, prompt = res
            total_metrics = total_metrics + resp.metrics
            self._save_trace(task, prompt, resp.content, resp.metrics.total_tokens, context)
            
            try:
                row_obj = json.loads(resp.content)
                if isinstance(row_obj, list): row_obj = row_obj[0]
                # Enforce key
                row_obj[key_col] = keys_data[i]
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
