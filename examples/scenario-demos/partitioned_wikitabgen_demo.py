"""Compare flat, row-by-row, and partitioned retrieval on WikiTabGen query 15.

The query and columns are embedded; a reference CSV is optional. Run from the
blue-data-llm-source checkout root with OPENAI_API_KEY exported:

    python3 examples/scenario-demos/partitioned_wikitabgen_demo.py --live
"""

from __future__ import annotations

import argparse
import csv
import json
import os
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from blue_data_llm_source import (
    Budget,
    ConfigError,
    DefaultLlmClient,
    Limits,
    LlmSource,
    LlmSourceError,
)

QUERY = "Generate a detailed table about chemical elements"
ATOMIC_NUMBERS = list(range(1, 119))
ELEMENT_CONTEXT = (
    "Use the full modern periodic table. Return each element once, with its "
    "atomic number and period. The exact requested Z values are: "
    + ", ".join(str(number) for number in ATOMIC_NUMBERS)
    + "."
)
ATTRS = [
    "Z",
    "Sym",
    "Element",
    "Group",
    "Period",
    "Atomic weight u",
    "Density g / cm 3",
    "Melt K",
    "Boil K",
    "Heat J / g * K",
    "Neg",
]
FETCH_ATTRS = [
    {"name": name, "type": "integer"} if name in ("Z", "Period") else {"name": name}
    for name in ATTRS
]
MODES = ("flat", "row_by_row", "partitioned")
PERIOD_RULE = {
    "dimension": "Period",
    "ordered_primary_keys": ["Period", "Z"],
    "kind": "finite_domain",
    "allowed_domain": list(range(1, 8)),
    "parent_scope": {},
    "provenance": "source_configuration",
    "contract_version": "identity-partition-rule-1",
}
COMMON = {
    "llm.model": "gpt-6-luna",
    "llm.max_tokens": 4096,
    "llm.reasoning_effort": "none",
    "llm.cache": False,
    "llm.max_parallel": 4,
    "limits.max_retries": 0,
    "limits.max_rows": 130,
    "limits.max_calls": 160,
    "plan.keys": ["Z"],
    "plan.planning_order": "planning.combined",
    "plan.max_continuations": 0,
}
TOTAL_LIMITS = Limits(max_cost=0.30, max_calls=160, max_seconds=900, max_retries=0)


def properties_for(mode: str) -> dict[str, Any]:
    props = {**COMMON, "plan.mode": mode}
    if mode == "row_by_row":
        props["plan.max_leaves"] = 130
    elif mode == "partitioned":
        props.update(
            {
                "plan.distribution_search": True,
                "plan.keys": ["Period", "Z"],
                "plan.identity_partitions": [PERIOD_RULE],
                "plan.partition_key": "Period",
                "plan.max_depth": 1,
                "plan.max_partitions": 7,
                "plan.max_leaves": 7,
                "plan.max_strategy_candidates": 1,
            }
        )
    return props


class ShowingClient(DefaultLlmClient):
    """Print each request and its complete response or failure type."""

    def __init__(self, properties: dict[str, Any], mode: str) -> None:
        super().__init__(properties)
        self.mode = mode
        self.requests = 0

    async def acomplete(
        self, prompt: str, properties: dict[str, Any] | None = None
    ) -> str:
        self.requests += 1
        request_number = self.requests
        print(f"\n{self.mode} model request {request_number}:\n{prompt}", flush=True)
        try:
            answer = await super().acomplete(prompt, properties)
        except Exception as error:
            cause = getattr(error, "provider_error", None)
            cause_type = type(cause).__name__ if cause is not None else "none"
            status = getattr(cause, "status_code", None)
            print(
                f"{self.mode} model request {request_number} failed: "
                f"{type(error).__name__} (provider={cause_type}, status={status})",
                flush=True,
            )
            raise
        print(f"\n{self.mode} model response {request_number}:\n{answer}", flush=True)
        return answer


def record_key(record: dict[str, Any]) -> str | None:
    atomic_number = record.get("Z")
    if atomic_number is None:
        return None
    key = str(atomic_number).strip()
    try:
        number = Decimal(key)
    except InvalidOperation:
        return None
    return str(int(number)) if number == number.to_integral_value() else None


