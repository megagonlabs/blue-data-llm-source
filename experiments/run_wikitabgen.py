import asyncio
import os
import sys
import pandas as pd
import time
import json
import logging
import datetime
import argparse
from typing import List, Dict, Tuple
from unidecode import unidecode
from dateutil import parser as date_parser

# Ensure project root is in path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import src
print(f"DEBUG: src imported from: {src.__file__}")

from src.interface import PKDB
from src.schema import SchemaDefinition, Column
from experiments.run_mini_dataset import setup_global_logging, query_logging_context
from utils.llm_utils import SharedCostTracker

# --- METRIC UTILS (Ported from WikiTabGen/example_notebooks/Metrics_calculation.ipynb) ---

def normalize_value(value, is_date=False):
    if value != value: # NaN check
        return ''

    if is_date:
        try:
            return date_parser.parse(str(value))
        except:
            try:
                return pd.to_datetime(value)
            except:
                pass    
        
    # Handle numpy types
    if hasattr(value, 'item'):
        value = value.item()

    if isinstance(value, str):            
        clean_val = value.replace(',', '').replace('.', '')
        # Negative numbers
        if value.startswith('-') and clean_val.replace('-', '').isdigit():
            return float(value.replace(',', ''))
        # Integers
        if value.replace(',', '').isdigit():                
            return int(value.replace(',', ''))
        # Equals sign prefix?
        if value.startswith('=') and value[1:].isdigit():                
            return int(value.replace('=', ''))
        # Float
        if clean_val.isdigit():
            return float(value.replace(',', ''))
        
        value = value.strip().lower()

        if value in ('none', 'n/a', 'nan', '-'):
            return '' 

        value = value.replace('&', 'and')

        if value == 'united states':
            return 'usa'
        if value == 'united kingdom':
            return 'uk'
        
        value = unidecode(value)        
        value = ''.join(c for c in value if c.isalnum()) 
        return value

    return value

def normalize_key(value, is_date=False):
    if value != value:
        return ''

    if is_date:
        try:
            return str(date_parser.parse(str(value)))
        except:
            try:
                return str(pd.to_datetime(value))
            except:
                pass  

    if isinstance(value, str):  
        value = value.strip().lower()

        if value in ('none', 'n/a', 'nan', '-', '--', 'unknown'):
            return '' 

        value = value.replace('&', 'and')

        if value == 'united states':
            return 'usa'
        if value == 'united kingdom':
            return 'uk'

        value = unidecode(value)        
        value = ''.join(c for c in value if c.isalnum()) 
        return value

    return str(value)

def normalize_primary_columns(df, norm_columns, date_columns, primary_columns, keys_type):
    # Create copy to avoid SettingWithCopy warnings
    df = df.copy()
    
    for col in norm_columns:
        is_date = col in date_columns
        df[col] = df[col].apply(lambda x: normalize_key(x, is_date))
    
    for col, key_type in zip(primary_columns, keys_type):
        if key_type == 'year':
            try:
                df[col] = df[col].astype(float).astype(int)
            except:
                pass
            
        df[col] = df[col].astype(str)
        
    return [tuple(r) for r in df[primary_columns].to_numpy()], df

def find_row(df, columns, values):
    # row finding according
    # values is a tuple of normalized values
    # We need to match against the normalized columns in df
    
    # We assume df has already been normalized in `evaluate_table` wrapper
    # But wait, find_row in original code uses query builder string construction which is fragile
    # Let's do logical indexing manually
    
    mask = pd.Series(True, index=df.index)
    for col, val in zip(columns, values):
        mask &= (df[col] == val)
        
    return df[mask]

