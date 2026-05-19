import json
import os
import logging
import pandas as pd
import time
from typing import Dict, Any, List

from src.interface import PKDB

logger = logging.getLogger(__name__)

class WikiTabGenAdapter:
    """
    Adapter to run the WikiTabGen benchmark using PKDB.
    Equivalent to 'ExperimentRunner' in the original codebase.
    """
    
    def __init__(self, model: str = "gpt-3.5-turbo"):
        self.pkdb = PKDB(model=model, cache_dir=".cache_pkdb")
        
    async def run_benchmark(self, cfg_path: str, output_dir: str, limit: int = None):
        """
        Runs the benchmark on the full dataset defined in cfg.json.
        """
        # Load config
        with open(cfg_path, 'r') as f:
            data = json.load(f)
            
        # Ensure output dirs
        os.makedirs(output_dir, exist_ok=True)
        tables_dir = os.path.join(output_dir, "tables")
        os.makedirs(tables_dir, exist_ok=True)
        
        results_log = {}
        
        # Iterate over tasks
        # cfg.json is a dict keyed by ID "0", "1", etc.
        tasks = list(data.values())
        if limit and limit > 0:
            tasks = tasks[:limit]
            
        for i, task in enumerate(tasks):
            table_id = task.get('id', i)
            name = task.get('name', f"table_{i}")
            title = task['table_title']
            target_columns = task['columns']
            
            logger.info(f"[{i+1}/{len(tasks)}] Processing: {name}")
            
            # Construct Query
            query = f"List all {title}"
            
            start_time = time.time()
            try:
                # Execute PKDB pipeline
                # The pipeline will infer its own schema.
                # In a real benchmark, we might want to enforce target columns.
                # But let's test "Auto" partitioning capabilities first.
                
                df, _, __, ___ = await self.pkdb.extract_table(query)
                
                # Save CSV
                out_path = os.path.join(tables_dir, f"{name}.csv")
                df.to_csv(out_path, index=False)
                
                results_log[table_id] = {
                    "status": "success",
                    "rows": len(df),
                    "time": time.time() - start_time,
                    "output_path": out_path
                }
                
            except Exception as e:
                logger.error(f"Failed task {name}: {e}")
                results_log[table_id] = {
                    "status": "error",
                    "error": str(e)
                }
                
        # Save results log
        with open(os.path.join(output_dir, "result.json"), "w") as f:
            json.dump(results_log, f, indent=4)
            
        logger.info(f"Benchmark complete. Results saved to {output_dir}")
