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

from src.interface import PKDB
from src.schema import SchemaDefinition, Column
from experiments.run_mini_dataset import setup_global_logging, query_logging_context

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
    # Robust row finding
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
    
    # Convert lists to sets for O(1) lookup? 
    # But we need row index.
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
    print(f"Loading config from {cfg_path}...")
    with open(cfg_path, 'r') as f:
        cfg = json.load(f)
        
    # Convert to list for easier handling (it's dict of "0": {}, "1": {}...)
    cfg_items = [cfg[k] for k in sorted(cfg.keys(), key=lambda x: int(x))]
    
    # Filter
    limit = args.limit if args and args.limit > 0 else len(cfg_items)
    target_items = cfg_items[:limit]
    
    print(f"Selected {len(target_items)} tables for evaluation.")
    
    # Save Run Config
    run_config = {
        "timestamp": timestamp,
        "model": args.model if args else "gpt-4o",
        "limit": limit,
        "use_cache": args.use_cache if args else False,
        "max_concurrent": args.max_concurrent if args else 10,
        "row_threshold": args.row_threshold if args else 50,
        "decomposition_strategy": args.decomposition_strategy,
        "execution_mode": args.execution_mode if args else "batch",
        "generated_by": "experiments/run_wikitabgen.py"
    }
    with open(os.path.join(run_dir, "run_config.json"), "w") as f:
        json.dump(run_config, f, indent=4)
    
    # 3. Initialize PKDB
    # 3. Initialize Shared State for Rate Limiting
    print("Initializing Shared Resources...")
    # These must be shared across all PKDB instances to prevent 429 errors
    shared_state = {
        "rate_limit_times": [],
        "rate_limit_lock": asyncio.Lock(),
        "semaphore": asyncio.Semaphore(100) # Global concurrency limit for LLM calls implies max_concurrent * tables
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
                default_execution_mode=args.execution_mode if args else "batch"
            )
            
            pkdb = PKDB(
                config=cfg,
                shared_state=shared_state
            )
            
            # [RESUME] Check if result already exists
            out_file = os.path.join(run_dir, f"{table_name}.csv")
            df = None
            if os.path.exists(out_file):
                print(f"LOAD {table_name}: Result already exists. Re-calculating metrics.")
                try:
                    df = pd.read_csv(out_file)
                except Exception as e:
                    print(f"Error loading {table_name}: {e}. Re-running.")
            
            if df is None:
                # ... (Normal Generation Logic) ...
                title = item.get('table_title', table_name)
                # ...
                
                # Construct STRICT SCHEMA
                cols = []
                numeric_cols = set(item.get('numericNonKeys', []))
                keys = set(item.get('keys', []))
                
                for c_name in item['columns']:
                    sem_type = "numerical" if c_name in numeric_cols else "text"
                    is_pk = c_name in keys
                    cols.append(Column(name=c_name, semantic_type=sem_type, is_partition_key=is_pk))
                
                target_schema = SchemaDefinition(
                    table_name=table_name,
                    columns=cols,
                    primary_keys=item.get('keys', [])
                )
                
                task_query = f"Generate a detailed table about {title}"
                
                print(f"\n--- Processing: {table_name} ---")
                start = time.time()
                try:
                    with query_logging_context(run_dir, table_name):
                        df, metrics, subqueries = await pkdb.extract_table(task_query, schema=target_schema)
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
                if not os.path.exists(out_file):
                    df.to_csv(out_file, index=False)
                
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
                    "Subqueries": subqueries,
                    "Time_s": duration,
                    "Cost_USD": metrics.cost,
                    "Status": "Success"
                }
                    
            except Exception as e:
                print(f"-> Failed Metrics {table_name}: {e}")
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
        cols_show = ["Table", "GT_Rows", "Extracted_Rows", "Cell_F1", "Key_F1", "Time_s", "Status"]
        print(summary_df[[c for c in cols_show if c in summary_df.columns]])
        
        summary_path = os.path.join(run_dir, "summary.csv")
        summary_df.to_csv(summary_path, index=False)
        print(f"\nSaved summary to {summary_path}")
        
        # Calculate Averages and Totals
        numeric_cols = ["Cell_F1", "Key_F1", "Cell_Precision", "Cell_Recall", "Key_Precision", "Key_Recall", "Time_s", "Cost_USD"]
        print("\n--- Averages ---")
        print(summary_df[numeric_cols].mean())
        
        print("\n--- Totals ---")
        print(f"Total Cost: ${summary_df['Cost_USD'].sum():.4f}")
    else:
        print("No results to summarize.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    # Paths adjusted to assume running from project root
    parser.add_argument("--cfg_path", type=str, default="third_parties/WikiTabGen/benchmark/cfg.json")
    parser.add_argument("--tables_dir", type=str, default="third_parties/WikiTabGen/benchmark/tables")
    parser.add_argument("--base_output_dir", type=str, default="experiments/runs_wikitabgen")
    parser.add_argument("--model", type=str, default="gpt-4o")
    parser.add_argument("--limit", type=int, default=3, help="Max number of tables to evaluate")
    parser.add_argument("--use_cache", action="store_true")
    parser.add_argument("--max_concurrent", type=int, default=10)
    parser.add_argument("--row_threshold", type=int, default=120)
    parser.add_argument("--decomposition_strategy", type=str, default="rule_based", choices=["rule_based", "llm_based"])
    parser.add_argument("--table_concurrent", type=int, default=5, help="Number of tables to process concurrently")
    parser.add_argument("--resume_dir", type=str, default=None, help="Directory to resume run from")
    parser.add_argument("--execution_mode", type=str, default="batch", choices=["batch", "row_by_row"], help="Execution mode")
    
    args = parser.parse_args()
    
    asyncio.run(evaluate_wikitabgen(
        cfg_path=args.cfg_path,
        tables_dir=args.tables_dir,
        base_output_dir=args.base_output_dir,
        args=args
    ))
