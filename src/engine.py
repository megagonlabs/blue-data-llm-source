import asyncio
import json
import logging
from typing import List, Dict, Any, Set, Tuple, Optional
import pandas as pd
from dataclasses import asdict

from src.dataplan import DataPlanRequest, MapTask
from src.schema import Column
from utils.llm_utils import LLMClient, LLMMetrics, sum_metrics
from src.validator import Validator

logger = logging.getLogger(__name__)

class MapReduceEngine:
    """
    Runtime engine for the Auto-Partitioning framework.
    1. Map: Executes MapTasks in parallel.
    2. Reduce: Aggregates results into a single DataFrame.
    """
    
    def __init__(self, llm_client: LLMClient, max_concurrent: int = 5, extraction_temperature: float = 0.0):
        self.llm = llm_client
        self.max_concurrent = max_concurrent
        self.extraction_temperature = extraction_temperature
        self.validator = Validator(llm_client)
        
    async def execute(self, plan: DataPlanRequest) -> Tuple[pd.DataFrame, LLMMetrics]:
        logger.info(f"Starting execution of {len(plan.map_tasks)} map tasks.")
        
        # 1. Map Phase
        semaphore = asyncio.Semaphore(self.max_concurrent) # Limit concurrency
        
        async def run_task(task: MapTask) -> Tuple[List[Dict], Optional[LLMMetrics]]:
            async with semaphore:
                return await self._execute_map_task(task)
                
        tasks = [run_task(t) for t in plan.map_tasks]
        results_and_metrics = await asyncio.gather(*tasks)
        
        results = [r[0] for r in results_and_metrics]
        metrics_list = [r[1] for r in results_and_metrics if r[1]]
        total_metrics = sum_metrics(metrics_list) if metrics_list else LLMMetrics()
        
        # 2. Reduce Phase
        logger.info("Aggregating results...")
        df = self._reduce(results, plan.schema.columns)
        
        # 3. Global Validation
        df = self.validator.clean_data(df, primary_keys=plan.schema.primary_keys)
        
        logger.info(f"Execution complete. Generated {len(df)} rows. Total Tokens: {total_metrics.total_tokens}")
        return df, total_metrics
        
    async def _execute_map_task(self, task: MapTask) -> Tuple[List[Dict], Optional[LLMMetrics]]:
        """Runs a single partition query"""
        prompt = task.to_prompt()
        
        try:
            # Using low temp for extraction
            response = await self.llm.agenerate(prompt, temperature=self.extraction_temperature, response_format={"type": "json_object"})
            
            content = response.content
            metrics = response.metrics
            
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
                        item[task.partition_key] = task.partition_value
                
                # CHUNK VALIDATION
                if task.partition_key != "All":
                    # Create constraint dict
                    constraints = {task.partition_key: task.partition_value}
                    
                    # Convert list to DF for validation
                    chunk_df = pd.DataFrame(data)
                    chunk_df = self.validator.validate_chunk(chunk_df, constraints)
                    
                    # Convert back to list
                    data = chunk_df.to_dict('records')
                    
                return data, metrics
            else:
                logger.warning(f"Task {task.id} returned non-list JSON. Type: {type(data)}. Content snippet: {str(data)[:200]}")
                return [], metrics
                
        except Exception as e:
            logger.error(f"Task {task.id} failed: {e}")
            return [], None
            
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
