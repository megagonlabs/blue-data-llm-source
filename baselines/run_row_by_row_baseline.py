import argparse
import asyncio
import datetime
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import pandas as pd

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from experiments.evaluate_multisources_metrics import evaluate_table_metrics as evaluate_multisource_table
from experiments.run_mini_dataset import query_logging_context, setup_global_logging
from experiments.run_wikitabgen import evaluate_table_metrics as evaluate_wikitabgen_table
from utils.llm_utils import LLMClient, LLMMetrics, SharedCostTracker, sum_metrics


def _normalize_model_name(model: str) -> str:
    return model.replace("/", "_").replace(" ", "_")


def _default_base_output_dir(dataset: str) -> str:
    dataset_dirs = {
        "wikitabgen": "exp_results/wikitabgen_dataset",
        "multisources_dataset_v1": "exp_results/multisources_dataset_v1",
    }
    return dataset_dirs[dataset]


def _f1(precision: float, recall: float) -> float:
    return 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0


@dataclass
class BaselineTask:
    dataset_name: str
    task_id: str
    table_name: str
    category: str
    query: str
    columns: List[str]
    primary_keys: List[str]
    gt_path: str
    eval_info: Dict[str, Any]


class TableGeneratorJSON:
    SYSTEM_MSG = "You are a retriever of facts."

    KEYS_TEMPLATE = """
    We want to create a table with the detailed information about %s.
    %s.
    List all %s entities for the table.
    The response will be formatted as JSON list shown below.

    RESPONSE FORMAT:
    [{
        %s
    }]
    """

    ROW_TEMPLATE = """
    We want to create a table with the detailed information about %s.
    Columns in the table are %s.
    %s.
    Retrieve a single row whose key is %s.
    The response will be formatted as JSON dictionary shown below.
    Pay special attention to wrap all property names and values in double quotes!

    RESPONSE FORMAT:
    {
        %s
    }
    """

    ROW_TEMPLATE_WITH_CONFIDENCE = """
    We want to create a table with the detailed information about %s.
    Columns in the table are %s.
    %s.
    Retrieve a single row whose key is %s.
    The response will be formatted as JSON dictionary shown below.
    Pay special attention to wrap all property names and values in double quotes!

    Also include a "_confidence" key whose value is a JSON object mapping each
    non-key column name to a float between 0.0 and 1.0 representing your confidence
    in that value (1.0 = certain, 0.7 = fairly confident, 0.5 = uncertain, 0.0 = unknown).
    Do NOT include confidence scores for the key column(s).

    RESPONSE FORMAT:
    {
        %s,
        "_confidence": {"non_key_field": 0.9, ...}
    }
    """

    def _norm_field(self, value: str) -> str:
        value = (
            value.lower()
            .replace(" ", "_")
            .replace("-", "_")
            .replace(".", "")
            .replace(",", "_")
            .replace("(", "")
            .replace(")", "")
            .replace(":", "")
            .replace('"', "")
            .replace("'", "")
            .replace("/", "")
        )
        return re.sub(r"_+", "_", value)

    def _key_columns(self, keys: List[str]) -> str:
        if len(keys) == 1:
            return f"The key column in the table is {keys[0]}"
        return f"The key columns in the table are {', '.join(keys)}"

    def generate_keys_prompt(self, query: str, keys: List[str]) -> str:
        normalized_keys = [self._norm_field(k) for k in keys]
        response_format = ", ".join(f'"{key}": "{key}"' for key in normalized_keys)
        return self.KEYS_TEMPLATE % (
            query,
            self._key_columns(normalized_keys),
            ", ".join(normalized_keys),
            response_format,
        )

    def generate_row_prompt(
        self,
        query: str,
        keys: List[str],
        fields: List[str],
        fetched_key: Dict[str, Any],
        with_confidence: bool = False,
    ) -> str:
        normalized_keys = [self._norm_field(k) for k in keys]
        normalized_fields = [self._norm_field(f) for f in fields]
        key_json = []
        fields_json = []
        for field in normalized_fields:
            if field in fetched_key:
                key_json.append(f"{field} = {fetched_key[field]}")
            field_value = fetched_key.get(field, field)
            fields_json.append(f'"{field}": "{field_value}"')
        template = self.ROW_TEMPLATE_WITH_CONFIDENCE if with_confidence else self.ROW_TEMPLATE
        return template % (
            query,
            ", ".join(normalized_fields),
            self._key_columns(normalized_keys),
            f"({', '.join(key_json)})",
            ", ".join(fields_json),
        )

    def _extract_json_payload(self, response: str, opening: str, closing: str) -> Optional[str]:
        if not response:
            return None
        if opening not in response or closing not in response:
            return None
        start = response.find(opening)
        end = response.rfind(closing)
        if start == -1 or end == -1 or end < start:
            return None
        return response[start:end + 1]

    def parse_keys_response(self, response: str, keys: List[str]) -> List[Dict[str, Any]]:
        response_json: Any
        payload = self._extract_json_payload(response.strip(), "[", "]")
        if payload is None and "{" in response and "}" in response:
            payload = f"[{self._extract_json_payload(response.strip(), '{', '}')}]"

        try:
            response_json = json.loads(payload if payload is not None else response)
            if isinstance(response_json, dict) and len(response_json.keys()) == 1:
                response_json = list(response_json.values())[0]
        except Exception:
            response_json = []
            for match in re.finditer(r"\{([^{}]+)\}", response):
                attrs = {}
                for attr in match.group(1).split(","):
                    if ":" not in attr:
                        continue
                    key, value = attr.split(":", 1)
                    attrs[key.replace('"', "").strip()] = value.replace('"', "").strip()
                if attrs:
                    response_json.append(attrs)

        if isinstance(response_json, dict):
            response_json = [response_json]
        if not isinstance(response_json, list):
            response_json = []

        normalized_keys = [self._norm_field(key) for key in keys]
        parsed_keys = []
        for item in response_json:
            if isinstance(item, dict):
                parsed_keys.append({key: item.get(key, "") for key in normalized_keys})
            else:
                parsed_keys.append({normalized_keys[0]: item})
        return parsed_keys

    def parse_row_response(self, response: str) -> tuple[Dict[str, Any], Optional[Dict[str, float]]]:
        """
        Returns (row_dict, confidence_dict | None).
        confidence_dict is extracted from the "_confidence" key and removed from row_dict.
        """
        payload = self._extract_json_payload(response.strip(), "{", "}")
        row = json.loads(payload if payload is not None else response)
        confidence = None
        if isinstance(row, dict) and "_confidence" in row:
            raw_conf = row.pop("_confidence")
            if isinstance(raw_conf, dict):
                confidence = {k: float(v) for k, v in raw_conf.items()
                              if isinstance(v, (int, float))}
        return row, confidence

    def create_dataframe(
        self,
        rows: List[Dict[str, Any]],
        columns: List[str],
        keys: List[str],
        df_ref: pd.DataFrame,
    ) -> pd.DataFrame:
        if not rows:
            return pd.DataFrame(columns=df_ref.columns)

        df = pd.DataFrame.from_dict(rows)
        normalized_columns = [self._norm_field(col) for col in columns]
        for column in normalized_columns:
            if column not in df.columns:
                df[column] = ""
        df = df[normalized_columns]
        df.columns = df_ref.columns
        return df.drop_duplicates(subset=keys)