def evaluate_table_metrics(df_fetched, df_ref, primary_columns, keys_type, date_columns, epsilons):
    if df_fetched.empty:
        return 0, 0, 0, 0, 0, 0, 0, 0, 0, 0
        
    columns = df_ref.columns
    # Ensure columns match (strict schema enforcement should guarantee this, but safety check)
    if len(df_fetched.columns) != len(columns):
        # Allow column renaming if count matches? Or intersection?
        # For now assume strict schema worked
        pass
    
    df_fetched.columns = columns # Align headers
    df_fetched = df_fetched.drop_duplicates(subset=primary_columns)   

    norm_columns = set(primary_columns)        
    for pc in primary_columns:
        df_fetched = df_fetched[df_fetched[pc].notna()]    
    
    # helper returns (tuples_list, normalized_df)
    fetched_entities, df_fetched_norm = normalize_primary_columns(df_fetched, norm_columns, date_columns, primary_columns, keys_type)
    ref_entities, df_ref_norm = normalize_primary_columns(df_ref, norm_columns, date_columns, primary_columns, keys_type)

    total_matches = 0
    key_matches = 0
    
    # Original code iterates list.
    ref_entities_set = set(ref_entities)

    for fetched_entity in fetched_entities:
        if fetched_entity in ref_entities_set: 
            # Find the actual rows (normalized)
            row_fetched = find_row(df_fetched_norm, primary_columns, fetched_entity)
            row_ref = find_row(df_ref_norm, primary_columns, fetched_entity)
            
            if row_fetched.empty or row_ref.empty:
                continue
                
            key_matches += 1
            
            for column in columns:
                try:
                    value_fetched = row_fetched.iloc[0][column] # Use iloc[0]
                    value_ref = row_ref.iloc[0][column]

                    norm_value_fetched = normalize_value(value_fetched, column in date_columns)
                    norm_value_ref = normalize_value(value_ref, column in date_columns)

                    if norm_value_fetched == norm_value_ref:    
                        total_matches += 1
                    elif column in epsilons and norm_value_ref != '' and norm_value_fetched != '':
                        # Handle float comparisons
                        if isinstance(norm_value_ref, (int, float)) and isinstance(norm_value_fetched, (int, float)):
                            if norm_value_ref * 0.999 < norm_value_fetched < norm_value_ref * 1.001:
                                total_matches += 1   
                except Exception as e:
                    pass
            
    # Calculations
    n_cells_ref = df_ref.shape[0] * df_ref.shape[1]
    n_cells_fetched = df_fetched.shape[0] * df_fetched.shape[1]
    
    recall = total_matches / n_cells_ref if n_cells_ref > 0 else 0
    precision = total_matches / n_cells_fetched if n_cells_fetched > 0 else 0
    f1_score = 2*recall*precision/(recall+precision) if (recall + precision) > 0 else 0.0

    matches_len = len(ref_entities)
    fetched_len = len(fetched_entities)
    
    keys_recall = key_matches/matches_len if matches_len > 0 else 0
    keys_precision = key_matches/fetched_len if fetched_len > 0 else 0
    keys_f1_score = 2*keys_recall*keys_precision/(keys_recall+keys_precision) if (keys_recall + keys_precision) > 0 else 0.0
    
    nk = len(primary_columns)
    n_non_key_cells_ref = df_ref.shape[0] * (df_ref.shape[1] - nk)
    n_non_key_cells_fetched = df_fetched.shape[0] * (df_fetched.shape[1] - nk)
    
    non_key_matches = (total_matches - key_matches*nk)
    
    non_keys_recall = non_key_matches / n_non_key_cells_ref if n_non_key_cells_ref > 0 else 1.0 # 1 if empty?
    non_keys_precision = non_key_matches / n_non_key_cells_fetched if n_non_key_cells_fetched > 0 else 1.0
    non_keys_f1_score = 2*non_keys_recall*non_keys_precision/(non_keys_recall+non_keys_precision) if (non_keys_recall + non_keys_precision) > 0 else 0.0
    
    relative_non_key_accuracy = (total_matches - key_matches*nk) / (key_matches * (df_ref.shape[1] - nk)) if (key_matches * (df_ref.shape[1] - nk)) > 0 else 0.0
        
    return keys_recall, keys_precision, keys_f1_score, non_keys_recall, non_keys_precision, non_keys_f1_score, recall, precision, f1_score, relative_non_key_accuracy

# --- MAIN EVALUATION ---

