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

from utils.llm_utils import SharedCostTracker

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


def _normalize_model_name(model: str) -> str:
    return model.replace("/", "_").replace(" ", "_")

async def evaluate_dataset(dataset_path: str, base_output_dir: str, args=None):
    # 1. Create Independent Run Directory
    now = datetime.datetime.now()
    timestamp = now.strftime("%Y%m%d_%H%M%S")
    run_dir = os.path.join(
        base_output_dir,
        f"pkdb_{_normalize_model_name(args.model if args else 'gpt-4o')}_{now.strftime('%Y%m%d')}_{now.strftime('%H%M%S')}",
    )
    os.makedirs(run_dir, exist_ok=True)
    print(f"Starting new experiment run at: {run_dir}")
    
    
    # 2. Setup Logging
    setup_global_logging(run_dir)
    
    # 3. Copy Config (Dataset)
    # Skipped per user request
    # shutil.copy(dataset_path, os.path.join(run_dir, "config_mini_dataset.json"))
    
    # 4. Save Run Config
    # Configurable Experiment Settings from Args or Defaults
    config = {
        "timestamp": timestamp,
        "dataset_path": dataset_path,
        "model": args.model if args else "gpt-4o",
        "schema_selection_strategy": args.strategy if args else "llm_rank",
        "use_cache": args.use_cache if args else False,
        "max_concurrent": args.max_concurrent if args else 20,
        "max_partitions": args.max_partitions if args else 50,
        "num_schema_candidates": 3,   # Still hardcoded for now, or add arg
        "schema_temperature": 0.7,
        "extraction_temperature": 0.0,
        "decomposition_strategy": args.decomposition_strategy if args else "rule_based",
        "row_threshold": args.row_threshold if args else 50,
        "max_depth": args.max_depth if args else 1,
        "partitioning_method": args.partitioning_method if args else "semantic",
        "partition_selection_mode": args.partition_selection_mode if args else "llm",
        "entity_resolution_mode": args.entity_resolution_mode if args else "llm",
    }
    
    with open(os.path.join(run_dir, "run_config.json"), 'w') as f:
        json.dump(config, f, indent=4)
    
    print(f"Loading dataset from {dataset_path}...")
    with open(dataset_path, 'r') as f:
        dataset = json.load(f)

    print(f"Initializing PKDB with config: {config}...")
    
    # Shared State for Global Limits
    shared_state = {
        "cost_tracker": SharedCostTracker(max_total_requests=args.max_total_requests if args else 10000, max_total_cost=50.0)
    }

    pkdb = PKDB(
        model=config["model"], 
        cache_dir=".cache_pkdb",
        use_cache=config["use_cache"],
        max_concurrent=config["max_concurrent"],
        max_partitions=config["max_partitions"],
        num_schema_candidates=config["num_schema_candidates"],
        schema_temperature=config["schema_temperature"],
        extraction_temperature=config["extraction_temperature"],
        decomposition_strategy=config["decomposition_strategy"],
        row_threshold=config["row_threshold"],
        max_depth=config["max_depth"],
        partitioning_method=config["partitioning_method"],
        partition_selection_mode=config["partition_selection_mode"],
        entity_resolution_mode=config["entity_resolution_mode"],
        shared_state=shared_state
    )
    
    results = []
    
    # Dataset is now a dict, iterate values
    tasks = list(dataset.values())
    
    # Filter by IDs if provided
    if args and args.ids:
        target_ids = [tid.strip() for tid in args.ids.split(",")]
        tasks = [t for t in tasks if t['id'] in target_ids]
        print(f"Filtering dataset to {len(tasks)} tasks: {target_ids}")
    
    for i, item in enumerate(tasks):
        uid = item['id']
        category = item['category']
        title = item['table_title']
        q = f"List all {title}"
        
        print(f"\n--- [{i+1}/{len(tasks)}] {category}: '{q}' ---")
        
        start = time.time()
        
        # Setup individual log for this query
        # Create per-query output directory
        query_out_dir = os.path.join(run_dir, uid)
        os.makedirs(query_out_dir, exist_ok=True)
        
        # Setup individual log for this query (inside its own dir)
        with query_logging_context(query_out_dir, "process"):
            try:
                # Pass output_dir context to PKDB
                # Validator Config
                from src.validator import DataValidator
                
                # Use new validators for Generic Fixes
                # Split validators into Map-side (local) and Global (reduce)
                map_strategies = ["ChunkConstraints", "FormatNormalization"]
                global_strategies = ["EntityResolution", "Deduplication"]
                
                map_validator = DataValidator(pkdb.llm, strategies=map_strategies)
                global_validator = DataValidator(pkdb.llm, strategies=global_strategies)
                
                # Inject dependencies via extract_table
                pkdb.engine.completeness_strategy = args.completeness_strategy if args else "heuristic"
                
                df, _, metrics, subquery_count = await pkdb.extract_table(
                    q,
                    strategy=config["schema_selection_strategy"],
                    context={"output_dir": query_out_dir, "dataset_id": uid},
                    map_validator=map_validator,
                    global_validator=global_validator
                )
                duration = time.time() - start
                count = len(df)
                
                # Log to both console (via root handler) and file (via query handler)
                msg = f"-> Extracted {count} rows in {duration:.2f}s. Metrics: {metrics.total_tokens} tokens. Cost: ${metrics.cost:.4f}. Subqueries: {subquery_count}"
                print(msg)
                logging.info(msg)
                
                # Save individual table in query dir
                out_file = os.path.join(query_out_dir, f"{uid}.csv")
                df.to_csv(out_file, index=False)

                # Artifact Persistence: Cost & Metrics
                # Calculate derived metrics
                req_count = metrics.request_count if metrics.request_count > 0 else 1
                avg_tokens = metrics.total_tokens / req_count
                
                cost_artifact = {
                    "query_id": uid,
                    "duration_seconds": duration,
                    "total_cost_usd": metrics.cost,
                    "total_tokens": metrics.total_tokens,
                    "input_tokens": metrics.input_tokens,
                    "output_tokens": metrics.output_tokens,
                    "request_count": metrics.request_count,
                    "avg_tokens_per_prompt": avg_tokens,
                    "max_tokens_per_prompt": metrics.max_tokens_per_request
                }
                
                with open(os.path.join(query_out_dir, "cost.json"), "w") as f:
                    json.dump(cost_artifact, f, indent=2)
                
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
                    "Request_Count": metrics.request_count,
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

    # Save detailed metrics/config JSON
    metrics_path = os.path.join(run_dir, "metrics.json")
    
    metrics_dict = {
        "Model": config.get("model", "unknown"),
        "Total_Time_s": summary_df['Time_s'].sum() if 'Time_s' in summary_df.columns else 0.0,
        "Total_Cost_USD": summary_df['Cost_USD'].sum() if 'Cost_USD' in summary_df.columns else 0.0,
        "Total_Tokens": int(summary_df['Total_Tokens'].sum()) if 'Total_Tokens' in summary_df.columns else 0,
        "Total_Input_Tokens": int(summary_df['Input_Tokens'].sum()) if 'Input_Tokens' in summary_df.columns else 0,
        "Total_Output_Tokens": int(summary_df['Output_Tokens'].sum()) if 'Output_Tokens' in summary_df.columns else 0,
        "Total_Request_Count": int(summary_df['Request_Count'].sum()) if 'Request_Count' in summary_df.columns else 0
    }
    
    with open(metrics_path, 'w') as f:
        json.dump(metrics_dict, f, indent=2)
    print(f"Saved aggregate metrics to {metrics_path}")

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Run Mini Dataset Benchmark")
    parser.add_argument("--dataset_path", type=str, default="datasets/mini_dataset.json", help="Path to input dataset JSON")
    parser.add_argument("--base_output_dir", type=str, default="exp_results/mini_dataset", help="Base directory for output runs")
    
    # Core Configs
    parser.add_argument("--model", type=str, default="gpt-4o", help="LLM model name")
    parser.add_argument("--strategy", type=str, default="llm_rank", help="Schema selection strategy")
    parser.add_argument("--decomposition_strategy", type=str, default="llm_based", choices=["rule_based", "llm_based", "none"], help="Decomposition strategy")
    parser.add_argument("--partitioning_method", type=str, default="llm_based", choices=["llm_based", "first", "semantic", "greedy"], help="Partitioning method")
    parser.add_argument("--partition_selection_mode", type=str, default="llm", choices=["scoring", "llm"], help="Method for selecting partition key (scoring or llm)")
    parser.add_argument("--entity_resolution_mode", type=str, default="llm",
                        choices=["semantic_type", "keyword", "llm"],
                        help="How to identify the entity identity column for EntityResolutionValidator")
    parser.add_argument("--completeness_strategy", type=str, default="heuristic", choices=["heuristic", "llm"], help="Completeness check strategy")
    
    # Toggles & Thresholds
    parser.add_argument("--use_cache", action="store_true", help="Enable semantic caching")
    parser.add_argument("--row_threshold", type=int, default=50, help="Row count threshold for decomposition")
    parser.add_argument("--max_depth", type=int, default=1, help="Max recursion depth")
    parser.add_argument("--max_partitions", type=int, default=200, help="Max partitions per map task")
    parser.add_argument("--max_concurrent", type=int, default=20, help="Max concurrent requests")
    parser.add_argument("--max_total_requests", type=int, default=10000, help="Global max total LLM requests limit")
    parser.add_argument("--ids", type=str, default=None, help="Comma-separated list of dataset IDs to run (e.g. 'geo_countries,sci_elements')")
    
    args = parser.parse_args()
    
    asyncio.run(evaluate_dataset(
        dataset_path=args.dataset_path, 
        base_output_dir=args.base_output_dir,
        args=args
    ))
