import asyncio
import os
import sys
import pandas as pd
import time
import json

import logging

import shutil
import datetime
import contextlib

# Ensure project root is in path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.interface import PKDB

@contextlib.contextmanager
def query_logging_context(run_dir: str, uid: str):
    log_file = os.path.join(run_dir, f"{uid}.log")
    handler = logging.FileHandler(log_file, mode='w')
    handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
    
    root = logging.getLogger()
    root.addHandler(handler)
    try:
        yield
    finally:
        root.removeHandler(handler)
        handler.close()

def setup_global_logging(output_dir: str):
    log_file = os.path.join(output_dir, "experiment_details.log")
    
    # Configure root logger to capture all src modules
    # Force reset handlers to avoid duplicate logs
    root = logging.getLogger()
    if root.handlers:
        for handler in root.handlers:
            root.removeHandler(handler)
            
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(log_file, mode='w'),
            logging.StreamHandler(sys.stdout)
        ]
    )
    print(f"Logging detailed execution traces to {log_file}")

async def evaluate_dataset(dataset_path: str, base_output_dir: str):
    # 1. Create Independent Run Directory
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = os.path.join(base_output_dir, f"run_{timestamp}")
    os.makedirs(run_dir, exist_ok=True)
    print(f"Starting new experiment run at: {run_dir}")
    
    # 2. Setup Logging
    setup_global_logging(run_dir)
    
    # 3. Copy Config (Dataset)
    shutil.copy(dataset_path, os.path.join(run_dir, "config_mini_dataset.json"))
    
    print(f"Loading dataset from {dataset_path}...")
    with open(dataset_path, 'r') as f:
        dataset = json.load(f)

    print("Initializing PKDB...")
    pkdb = PKDB(model="gpt-4o", cache_dir=".cache_pkdb")
    
    results = []
    
    # Dataset is now a dict, iterate values
    tasks = list(dataset.values())
    
    for i, item in enumerate(tasks):
        uid = item['id']
        category = item['category']
        title = item['table_title']
        q = f"List all {title}"
        
        print(f"\n--- [{i+1}/{len(tasks)}] {category}: '{q}' ---")
        
        start = time.time()
        
        # Setup individual log for this query
        with query_logging_context(run_dir, uid):
            try:
                df, metrics, subquery_count = await pkdb.extract_table(q)
                duration = time.time() - start
                count = len(df)
                
                print(f"-> Extracted {count} rows in {duration:.2f}s")
                print(f"-> Metrics: {metrics.total_tokens} tokens. Cost: ${metrics.cost:.4f}. Subqueries: {subquery_count}")
                
                # Save individual table
                out_file = os.path.join(run_dir, f"{uid}.csv")
                df.to_csv(out_file, index=False)
                
                results.append({
                    "ID": uid,
                    "Category": category,
                    "Query": q,
                    "Rows": count,
                    "Subqueries": subquery_count,
                    "Time_s": duration,
                    "Total_Tokens": metrics.total_tokens,
                    "Input_Tokens": metrics.input_tokens,
                    "Output_Tokens": metrics.output_tokens,
                    "Cost_USD": metrics.cost,
                    "Output_File": out_file,
                    "Status": "Success"
                })
            except Exception as e:
                print(f"-> Failed: {e}")
                results.append({
                    "ID": uid,
                    "Category": category,
                    "Query": q,
                    "Rows": 0,
                    "Subqueries": 0,
                    "Time_s": time.time() - start,
                    "Total_Tokens": 0,
                    "Cost_USD": 0.0,
                    "Status": f"Error: {str(e)}"
                })
            
    # Summary
    print("\n=== EVALUATION SUMMARY ===")
    summary_df = pd.DataFrame(results)
    # Reorder columns
    cols = ["ID", "Category", "Query", "Rows", "Subqueries", "Total_Tokens", "Cost_USD", "Time_s", "Status"]
    print(summary_df[cols])
    
    summary_path = os.path.join(run_dir, "benchmark_results.csv")
    summary_df.to_csv(summary_path, index=False)
    print(f"\nSummary saved to {summary_path}")

if __name__ == "__main__":
    asyncio.run(evaluate_dataset("experiments/mini_dataset.json", "experiments/runs"))
