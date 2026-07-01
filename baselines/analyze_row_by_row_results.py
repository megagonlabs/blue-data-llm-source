import argparse
import json
import os
import sys
from typing import Any, Dict, List

import pandas as pd

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from experiments.evaluate_multisources_metrics import evaluate_table_metrics as eval_multisource
from experiments.run_wikitabgen import evaluate_table_metrics as eval_wikitabgen


REQUEST_MARKER = "LiteLLM completion() model="


def _flatten_type_metrics(type_f1: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    flat: Dict[str, Any] = {}
    for metric_name, metric_value in type_f1.items():
        flat[f"F1_{metric_name}"] = metric_value["f1"]
        flat[f"Precision_{metric_name}"] = metric_value["precision"]
        flat[f"Recall_{metric_name}"] = metric_value["recall"]
    return flat


def _load_cost(cost_path: str) -> Dict[str, Any]:
    return json.load(open(cost_path)) if os.path.exists(cost_path) else {}


def _build_metrics_payload(summary_df: pd.DataFrame, dataset: str, model: str, oracle_keys: bool) -> Dict[str, Any]:
    numeric_cols = [
        "Cell_Precision",
        "Cell_Recall",
        "Cell_F1",
        "Key_Precision",
        "Key_Recall",
        "Key_F1",
        "Non_Key_Precision",
        "Non_Key_Recall",
        "Non_Key_F1",
        "Time_s",
        "Cost_USD",
        "Request_Count",
        "Input_Tokens",
        "Output_Tokens",
        "Total_Tokens",
    ]
    means = summary_df[[col for col in numeric_cols if col in summary_df.columns]].mean(numeric_only=True).to_dict()
    means["Total_Cost_USD"] = float(summary_df["Cost_USD"].sum()) if "Cost_USD" in summary_df.columns else 0.0
    means["Total_Requests"] = int(summary_df["Request_Count"].sum()) if "Request_Count" in summary_df.columns else 0
    means["Total_Input_Tokens"] = int(summary_df["Input_Tokens"].sum()) if "Input_Tokens" in summary_df.columns else 0
    means["Total_Output_Tokens"] = int(summary_df["Output_Tokens"].sum()) if "Output_Tokens" in summary_df.columns else 0
    means["Total_Tokens"] = int(summary_df["Total_Tokens"].sum()) if "Total_Tokens" in summary_df.columns else 0
    means["dataset"] = dataset
    means["model"] = model
    means["oracle_keys"] = oracle_keys

    type_f1_cols = [column for column in summary_df.columns if column.startswith("F1_")]
    if type_f1_cols:
        means["column_type_f1"] = {
            column[len("F1_"):]: round(float(summary_df[column].dropna().mean()), 6)
            for column in sorted(type_f1_cols)
            if not summary_df[column].dropna().empty
        }

    return means


def write_run_artifacts(run_dir: str, completed_df: pd.DataFrame, dataset: str, model: str, oracle_keys: bool) -> None:
    summary_df = completed_df.drop(columns=["Status", "Attempt_Log_Count"], errors="ignore").copy()
    summary_df.to_csv(os.path.join(run_dir, "summary.csv"), index=False)
    metrics_payload = _build_metrics_payload(summary_df, dataset, model, oracle_keys)
    with open(os.path.join(run_dir, "metrics.json"), "w") as f:
        json.dump(metrics_payload, f, indent=2)


def load_wikitabgen_rows(run_dir: str, cfg_path: str, tables_dir: str) -> pd.DataFrame:
    cfg = json.load(open(cfg_path))
    rows: List[Dict] = []
    for key in sorted(cfg, key=lambda value: int(value)):
        item = cfg[key]
        table = item["name"]
        out = os.path.join(run_dir, table, f"{table}.csv")
        process_log = os.path.join(run_dir, table, "process.log")
        attempt_count = open(process_log).read().count(REQUEST_MARKER) if os.path.exists(process_log) else 0
        if os.path.exists(out):
            df = pd.read_csv(out)
            gt = pd.read_csv(os.path.join(tables_dir, item["file"]))
            kr, kp, kf1, nkr, nkp, nkf1, r, p, f1, rnka, type_f1 = eval_wikitabgen(
                df.copy(),
                gt,
                item["keys"],
                item["keys_type"],
                item.get("dateColumns", []),
                item.get("epsilons", []),
                item.get("numericNonKeys", []),
            )
            cost_path = os.path.join(run_dir, table, "cost.json")
            cost = _load_cost(cost_path)
            rows.append(
                {
                    "Table": table,
                    "Category": item.get("category", "Unknown"),
                    "GT_Rows": len(gt),
                    "Extracted_Rows": len(df),
                    "Cell_Precision": p,
                    "Cell_Recall": r,
                    "Cell_F1": f1,
                    "Key_Precision": kp,
                    "Key_Recall": kr,
                    "Key_F1": kf1,
                    "Non_Key_Precision": nkp,
                    "Non_Key_Recall": nkr,
                    "Non_Key_F1": nkf1,
                    "Time_s": 0.0,
                    "Cost_USD": cost.get("cost", 0.0),
                    "Request_Count": cost.get("request_count", 0),
                    "Input_Tokens": cost.get("input_tokens", 0),
                    "Output_Tokens": cost.get("output_tokens", 0),
                    "Total_Tokens": cost.get("total_tokens", 0),
                    "Rows": len(df),
                    "Requests": cost.get("request_count", 0),
                    "Status": "completed",
                    "Attempt_Log_Count": attempt_count,
                    **_flatten_type_metrics(type_f1),
                }
            )
        else:
            rows.append(
                {
                    "Table": table,
                    "Category": item.get("category", "Unknown"),
                    "Status": "missing",
                    "GT_Rows": len(pd.read_csv(os.path.join(tables_dir, item["file"]))),
                    "Extracted_Rows": 0,
                    "Cell_Precision": 0.0,
                    "Cell_Recall": 0.0,
                    "Cell_F1": 0.0,
                    "Key_Precision": 0.0,
                    "Key_Recall": 0.0,
                    "Key_F1": 0.0,
                    "Non_Key_Precision": 0.0,
                    "Non_Key_Recall": 0.0,
                    "Non_Key_F1": 0.0,
                    "Time_s": 0.0,
                    "Cost_USD": 0.0,
                    "Request_Count": 0,
                    "Input_Tokens": 0,
                    "Output_Tokens": 0,
                    "Total_Tokens": 0,
                    "Rows": 0,
                    "Requests": 0,
                    "Attempt_Log_Count": attempt_count,
                }
            )
    return pd.DataFrame(rows)


def load_multisource_rows(run_dir: str, cfg_path: str, tables_dir: str) -> pd.DataFrame:
    cfg = json.load(open(cfg_path))
    rows: List[Dict] = []
    for item in cfg.values():
        table = item["id"]
        out = os.path.join(run_dir, table, f"{table}.csv")
        process_log = os.path.join(run_dir, table, "process.log")
        attempt_count = open(process_log).read().count(REQUEST_MARKER) if os.path.exists(process_log) else 0
        if os.path.exists(out):
            df = pd.read_csv(out)
            gt = pd.read_csv(os.path.join(tables_dir, f"{table}.csv"))
            column_types = item.get("column_types", {})
            date_cols = [column for column, kind in column_types.items() if kind == "date"]
            numeric_cols = [column for column, kind in column_types.items() if kind in ("numeric", "rank")]
            kr, kp, kf1, nkr, nkp, nkf1, r, p, f1, rnka, type_f1 = eval_multisource(
                df.copy(),
                gt,
                item["primary_key"],
                date_cols,
                numeric_cols,
                column_types=column_types,
                epsilon=0.01,
                fuzzy_match=True,
            )
            cost_path = os.path.join(run_dir, table, "cost.json")
            cost = _load_cost(cost_path)
            rows.append(
                {
                    "Table": table,
                    "Category": item.get("category", "Unknown"),
                    "GT_Rows": len(gt),
                    "Extracted_Rows": len(df),
                    "Cell_Precision": p,
                    "Cell_Recall": r,
                    "Cell_F1": f1,
                    "Key_Precision": kp,
                    "Key_Recall": kr,
                    "Key_F1": kf1,
                    "Non_Key_Precision": nkp,
                    "Non_Key_Recall": nkr,
                    "Non_Key_F1": nkf1,
                    "Time_s": 0.0,
                    "Cost_USD": cost.get("cost", 0.0),
                    "Request_Count": cost.get("request_count", 0),
                    "Input_Tokens": cost.get("input_tokens", 0),
                    "Output_Tokens": cost.get("output_tokens", 0),
                    "Total_Tokens": cost.get("total_tokens", 0),
                    "Rows": len(df),
                    "Requests": cost.get("request_count", 0),
                    "Status": "completed",
                    "Attempt_Log_Count": attempt_count,
                    **_flatten_type_metrics(type_f1),
                }
            )
        else:
            rows.append(
                {
                    "Table": table,
                    "Category": item.get("category", "Unknown"),
                    "Status": "missing",
                    "GT_Rows": len(pd.read_csv(os.path.join(tables_dir, f"{table}.csv"))),
                    "Extracted_Rows": 0,
                    "Cell_Precision": 0.0,
                    "Cell_Recall": 0.0,
                    "Cell_F1": 0.0,
                    "Key_Precision": 0.0,
                    "Key_Recall": 0.0,
                    "Key_F1": 0.0,
                    "Non_Key_Precision": 0.0,
                    "Non_Key_Recall": 0.0,
                    "Non_Key_F1": 0.0,
                    "Time_s": 0.0,
                    "Cost_USD": 0.0,
                    "Request_Count": 0,
                    "Input_Tokens": 0,
                    "Output_Tokens": 0,
                    "Total_Tokens": 0,
                    "Rows": 0,
                    "Requests": 0,
                    "Attempt_Log_Count": attempt_count,
                }
            )
    return pd.DataFrame(rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", choices=["wikitabgen", "multisources_dataset_v1"], required=True)
    parser.add_argument("--run_dir", required=True)
    parser.add_argument("--cfg_path", required=True)
    parser.add_argument("--tables_dir", required=True)
    parser.add_argument("--write_run_artifacts", action="store_true")
    args = parser.parse_args()

    if args.dataset == "wikitabgen":
        df = load_wikitabgen_rows(args.run_dir, args.cfg_path, args.tables_dir)
    else:
        df = load_multisource_rows(args.run_dir, args.cfg_path, args.tables_dir)

    completed = df[df["Status"] == "completed"]

    if args.write_run_artifacts and not completed.empty:
        run_config_path = os.path.join(args.run_dir, "run_config.json")
        run_config = json.load(open(run_config_path)) if os.path.exists(run_config_path) else {}
        write_run_artifacts(
            args.run_dir,
            completed,
            dataset=args.dataset,
            model=run_config.get("model", "unknown"),
            oracle_keys=bool(run_config.get("oracle_keys", False)),
        )

    print(f"completed={len(completed)} total={len(df)}")
    print("completed_only", completed[["Cell_F1", "Key_F1", "Non_Key_F1", "Cost_USD", "Requests"]].mean(numeric_only=True).to_dict())
    print("all_tables", df[["Cell_F1", "Key_F1", "Non_Key_F1"]].mean(numeric_only=True).to_dict())
    print("completed_totals", completed[["Cost_USD", "Requests"]].sum(numeric_only=True).to_dict())
    print("missing_attempt_log_count", int(df[df["Status"] == "missing"]["Attempt_Log_Count"].sum()))
    print("missing_tables", df[df["Status"] == "missing"]["Table"].tolist())
    print(df[["Table", "Status", "Cell_F1", "Key_F1", "Non_Key_F1", "Rows", "Requests", "Attempt_Log_Count"]].sort_values("Table").to_string(index=False))


if __name__ == "__main__":
    main()