class RowByRowRunner:
    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.generator = TableGeneratorJSON()
        shared_state = {
            "rate_limit_times": [],
            "rate_limit_lock": asyncio.Lock(),
            "semaphore": asyncio.Semaphore(args.max_concurrent),
            "cost_tracker": SharedCostTracker(
                max_total_requests=args.max_total_requests,
                max_total_cost=args.max_total_cost,
            ),
        }
        self.llm = LLMClient(
            model=args.model,
            max_concurrent=args.max_concurrent,
            default_use_cache=args.use_cache,
            max_total_requests=args.max_total_requests,
            max_total_cost=args.max_total_cost,
            shared_state=shared_state,
        )
        self.shared_state = shared_state

    def _task_dir(self, run_dir: str, task: BaselineTask) -> str:
        return os.path.join(run_dir, task.table_name)

    async def _generate_keys(
        self,
        task: BaselineTask,
        trace: Dict[str, Any],
    ) -> tuple[List[Dict[str, Any]], LLMMetrics]:
        if self.args.oracle_keys:
            gt_df = pd.read_csv(task.gt_path)
            keys = gt_df[task.primary_keys].drop_duplicates()
            normalized_primary_keys = [self.generator._norm_field(key) for key in task.primary_keys]
            key_rows = []
            for _, row in keys.iterrows():
                key_rows.append(
                    {
                        self.generator._norm_field(column): ("" if pd.isna(value) else str(value))
                        for column, value in row.items()
                    }
                )
            trace["oracle_keys"] = True
            trace["oracle_key_count"] = len(key_rows)
            return key_rows, LLMMetrics()

        prompt = self.generator.generate_keys_prompt(task.query, task.primary_keys)
        response = await self.llm.agenerate(
            prompt,
            system=self.generator.SYSTEM_MSG,
            temperature=0.0,
            use_cache=self.args.use_cache,
            cache_refresh=self.args.cache_refresh,
        )
        parsed = self.generator.parse_keys_response(response.content, task.primary_keys)
        trace["keys_prompt"] = prompt
        trace["keys_response"] = response.content
        trace["parsed_keys_count"] = len(parsed)
        return parsed, response.metrics

    def _fallback_row(self, columns: List[str], key_instance: Dict[str, Any]) -> Dict[str, Any]:
        fallback = {}
        for column in [self.generator._norm_field(col) for col in columns]:
            fallback[column] = key_instance.get(column, "failed")
        return fallback

    async def process_task(self, task: BaselineTask, run_dir: str) -> Dict[str, Any]:
        task_dir = self._task_dir(run_dir, task)
        os.makedirs(task_dir, exist_ok=True)
        output_csv = os.path.join(task_dir, f"{task.table_name}.csv")
        trace_path = os.path.join(task_dir, "trace.json")

        if os.path.exists(output_csv) and not self.args.overwrite:
            df = pd.read_csv(output_csv)
            metrics = LLMMetrics()
            trace = {"resumed": True}
        else:
            gt_df = pd.read_csv(task.gt_path)
            trace = {
                "dataset_name": task.dataset_name,
                "table_name": task.table_name,
                "query": task.query,
                "columns": task.columns,
                "primary_keys": task.primary_keys,
                "row_artifacts": [],
            }

            metrics_list: List[LLMMetrics] = []
            key_instances, key_metrics = await self._generate_keys(task, trace)
            metrics_list.append(key_metrics)
            rows: List[Dict[str, Any]] = []
            confidence_rows: List[Dict[str, Any]] = []
            output_confidence = getattr(self.args, "output_confidence", False)

            for index, key_instance in enumerate(key_instances):
                row_prompt = self.generator.generate_row_prompt(
                    task.query,
                    task.primary_keys,
                    task.columns,
                    key_instance,
                    with_confidence=output_confidence,
                )
                row_artifact = {
                    "index": index,
                    "key_instance": key_instance,
                    "row_prompt": row_prompt,
                }
                try:
                    response = await self.llm.agenerate(
                        row_prompt,
                        system=self.generator.SYSTEM_MSG,
                        temperature=0.0,
                        use_cache=self.args.use_cache,
                        cache_refresh=self.args.cache_refresh,
                    )
                    metrics_list.append(response.metrics)
                    row_artifact["row_response"] = response.content
                    parsed_row, conf_dict = self.generator.parse_row_response(response.content)
                    row_artifact["parsed_ok"] = True
                    if output_confidence and conf_dict:
                        # Add key columns so the confidence row can be aligned
                        conf_row = {self.generator._norm_field(k): key_instance.get(self.generator._norm_field(k), "")
                                    for k in task.primary_keys}
                        conf_row.update(conf_dict)
                        confidence_rows.append(conf_row)
                except Exception as exc:
                    row_artifact["parsed_ok"] = False
                    row_artifact["error"] = f"{type(exc).__name__}: {exc}"
                    parsed_row = self._fallback_row(task.columns, key_instance)
                trace["row_artifacts"].append(row_artifact)
                rows.append(parsed_row)

            df = self.generator.create_dataframe(rows, task.columns, task.primary_keys, gt_df)
            df.to_csv(output_csv, index=False)

            if output_confidence and confidence_rows:
                conf_df = pd.DataFrame(confidence_rows)
                conf_csv = os.path.join(task_dir, f"{task.table_name}_confidence.csv")
                conf_df.to_csv(conf_csv, index=False)
            metrics = sum_metrics(metrics_list) if metrics_list else LLMMetrics()
            trace["metrics"] = metrics.to_dict()
            trace["output_csv"] = output_csv
            with open(trace_path, "w") as f:
                json.dump(trace, f, indent=2)

        evaluation = self.evaluate_task(task, df)

        cost_path = os.path.join(task_dir, "cost.json")
        with open(cost_path, "w") as f:
            json.dump(
                {
                    "request_count": metrics.request_count,
                    "input_tokens": metrics.input_tokens,
                    "output_tokens": metrics.output_tokens,
                    "total_tokens": metrics.total_tokens,
                    "cost": metrics.cost,
                    "max_tokens_per_request": metrics.max_tokens_per_request,
                },
                f,
                indent=2,
            )

        return {
            "Table": task.table_name,
            "Category": task.category,
            "GT_Rows": evaluation["GT_Rows"],
            "Extracted_Rows": evaluation["Extracted_Rows"],
            "Cell_Precision": evaluation["Cell_Precision"],
            "Cell_Recall": evaluation["Cell_Recall"],
            "Cell_F1": evaluation["Cell_F1"],
            "Key_Precision": evaluation["Key_Precision"],
            "Key_Recall": evaluation["Key_Recall"],
            "Key_F1": evaluation["Key_F1"],
            "Non_Key_Precision": evaluation["Non_Key_Precision"],
            "Non_Key_Recall": evaluation["Non_Key_Recall"],
            "Non_Key_F1": evaluation["Non_Key_F1"],
            "Time_s": evaluation["Time_s"],
            "Cost_USD": metrics.cost,
            "Request_Count": metrics.request_count,
            "Input_Tokens": metrics.input_tokens,
            "Output_Tokens": metrics.output_tokens,
            "Total_Tokens": metrics.total_tokens,
            "Status": "Success",
            **evaluation["type_metrics"],
        }

    def evaluate_task(self, task: BaselineTask, df: pd.DataFrame) -> Dict[str, Any]:
        gt_df = pd.read_csv(task.gt_path)
        started = time.time()
        if task.dataset_name == "wikitabgen":
            (
                kr,
                kp,
                kf1,
                nkr,
                nkp,
                nkf1,
                r,
                p,
                f1,
                _rnka,
                type_f1,
            ) = evaluate_wikitabgen_table(
                df.copy(),
                gt_df,
                task.primary_keys,
                task.eval_info["keys_type"],
                task.eval_info["date_columns"],
                task.eval_info["epsilons"],
                task.eval_info.get("numeric_non_keys", []),
            )
        else:
            (
                kr,
                kp,
                kf1,
                nkr,
                nkp,
                nkf1,
                r,
                p,
                f1,
                _rnka,
                type_f1,
            ) = evaluate_multisource_table(
                df.copy(),
                gt_df,
                task.primary_keys,
                task.eval_info["date_columns"],
                task.eval_info["numeric_columns"],
                column_types=task.eval_info.get("column_types"),
                epsilon=self.args.epsilon,
                fuzzy_match=not self.args.no_fuzzy_match,
            )

        type_metrics = {}
        for metric_name, metric_value in type_f1.items():
            type_metrics[f"F1_{metric_name}"] = metric_value["f1"]
            type_metrics[f"Precision_{metric_name}"] = metric_value["precision"]
            type_metrics[f"Recall_{metric_name}"] = metric_value["recall"]

        return {
            "GT_Rows": len(gt_df),
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
            "Time_s": time.time() - started,
            "type_metrics": type_metrics,
        }


