"""CLI translation for composite identity and estimate diagnostics."""

from __future__ import annotations

from blue_data_llm_source import ExecMeta, Limits

from blue_data_llm_cli import config


def test_cli_translates_ordered_keys_exact_rows_and_exclusion_bound() -> None:
    properties = config.build_properties(
        None,
        Limits(),
        plan_keys=("Console", "Game"),
        plan_exact_rows=210,
        plan_exclusion_bytes=4096,
    )
    assert properties == {
        "plan.keys": ["Console", "Game"],
        "plan.exact_rows": 210,
        "plan.max_exclusion_bytes": 4096,
    }


def test_cli_translates_planning_order_and_estimate_exposure() -> None:
    properties = config.build_properties(
        None,
        config.build_limits(None, None, None),
        plan_planning_order="planning.locked_parallel",
        plan_expose_estimate=False,
    )
    assert properties == {
        "plan.planning_order": "planning.locked_parallel",
        "plan.expose_estimate": False,
    }


def test_cli_meta_exposes_structured_estimate_and_key_diagnostics() -> None:
    estimate: dict[str, object] = {
        "expected": 210,
        "lower": 180,
        "upper": 240,
        "row_unit": "games",
        "source": "model",
        "parse_status": "parsed",
        "diagnostics": [],
    }
    summary = config.meta_summary(
        ExecMeta(
            row_estimate=210,
            cardinality_estimate=estimate,
            primary_keys=["Console", "Game"],
            observed_key_count=12,
            marginal_key_yields=[8, 4],
            incomplete_key_records=1,
        )
    )
    assert summary["cardinality_estimate"] == estimate
    assert summary["primary_keys"] == ["Console", "Game"]
    assert summary["marginal_key_yields"] == [8, 4]