async def evaluate_wikitabgen(cfg_path: str, tables_dir: str, base_output_dir: str, args=None):
    # 1. Setup Run Directory
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    
    if args and args.resume_dir:
        run_dir = args.resume_dir
        if not os.path.exists(run_dir):
            os.makedirs(run_dir, exist_ok=True)
        print(f"Resuming WikiTabGen benchmark at: {run_dir}")
    else:
        run_dir = os.path.join(base_output_dir, f"wikitabgen_{timestamp}")
        os.makedirs(run_dir, exist_ok=True)
        print(f"Starting WikiTabGen benchmark at: {run_dir}")
    
    setup_global_logging(run_dir)
    
    # 2. Load Config (Dataset Metadata)
    # Load Config
    print(f"Loading config from {cfg_path}...")
    with open(cfg_path, 'r') as f:
        cfg = json.load(f)
        
    # Convert to list for easier handling (it's dict of "0": {}, "1": {}...)
    cfg_items = [cfg[k] for k in sorted(cfg.keys(), key=lambda x: int(x))]
    
    # Filter
    if args and args.table:
        target_items = [item for item in cfg_items if item['name'] == args.table]
        if not target_items:
            print(f"Table {args.table} not found in config.")
            return
    else:
        limit = args.limit if args and args.limit > 0 else len(cfg_items)
        target_items = cfg_items[:limit]
    
    print(f"Selected {len(target_items)} tables for evaluation.")
    
    # Save Run Config
    run_config = {
        "timestamp": timestamp,
        "model": args.model if args else "gpt-4o",
        "limit": len(target_items),
        "table_filter": args.table if args else None,
        "use_cache": args.use_cache if args else False,
        "max_concurrent": args.max_concurrent if args else 10,
        "row_threshold": args.row_threshold if args else 50,
        "decomposition_strategy": args.decomposition_strategy,
        "partition_selection_mode": args.partition_selection_mode if args else "auto",
        "execution_mode": args.execution_mode if args else "batch",
        "plan_only": args.plan_only if args else False,
        "generated_by": "experiments/run_wikitabgen.py"
    }
    with open(os.path.join(run_dir, "run_config.json"), "w") as f:
        json.dump(run_config, f, indent=4)
    
    # 3. Initialize PKDB
    print("Initializing Shared Resources...")
    # These must be shared across all PKDB instances to prevent 429 errors
    shared_state = {
        "rate_limit_times": [],
        "rate_limit_lock": asyncio.Lock(),
        "semaphore": asyncio.Semaphore(100), # Global concurrency limit for LLM calls implies max_concurrent * tables
        "cost_tracker": SharedCostTracker(max_total_requests=args.max_total_requests if args else 100000, max_total_cost=100.0)
    }
    
    results = []
    
    sim_semaphore = asyncio.Semaphore(args.table_concurrent if args else 5) # Limit concurrent table extractions

    async def process_table(item):
        async with sim_semaphore:
            table_name = item['name']
            # Create FRESH PKDB instance per table to reset Safety Quotas (max_total_requests)
            # But share the rate limiter (shared_state) to protect the API
            from src.interface import PKDBConfig # Import Config
            
            cfg = PKDBConfig(
                model=args.model if args else "gpt-4o",
                use_cache=args.use_cache if args else True,
                max_concurrent=args.max_concurrent if args else 10,
                row_threshold=args.row_threshold if args else 120,
                decomposition_strategy=args.decomposition_strategy, 
                partitioning_method="smart",
                max_total_requests=1000,
                default_execution_mode=args.execution_mode if args else "batch",
                source=args.source if args else "Wikipedia" # [NEW]
            )
            
            pkdb = PKDB(
                config=cfg,
                shared_state=shared_state
            )
            
            table_dir = os.path.join(run_dir, table_name)
            os.makedirs(table_dir, exist_ok=True)
            
            # Check if result already exists
            out_file = os.path.join(table_dir, f"{table_name}.csv")
            df = None
            if os.path.exists(out_file):
                print(f"LOAD {table_name}: Result already exists. Re-calculating metrics.")
                try:
                    df = pd.read_csv(out_file)
                except Exception as e:
                    print(f"Error loading {table_name}: {e}. Re-running.")
            
            if df is None:
                title = item.get('table_title', table_name)
                
                # Construct STRICT SCHEMA
                cols = []
                numeric_cols = set(item.get('numericNonKeys', []))
                keys = set(item.get('keys', []))
                
                for c_name in item['columns']:
                    sem_type = "numerical" if c_name in numeric_cols else "text"
                    is_pk = c_name in keys
                    cols.append(Column(name=c_name, semantic_type=sem_type))
                
                target_schema = SchemaDefinition(
                    table_name=table_name,
                    columns=cols,
                    primary_keys=item.get('keys', [])
                )
                
                task_query = f"Generate a detailed table about {title}"
                
                print(f"\n--- Processing: {table_name} ---")
                start = time.time()
                try:
                    # Log to table_dir with separate log file
                    with query_logging_context(table_dir, "process"):
                        df, metrics, subqueries = await pkdb.extract_table(
                            task_query, 
                            schema=target_schema, 
                            context={"output_dir": table_dir},
                            plan_only=args.plan_only if args else False
                        )
                        duration = time.time() - start
                except Exception as e:
                    print(f"-> Failed {table_name}: {e}")
                    return {"Table": table_name, "Status": f"Error: {e}"}
            else:
                # Dummy metrics for loaded run
                from src.analysis import QueryAnalysisResult # Just a placeholder
                duration = 0.0
                subqueries = 0
                # We need to recreate metrics object or just mock cost
                class MockMetrics:
                    cost = 0.0
                    total_tokens = 0
                    input_tokens = 0
                    output_tokens = 0
                    request_count = 0
                    max_tokens_per_request = 0
                metrics = MockMetrics()

            # [COMMON PATH] Metrics Calculation
            try:
                extracted_rows = len(df)
                gt_file = os.path.join(tables_dir, item['file'])
                gt_df = pd.read_csv(gt_file)
                
                primary_columns = item['keys']
                keys_type = item['keys_type']
                date_columns = item['dateColumns']
                epsilons = item['epsilons']
                
                (kr, kp, kf1, nkr, nkp, nkf1, r, p, f1, rnka) = evaluate_table_metrics(
                    df, gt_df, 
                    primary_columns, keys_type, date_columns, epsilons
                )
                
                # If generated new, save it. If loaded, no need to save unless we refined it (we didn't).
                if not os.path.exists(out_file) and not df.empty:
                    df.to_csv(out_file, index=False)
                
                # [NEW] Artifact Persistence: Cost & Metrics (Standardized)
                req_count = metrics.request_count if hasattr(metrics, 'request_count') and metrics.request_count > 0 else 1
                avg_tokens = metrics.total_tokens / req_count if hasattr(metrics, 'total_tokens') else 0
                
                cost_artifact = {
                    "query_id": table_name,
                    "duration_seconds": duration,
                    "total_cost_usd": metrics.cost if hasattr(metrics, 'cost') else 0.0,
                    "total_tokens": metrics.total_tokens if hasattr(metrics, 'total_tokens') else 0,
                    "input_tokens": metrics.input_tokens if hasattr(metrics, 'input_tokens') else 0,
                    "output_tokens": metrics.output_tokens if hasattr(metrics, 'output_tokens') else 0,
                    "request_count": metrics.request_count if hasattr(metrics, 'request_count') else 0,
                    "avg_tokens_per_prompt": avg_tokens,
                    "max_tokens_per_prompt": metrics.max_tokens_per_request if hasattr(metrics, 'max_tokens_per_request') else 0
                }
                
                with open(os.path.join(table_dir, "cost.json"), "w") as f:
                    json.dump(cost_artifact, f, indent=2)

                msg = (f"-> {table_name}: Extracted {extracted_rows} rows. Time: {duration:.2f}s\n"
                       f"   Cell F1: {f1:.4f} (Prec: {p:.4f}, Rec: {r:.4f})\n"
                       f"   Key F1: {kf1:.4f} (Prec: {kp:.4f}, Rec: {kr:.4f})")
                print(msg)
                
                return {
                    "Table": table_name,
                    "Category": item.get('category', 'Unknown'),
                    "GT_Rows": len(gt_df),
                    "Extracted_Rows": extracted_rows,
                    "Cell_Precision": p,
                    "Cell_Recall": r,
                    "Cell_F1": f1,
                    "Key_Precision": kp,
                    "Key_Recall": kr,
                    "Key_F1": kf1,
                    "Non_Key_Precision": nkp,
                    "Non_Key_Recall": nkr,
                    "Non_Key_F1": nkf1,
                    "Subqueries": subqueries,
                    "Time_s": duration,
                    "Cost_USD": metrics.cost,
                    "Total_Tokens": metrics.total_tokens if hasattr(metrics, 'total_tokens') else 0,
                    "Input_Tokens": metrics.input_tokens if hasattr(metrics, 'input_tokens') else 0,
                    "Output_Tokens": metrics.output_tokens if hasattr(metrics, 'output_tokens') else 0,
                    "Request_Count": metrics.request_count if hasattr(metrics, 'request_count') else 0,
                    "Status": "Success"
                }
                    
            except Exception as e:
                print(f"-> Failed Metrics {table_name}: {e}")
                import traceback
                traceback.print_exc()
                return {
                    "Table": table_name,
                    "Category": item.get('category', 'Unknown'),
                    "Status": f"Error: {e}"
                }

    tasks = [process_table(item) for item in target_items]
    results_with_none = await asyncio.gather(*tasks)
    results = [r for r in results_with_none if r is not None]
                
    # Summary
    print("\n=== WIKITABGEN EVALUATION SUMMARY ===")
    summary_df = pd.DataFrame(results)
    if not summary_df.empty:
        cols_show = ["Table", "GT_Rows", "Extracted_Rows", "Cell_F1", "Key_F1", "Non_Key_F1", "Time_s", "Status"]
        print(summary_df[[c for c in cols_show if c in summary_df.columns]])
        
        summary_path = os.path.join(run_dir, "summary.csv")
        summary_df.to_csv(summary_path, index=False)
        print(f"\nSaved summary to {summary_path}")
        
        # Also save a version with Non_Key_F1 included
        if "Non_Key_F1" in summary_df.columns:
            summary_with_nonkey_path = os.path.join(run_dir, "summary_with_nonkey.csv")
            summary_df.to_csv(summary_with_nonkey_path, index=False)
            print(f"Saved summary with Non-Key F1 to {summary_with_nonkey_path}")
        
        # Calculate Averages and Totals
        numeric_cols = ["Cell_F1", "Key_F1", "Non_Key_F1", "Cell_Precision", "Cell_Recall", "Key_Precision", "Key_Recall", "Non_Key_Precision", "Non_Key_Recall", "Time_s", "Cost_USD"]
        valid_numeric = [c for c in numeric_cols if c in summary_df.columns]
        means = summary_df[valid_numeric].mean()
        print("\n--- Averages ---")
        print(means)
        
        # Save detailed metrics JSON
        metrics_path = os.path.join(run_dir, "metrics.json")
        metrics_dict = means.to_dict()
        
        # Add Totals
        metrics_dict["Model"] = args.model if args else "gpt-4o"
        metrics_dict["Total_Time_s"] = summary_df['Time_s'].sum() if 'Time_s' in summary_df.columns else 0.0
        
        if 'Cost_USD' in summary_df.columns:
            metrics_dict["Total_Cost_USD"] = summary_df['Cost_USD'].sum()
        
        # Add Token Totals
        if 'Total_Tokens' in summary_df.columns:
            metrics_dict["Total_Tokens"] = int(summary_df['Total_Tokens'].sum())
        if 'Input_Tokens' in summary_df.columns:
            metrics_dict["Total_Input_Tokens"] = int(summary_df['Input_Tokens'].sum())
        if 'Output_Tokens' in summary_df.columns:
            metrics_dict["Total_Output_Tokens"] = int(summary_df['Output_Tokens'].sum())
        if 'Request_Count' in summary_df.columns:
            metrics_dict["Total_Request_Count"] = int(summary_df['Request_Count'].sum())

        with open(metrics_path, "w") as f:
            json.dump(metrics_dict, f, indent=2)
        print(f"Saved aggregate metrics to {metrics_path}")

        if 'Cost_USD' in summary_df.columns:
            print("\n--- Totals ---")
            print(f"Total Cost: ${summary_df['Cost_USD'].sum():.4f}")
    else:
        print("No results to summarize.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    # Paths adjusted to assume running from project root
    parser.add_argument("--cfg_path", type=str, default="third_parties/WikiTabGen/benchmark/cfg.json")
    parser.add_argument("--tables_dir", type=str, default="third_parties/WikiTabGen/benchmark/tables")
    parser.add_argument("--base_output_dir", type=str, default="exp_results/runs_wikitabgen")
    parser.add_argument("--model", type=str, default="gpt-4o")
    parser.add_argument("--limit", type=int, default=3, help="Max number of tables to evaluate")
    parser.add_argument("--use_cache", action="store_true")
    parser.add_argument("--max_concurrent", type=int, default=10)
    parser.add_argument("--row_threshold", type=int, default=120)
    parser.add_argument("--decomposition_strategy", type=str, default="rule_based", choices=["rule_based", "llm_based", "none"], help="Decomposition strategy")
    parser.add_argument("--table_concurrent", type=int, default=5, help="Number of tables to process concurrently")
    parser.add_argument("--resume_dir", type=str, default=None, help="Directory to resume run from")
    parser.add_argument("--execution_mode", type=str, default="batch", choices=["batch", "row_by_row"], help="Execution mode")
    parser.add_argument("--table", type=str, help="Specific table name to run (overrides limit)")
    parser.add_argument("--source", type=str, default="Wikipedia", help="Data source for the extraction")
    parser.add_argument("--partition_selection_mode", type=str, default="llm", choices=["scoring", "llm"], help="Method for selecting partition key (scoring or llm)")
    parser.add_argument("--plan_only", action="store_true", help="Only run planning phase (skip data fetching)")
    parser.add_argument("--max_total_requests", type=int, default=20000, help="Global max total LLM requests limit")
    
    args = parser.parse_args()
    
    asyncio.run(evaluate_wikitabgen(
        cfg_path=args.cfg_path,
        tables_dir=args.tables_dir,
        base_output_dir=args.base_output_dir,
        args=args
    ))