def load_wikitabgen_tasks(cfg_path: str, tables_dir: str) -> List[BaselineTask]:
    with open(cfg_path, "r") as f:
        cfg = json.load(f)

    tasks = []
    for key in sorted(cfg.keys(), key=lambda value: int(value)):
        item = cfg[key]
        tasks.append(
            BaselineTask(
                dataset_name="wikitabgen",
                task_id=str(item["id"]),
                table_name=item["name"],
                category=item.get("category", "Unknown"),
                query=item["table_title"],
                columns=item["columns"],
                primary_keys=item["keys"],
                gt_path=os.path.join(tables_dir, item["file"]),
                eval_info={
                    "keys_type": item["keys_type"],
                    "date_columns": item.get("dateColumns", []),
                    "epsilons": item.get("epsilons", []),
                    "numeric_non_keys": item.get("numericNonKeys", []),
                },
            )
        )
    return tasks


def load_multisource_tasks(cfg_path: str, tables_dir: str) -> List[BaselineTask]:
    with open(cfg_path, "r") as f:
        cfg = json.load(f)

    tasks = []
    for item in cfg.values():
        column_types = item.get("column_types", {})
        tasks.append(
            BaselineTask(
                dataset_name="multisources_dataset_v1",
                task_id=item["id"],
                table_name=item["id"],
                category=item.get("category", "Unknown"),
                query=item["table_title"],
                columns=item["columns"],
                primary_keys=item["primary_key"],
                gt_path=os.path.join(tables_dir, f"{item['id']}.csv"),
                eval_info={
                    "column_types": column_types,
                    "date_columns": [column for column, kind in column_types.items() if kind == "date"],
                    "numeric_columns": [column for column, kind in column_types.items() if kind in ("numeric", "rank")],
                },
            )
        )
    return tasks


