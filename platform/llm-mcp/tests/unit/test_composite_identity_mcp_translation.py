"""MCP translation for composite identity and estimate diagnostics."""

from __future__ import annotations

from blue_data_llm_source import ExecMeta, meta_view

from blue_data_llm_mcp import tools


def test_mcp_declares_and_forwards_ordered_keys_exact_rows_and_exclusion_bound() -> None:
    arguments = {
        "plan.keys": ["Console", "Game"],
        "plan.exact_rows": 210,
        "plan.max_exclusion_bytes": 4096,
    }
    assert set(arguments) <= set(tools.PLAN_PROPERTIES)
    assert tools.limit_properties(arguments) == arguments


def test_mcp_meta_exposes_structured_estimate_and_key_diagnostics() -> None:
    estimate: dict[str, object] = {
        "expected": 210,
        "lower": 180,
        "upper": 240,
        "row_unit": "games",
        "source": "model",
        "parse_status": "parsed",
        "diagnostics": [],
    }
    summary = meta_view(
        ExecMeta(
            row_estimate=210,
            cardinality_estimate=estimate,
            primary_keys=["Console", "Game"],
            observed_key_count=12,
            marginal_key_yields=[8, 4],
            incomplete_key_records=1,
        ),
        verbose=True,
    )
    assert summary["cardinality_estimate"] == estimate
    assert summary["primary_keys"] == ["Console", "Game"]
    assert summary["observed_key_count"] == 12
