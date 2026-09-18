"""The CLI and the MCP server describe one run the same way.

They did not. Each adapter carried its own `meta_summary` and the two gated different
fields, so the same `ExecMeta` came out of the two doors with different keys. Both now
call the library's `meta_view`, and this is the property that makes that worth doing —
checked against the two adapters' real payloads rather than against the helper, since the
helper agreeing with itself proves nothing.

The MCP package is imported by path. The two platform packages are siblings that do not
depend on each other, and neither should acquire a dependency on the other to be tested;
what this needs is the source tree next door, which the repository always has.
"""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path
from typing import Any

import pytest
from blue_data_llm_source import ExecMeta, FetchResult
from click.testing import CliRunner

from blue_data_llm_cli.commands import cli

MCP_SRC = Path(__file__).resolve().parents[3] / "llm-mcp" / "src"
QUERY = "the member states of the European Union"


@pytest.fixture(scope="module")
def mcp_tools() -> Any:
    if str(MCP_SRC) not in sys.path:
        sys.path.insert(0, str(MCP_SRC))
    return importlib.import_module("blue_data_llm_mcp.tools")


def loud_meta() -> ExecMeta:
    """Every field either adapter can publish, with fixed timings.

    Canned rather than measured: two runs of the same fetch differ in `elapsed_seconds`,
    and comparing two serializations of one object is the question being asked.
    """
    return ExecMeta(
        gaps=["leaf_empty: leaf 0.0 returned nothing"],
        outcome="partial",
        model="gpt-4o",
        calls=3,
        prompt_tokens=12,
        completion_tokens=34,
        cost=0.0012,
        latency_seconds=0.31,
        cache_hits=2,
        requested_attrs=["country"],
        row_estimate=27,
        cardinality_estimate={"expected": 27},
        primary_keys=["country"],
        observed_key_count=9,
        marginal_key_yields=[7, 2],
        incomplete_key_records=1,
        normalization_collisions=[{"kept": "France"}],
        observed_keys=["France", "Germany"],
        unmapped_keys=["capital"],
        remapped_keys={"Country": "country"},
        asked="which countries joined the EU?",
        record_origins=[["0.0"]],
        type_mismatches=[
            {"record_index": 0, "attribute": "year", "value": "x", "declared_type": "int"}
        ],
        key_collisions=[
            {
                "record_index": 0,
                "attribute": "country",
                "kept_key": "country",
                "ignored_key": "Country",
                "ignored_value": "France",
            }
        ],
        unchecked_types=["money"],
        truncated_to=2,
        stop_reasons=["max_rows"],
        stopped_by="max_rows",
        accounting_known=False,
    )


class CannedSource:
    """Returns one prepared result, so both adapters serialize the same object."""

    def __init__(self, meta: ExecMeta) -> None:
        self.result = FetchResult(records=[{"country": "France"}], meta=meta)

    def fetch(self, *args: Any, **kwargs: Any) -> FetchResult:
        return self.result


def test_both_adapters_serialize_one_exec_meta_identically(
    runner: CliRunner, install_source: Any, mcp_tools: Any
) -> None:
    meta = loud_meta()
    install_source(CannedSource(meta))
    from_cli = runner.invoke(cli, ["data", "query", QUERY, "--verbose", "--format", "json"])
    assert from_cli.exit_code in (0, 8), from_cli.output

    from_mcp = mcp_tools.llmdb_query({"query": QUERY}, source=CannedSource(meta))

    assert json.loads(from_cli.stdout)["meta"] == from_mcp["meta"]


def test_the_shared_view_is_what_each_adapter_publishes(
    runner: CliRunner, install_source: Any, mcp_tools: Any
) -> None:
    """Neither adapter reshapes the view on the way out."""
    from blue_data_llm_source import meta_view

    meta = loud_meta()
    install_source(CannedSource(meta))
    from_cli = runner.invoke(cli, ["data", "query", QUERY, "--verbose", "--format", "json"])
    assert json.loads(from_cli.stdout)["meta"] == meta_view(meta, verbose=True)

    from_mcp = mcp_tools.llmdb_query({"query": QUERY}, source=CannedSource(meta))
    assert from_mcp["meta"] == meta_view(meta, verbose=True)