def summarize_run(run_dir: str, summary_rows: List[Dict[str, Any]], args: argparse.Namespace) -> None:
    summary_df = pd.DataFrame(summary_rows)
    summary_path = os.path.join(run_dir, "summary.csv")
    summary_df.to_csv(summary_path, index=False)

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
    means = summary_df[numeric_cols].mean(numeric_only=True).to_dict()
    means["Total_Cost_USD"] = float(summary_df["Cost_USD"].sum())
    means["Total_Requests"] = int(summary_df["Request_Count"].sum())
    means["Total_Input_Tokens"] = int(summary_df["Input_Tokens"].sum())
    means["Total_Output_Tokens"] = int(summary_df["Output_Tokens"].sum())
    means["Total_Tokens"] = int(summary_df["Total_Tokens"].sum())
    means["dataset"] = args.dataset
    means["model"] = args.model
    means["oracle_keys"] = args.oracle_keys

    type_f1_cols = [column for column in summary_df.columns if column.startswith("F1_")]
    if type_f1_cols:
        means["column_type_f1"] = {
            column[len("F1_"):]: round(float(summary_df[column].dropna().mean()), 6)
            for column in sorted(type_f1_cols)
            if not summary_df[column].dropna().empty
        }

    with open(os.path.join(run_dir, "metrics.json"), "w") as f:
        json.dump(means, f, indent=2)


