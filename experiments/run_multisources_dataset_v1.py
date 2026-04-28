import asyncio
import os
import sys
import pandas as pd
import time
import json
import logging
import datetime
import argparse
from typing import List, Dict

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import src
from src.interface import PKDB, PKDBConfig
from src.schema import SchemaDefinition, Column
from experiments.run_mini_dataset import setup_global_logging, query_logging_context
from experiments.evaluate_multisources_metrics import evaluate_offline


def _normalize_model_name(model: str) -> str:
    return model.replace("/", "_").replace(" ", "_")


async def run_trials(dataset_path: str, base_output_dir: str, args=None):
    now = datetime.datetime.now()
    timestamp = now.strftime("%Y%m%d_%H%M%S")
    
    if args and args.resume_dir:
        run_dir = args.resume_dir
        if not os.path.exists(run_dir):
            os.makedirs(run_dir, exist_ok=True)
        print(f"Resuming Multisources Dataset v1 experiment at: {run_dir}")
    else:
        run_dir = os.path.join(
            base_output_dir,
            f"pkdb_{_normalize_model_name(args.model if args else 'gpt-4o')}_{now.strftime('%Y%m%d')}_{now.strftime('%H%M%S')}_{now.strftime('%f')[:3]}",
        )
        os.makedirs(run_dir, exist_ok=True)
        print(f"Starting Multisources Dataset v1 experiment at: {run_dir}")
        
    setup_global_logging(run_dir)
    
    with open(dataset_path, 'r') as f:
        cfg = json.load(f)
        
    target_items = list(cfg.values())
    
    limit = args.limit if args and args.limit > 0 else len(target_items)
    target_items = target_items[:limit]
    
    if args and getattr(args, 'table', None):
        target_items = [item for item in target_items if item['id'] == args.table]
        if not target_items:
            print(f"ERROR: Table '{args.table}' not found in config. Aborting.")
            return

    run_config = {
        "timestamp": timestamp,
        "model": args.model if args else "gpt-4o",
        "limit": limit,
        "use_cache": getattr(args, 'use_cache', False),
        "max_concurrent": getattr(args, 'max_concurrent', 10),
        "max_cost_per_table": getattr(args, 'max_cost_per_table', 2.0),
        "execution_mode": getattr(args, 'execution_mode', "batch"),
        "generated_by": "experiments/run_multisources_dataset_v1.py"
    }
    run_config["disable_validators"] = getattr(args, 'disable_validators', False)
    run_config["output_confidence"] = getattr(args, 'output_confidence', False)
    if getattr(args, 'map_validators', None) is not None:
        run_config["map_validators"] = args.map_validators
    if getattr(args, 'global_validators', None) is not None:
        run_config["global_validators"] = args.global_validators
    with open(os.path.join(run_dir, "run_config.json"), "w") as f:
        json.dump(run_config, f, indent=4)

    # Shared Resource Config (vital to prevent API throttling)
    shared_state = {
        "rate_limit_times": [], 
        "rate_limit_lock": asyncio.Lock(), 
        "semaphore": asyncio.Semaphore(100)
    }
    sim_semaphore = asyncio.Semaphore(args.table_concurrent if args else 5)

    results = []

    async def process_table(item):
        async with sim_semaphore:
            table_name = item['id']
            
            cfg_kwargs = dict(
                model=args.model if args else "gpt-4o",
                use_cache=getattr(args, 'use_cache', False),
                max_concurrent=getattr(args, 'max_concurrent', 10),
                default_execution_mode=getattr(args, 'execution_mode', "batch"),
                max_cost_per_table=getattr(args, 'max_cost_per_table', 0.0),
                decomposition_strategy=getattr(args, 'decomposition_strategy', 'none'),
                row_threshold=getattr(args, 'row_threshold', 120),
                max_total_requests=getattr(args, 'max_total_requests', 10000),
                source=getattr(args, 'source', 'Wikipedia'),
                partition_selection_mode=getattr(args, 'partition_selection_mode', 'llm'),
                entity_resolution_mode=getattr(args, 'entity_resolution_mode', 'llm'),
                chunk_repair_mode=getattr(args, 'chunk_repair_mode', 'drop'),
                output_confidence=getattr(args, 'output_confidence', False),
            )
            if getattr(args, 'disable_validators', False):
                cfg_kwargs["map_validators"] = []
                cfg_kwargs["global_validators"] = []
            if getattr(args, 'map_validators', None) is not None:
                cfg_kwargs["map_validators"] = args.map_validators
            if getattr(args, 'global_validators', None) is not None:
                cfg_kwargs["global_validators"] = args.global_validators
            pkdb_cfg = PKDBConfig(**cfg_kwargs)
            pkdb = PKDB(config=pkdb_cfg, shared_state=shared_state)

            table_dir = os.path.join(run_dir, table_name)
            os.makedirs(table_dir, exist_ok=True)

            out_file = os.path.join(table_dir, f"{table_name}.csv")
            df = None

            # Skip Logic for Resuming
            if os.path.exists(out_file):
                print(f"LOAD {table_name}: Result already exists. Skipping Generation.")
                try:
                    df = pd.read_csv(out_file)
                    return {
                        "Table": table_name,
                        "Category": item.get('category', 'Unknown'),
                        "Extracted_Rows": len(df),
                        "Time_s": 0.0,
                        "Cost_USD": 0.0,
                        "Status": "Cached"
                    }
                except Exception as e:
                    print(f"Error loading {table_name}: {e}. Re-running.")
                    df = None
            
            cols = [Column(name=c, semantic_type="text") for c in item['columns']]
            target_schema = SchemaDefinition(table_name=table_name, columns=cols, primary_keys=item['primary_key'])
            task_query = f"Generate a detailed data table to satisfy this objective: {item['table_title']}"
            
            print(f"\n--- Extracting: {table_name} ---")
            start = time.time()
            try:
                with query_logging_context(table_dir, "process"):
                    df, conf_df, metrics, subqueries = await pkdb.extract_table(
                        task_query,
                        schema=target_schema,
                        context={"output_dir": table_dir},
                        plan_only=getattr(args, 'plan_only', False)
                    )
                    duration = time.time() - start

                df.to_csv(out_file, index=False)
                if conf_df is not None and not conf_df.empty:
                    conf_file = os.path.join(table_dir, f"{table_name}_confidence.csv")
                    conf_df.to_csv(conf_file, index=False)
                
                print(f"-> {table_name}: Extracted {len(df)} rows. Time: {duration:.2f}s. Cost: ${metrics.cost:.4f}")
                
                return {
                    "Table": table_name,
                    "Category": item.get('category', 'Unknown'),
                    "Extracted_Rows": len(df),
                    "Subqueries": subqueries,
                    "Time_s": duration,
                    "Cost_USD": metrics.cost,
                    "Status": "Success"
                }

            except Exception as e:
                import traceback
                traceback.print_exc()
                print(f"-> Failed {table_name}: {e}")
                return {
                    "Table": table_name,
                    "Category": item.get('category', 'Unknown'),
                    "Status": f"Error: {e}"
                }

    tasks = [process_table(item) for item in target_items]
    res_list = await asyncio.gather(*tasks)
    
    summary_df = pd.DataFrame([r for r in res_list if r])
    if not summary_df.empty:
        print("\n=== MULTISOURCES DATASET V1 GENERATION SUMMARY ===")
        print(summary_df)
        summary_path = os.path.join(run_dir, "summary.csv")
        summary_df.to_csv(summary_path, index=False)
        print(f"\nSaved generation logs to {summary_path}")
        
        # Calculate Averages and Totals for Generation Metrics
        numeric_cols = ["Time_s", "Cost_USD"]
        means = summary_df[numeric_cols].mean()
        metrics_dict = means.to_dict()
        metrics_dict["Total_Cost_USD"] = summary_df['Cost_USD'].sum()
        
        metrics_path = os.path.join(run_dir, "metrics.json")
        with open(metrics_path, "w") as f:
            json.dump(metrics_dict, f, indent=2)
        print(f"Saved generation metrics to {metrics_path}")
        
    if not getattr(args, 'no_eval', False):
        try:
            print("\n=== TRIGGERING METRICS EVALUATION ===")
            evaluate_offline(run_dir, dataset_path, args.tables_dir, args)
        except Exception as e:
            print(f"Failed to run evaluation offline: {e}")
    else:
        print(f"\nCompleted. Skipped evaluation block due to --no_eval.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset_path", type=str, default="datasets/multisources_dataset_v1/cfg.json")
    parser.add_argument("--tables_dir", type=str, default="datasets/multisources_dataset_v1/tables/")
    parser.add_argument("--base_output_dir", type=str, default="exp_results/multisources_dataset_v1")
    parser.add_argument("--limit", type=int, default=0, help="Max number of tables (0 = all)")
    parser.add_argument("--model", type=str, default="gpt-4o")
    parser.add_argument("--use_cache", action="store_true")
    parser.add_argument("--max_concurrent", type=int, default=10)
    parser.add_argument("--table_concurrent", type=int, default=5, help="Number of tables to process concurrently")
    parser.add_argument("--resume_dir", type=str, default=None, help="Directory to resume run from")
    parser.add_argument("--execution_mode", type=str, default="batch", choices=["batch", "row_by_row"])
    parser.add_argument("--table", type=str, default=None, help="Run only this specific table by name")
    parser.add_argument("--max_cost_per_table", type=float, default=2.0, help="Per-table cost cap in USD")
    parser.add_argument("--plan_only", action="store_true", help="Dry-run: plan only, skip extraction calls")
    # Engine Settings
    parser.add_argument("--decomposition_strategy", type=str, default="none", choices=["rule_based", "llm_based", "none"])
    parser.add_argument("--row_threshold", type=int, default=120)
    parser.add_argument("--max_total_requests", type=int, default=10000, help="Max LLM requests per table")
    parser.add_argument("--source", type=str, default="Wikipedia", help="Knowledge source label injected into prompts")
    parser.add_argument("--partition_selection_mode", type=str, default="llm", choices=["scoring", "llm"])
    parser.add_argument("--entity_resolution_mode", type=str, default="llm",
                        choices=["semantic_type", "keyword", "llm"],
                        help="How to identify the entity identity column for EntityResolutionValidator")
    parser.add_argument("--output_confidence", action="store_true", help="Ask LLM to output per-cell confidence scores; saves <table>_confidence.csv")
    parser.add_argument("--disable_validators", action="store_true", help="Disable all map/global validators")
    parser.add_argument("--map_validators", type=str, nargs="*", default=None,
                        help="Override map-side validators (space-separated). Use empty string for none.")
    parser.add_argument("--global_validators", type=str, nargs="*", default=None,
                        help="Override global validators (space-separated). Use empty string for none.")
    parser.add_argument("--chunk_repair_mode", type=str, default="drop", choices=["drop", "repair"],
                        help="ChunkValidator fix behavior: drop invalid rows or attempt LLM repair")
    
    # Auto-eval settings
    parser.add_argument("--no_eval", action="store_true", help="Skip default metric validation")
    parser.add_argument("--epsilon", type=float, default=0.01, help="Numeric tolerance for value matches (default 0.01 for 1%)")
    parser.add_argument("--no_fuzzy_match", action="store_true", help="Disable fuzzy string matching")
    
    args = parser.parse_args()
    
    asyncio.run(run_trials(
        dataset_path=args.dataset_path,
        base_output_dir=args.base_output_dir,
        args=args
    ))