def reference_rows(path: Path) -> dict[str, dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as stream:
        reader = csv.DictReader(stream)
        if not reader.fieldnames or not set(ATTRS).issubset(reader.fieldnames):
            raise ValueError("reference CSV needs the demo's 11 columns")
        return {key: row for row in reader if (key := record_key(row)) is not None}


def normalized_cell(value: Any) -> Decimal | str:
    if value is None:
        return ""
    text = str(value).strip().replace(",", "")
    try:
        return Decimal(text)
    except InvalidOperation:
        return text.casefold()


def run_mode(
    mode: str, budget: Budget, reference: dict[str, dict[str, str]] | None
) -> dict[str, Any]:
    props = properties_for(mode)
    client_props = {
        key: value for key, value in props.items() if key.startswith("llm.")
    }
    print(f"\n=== {mode} ===", flush=True)
    initial_calls, initial_cost = budget.calls, budget.cost
    try:
        with ShowingClient(client_props, mode) as client:
            result = LlmSource(properties=props, client=client).fetch(
                QUERY,
                attrs=FETCH_ATTRS,
                context=ELEMENT_CONTEXT,
                max_records=130,
                budget=budget,
            )
    except (ConfigError, LlmSourceError) as error:
        print(f"{mode} failed: {type(error).__name__}")
        summary = {
            "mode": mode,
            "rows": 0,
            "calls": budget.calls - initial_calls,
            "cost": budget.cost - initial_cost,
            "outcome": "failed",
        }
        if reference is not None:
            summary.update(
                {
                    "matched": 0,
                    "reference": len(reference),
                    "cells": 0,
                    "total_cells": sum(
                        normalized_cell(row[attr]) != ""
                        for row in reference.values()
                        for attr in ATTRS[1:]
                    ),
                }
            )
        return summary

    print("\nrecords:")
    for index, record in enumerate(result.records):
        origins = (
            result.meta.record_origins[index]
            if index < len(result.meta.record_origins)
            else []
        )
        print(json.dumps(record, ensure_ascii=False, sort_keys=True), "from", origins)
    meta = result.meta
    print("rows:", len(result.records))
    print("calls:", meta.calls)
    print("settled cost USD:", round(meta.cost, 6))
    print("rewritten query:", meta.asked)
    print("row estimate:", meta.row_estimate)
    print("outcome:", meta.outcome)
    print("gaps:", len(meta.gaps), meta.gaps[:2])
    print("stop reasons:", meta.stop_reasons)
    if result.planning_receipt is not None:
        receipt = result.planning_receipt.to_dict()
        facts = receipt["planning_facts"]
        print("planned leaves:", len(receipt["fetch_plan"]["leaves"]))
        print("accepted split nodes:", facts["accepted_internal_nodes"])
        print("partition safety:", facts.get("partition_safety_status"))

    summary: dict[str, Any] = {
        "mode": mode,
        "rows": len(result.records),
        "calls": meta.calls,
        "cost": meta.cost,
        "outcome": str(meta.outcome),
    }
    if reference is not None:
        produced: dict[str, dict[str, Any]] = {}
        duplicate_keys: set[str] = set()
        for row in result.records:
            key = record_key(row)
            if key is None:
                continue
            if key in produced:
                duplicate_keys.add(key)
            else:
                produced[key] = row
        shared = produced.keys() & reference.keys()
        matched = len(shared)
        nonkeys = [attr for attr in ATTRS if attr != "Z"]
        known_cells = [
            (key, attr, value)
            for key, row in reference.items()
            for attr in nonkeys
            if (value := normalized_cell(row[attr])) != ""
        ]
        cells = sum(
            normalized_cell(produced[key].get(attr)) == value
            for key, attr, value in known_cells
            if key in shared and key not in duplicate_keys
        )
        total_cells = len(known_cells)
        summary.update(
            {
                "matched": matched,
                "reference": len(reference),
                "cells": cells,
                "total_cells": total_cells,
            }
        )
        print(f"exact atomic numbers in reference: {matched}/{len(reference)}")
        print(f"matched unique returned atomic numbers: {matched}/{len(produced)}")
        print(f"duplicate atomic numbers in returned records: {len(duplicate_keys)}")
        print(f"matching non-key cells: {cells}/{total_cells}")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="call GPT-6 Luna")
    parser.add_argument("--mode", action="append", choices=("all", *MODES))
    parser.add_argument(
        "--reference-csv", type=Path, help="score atomic numbers and non-key cells"
    )
    args = parser.parse_args()
    selected = args.mode or ["all"]
    if selected == ["all"]:
        modes = MODES
    elif "all" in selected or len(set(selected)) != len(selected):
        parser.error("repeat --mode only for distinct individual modes")
    else:
        modes = tuple(selected)

    print("WikiTabGen #15: chemical elements")
    print("query:", QUERY)
    print("context:", ELEMENT_CONTEXT)
    print("columns:", ", ".join(ATTRS))
    print("modes:", ", ".join(modes))
    print(
        "model: gpt-6-luna; reasoning none; 4096 output tokens per call; no cache or retries"
    )
    print("shared cap: $0.30, 160 calls, 900 seconds")
    try:
        reference = reference_rows(args.reference_csv) if args.reference_csv else None
    except (OSError, ValueError) as error:
        parser.error(f"cannot read reference CSV: {error}")
    if not args.live:
        print("Add --live to call GPT-6 Luna and print every model response.")
        return
    if not os.environ.get("OPENAI_API_KEY"):
        parser.error("OPENAI_API_KEY is not exported")

    budget = Budget(TOTAL_LIMITS)
    summaries = [run_mode(mode, budget, reference) for mode in modes]
    print("\n=== comparison ===")
    for item in summaries:
        score = (
            f" exact_keys={item['matched']}/{item['reference']} "
            f"matching_cells={item['cells']}/{item['total_cells']}"
            if "matched" in item
            else ""
        )
        print(
            f"{item['mode']}: rows={item['rows']} calls={item['calls']} "
            f"cost=${item['cost']:.6f} outcome={item['outcome']}{score}"
        )
    print(f"total: calls={budget.calls} settled_cost=${budget.cost:.6f}")
    if reference is not None:
        print("Cell matches compare the returned values with the WikiTabGen table.")


if __name__ == "__main__":
    main()