async def main(args: argparse.Namespace) -> None:
    now = datetime.datetime.now()
    date_str = now.strftime("%Y%m%d")
    time_str = now.strftime("%H%M%S")
    base_output_dir = args.base_output_dir or _default_base_output_dir(args.dataset)
    run_name = f"row_by_row_{_normalize_model_name(args.model)}_{date_str}_{time_str}"
    run_dir = args.resume_dir or os.path.join(base_output_dir, run_name)
    os.makedirs(run_dir, exist_ok=True)
    setup_global_logging(run_dir)

    with open(os.path.join(run_dir, "run_config.json"), "w") as f:
        json.dump(vars(args), f, indent=2)

    if args.dataset == "wikitabgen":
        tasks = load_wikitabgen_tasks(args.cfg_path, args.tables_dir)
    else:
        tasks = load_multisource_tasks(args.cfg_path, args.tables_dir)

    if args.table:
        tasks = [task for task in tasks if task.table_name == args.table]
    if args.limit:
        tasks = tasks[:args.limit]

    runner = RowByRowRunner(args)
    table_semaphore = asyncio.Semaphore(args.table_concurrent)
    results: List[Dict[str, Any]] = []

    async def _wrapped(task: BaselineTask) -> Optional[Dict[str, Any]]:
        async with table_semaphore:
            table_dir = os.path.join(run_dir, task.table_name)
            os.makedirs(table_dir, exist_ok=True)
            print(f"\n--- Processing {task.table_name} ---")
            started = time.time()
            with query_logging_context(table_dir, "process"):
                try:
                    result = await runner.process_task(task, run_dir)
                    result["Time_s"] = time.time() - started
                    print(
                        f"-> {task.table_name}: Cell F1={result['Cell_F1']:.4f} "
                        f"Key F1={result['Key_F1']:.4f} Cost=${result['Cost_USD']:.4f} "
                        f"Requests={result['Request_Count']}"
                    )
                    return result
                except Exception as exc:
                    import traceback

                    traceback.print_exc()
                    print(f"-> Failed {task.table_name}: {exc}")
                    return {
                        "Table": task.table_name,
                        "Category": task.category,
                        "Status": f"Error: {exc}",
                    }

    for result in await asyncio.gather(*[_wrapped(task) for task in tasks]):
        if result is not None:
            results.append(result)
            summary_rows = [row for row in results if row.get("Status") == "Success"]
            if summary_rows:
                summarize_run(run_dir, summary_rows, args)

    summary_rows = [row for row in results if row.get("Status") == "Success"]
    if summary_rows:
        summarize_run(run_dir, summary_rows, args)
        print(pd.DataFrame(summary_rows)[["Table", "Cell_F1", "Key_F1", "Non_Key_F1", "Cost_USD", "Request_Count"]])
    else:
        print("No successful tables to summarize.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the WikiTabGen-style row-by-row baseline on supported datasets.")
    parser.add_argument("--dataset", choices=["wikitabgen", "multisources_dataset_v1"], required=True)
    parser.add_argument("--cfg_path", type=str, required=True)
    parser.add_argument("--tables_dir", type=str, required=True)
    parser.add_argument("--base_output_dir", type=str, default=None)
    parser.add_argument("--model", type=str, default="gpt-4o")
    parser.add_argument("--table", type=str, default=None)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--table_concurrent", type=int, default=1)
    parser.add_argument("--max_concurrent", type=int, default=16)
    parser.add_argument("--max_total_requests", type=int, default=20000)
    parser.add_argument("--max_total_cost", type=float, default=200.0)
    parser.add_argument("--use_cache", action="store_true")
    parser.add_argument("--cache_refresh", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--resume_dir", type=str, default=None)
    parser.add_argument("--oracle_keys", action="store_true")
    parser.add_argument("--epsilon", type=float, default=0.01)
    parser.add_argument("--no_fuzzy_match", action="store_true")
    parser.add_argument("--output_confidence", action="store_true",
                        help="Ask LLM to output per-cell confidence scores; saves <table>_confidence.csv")
    asyncio.run(main(parser.parse_args()))
