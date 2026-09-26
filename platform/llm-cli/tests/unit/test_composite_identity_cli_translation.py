"""CLI translation for composite identity and estimate diagnostics."""

from __future__ import annotations

from blue_data_llm_source import ExecMeta, Limits, meta_view

from blue_data_llm_cli import config


def test_cli_translates_ordered_keys_exact_rows_and_exclusion_bound() -> None:
    properties = config.plan_properties(
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
    properties = config.plan_properties(
        plan_planning_order="planning.locked_parallel",
        plan_expose_estimate=False,
    )
    assert properties == {
        "plan.planning_order": "planning.locked_parallel",
        "plan.expose_estimate": False,
    }


def test_cli_passes_the_prompt_contract_switches_through_unrenamed() -> None:
    properties = config.plan_properties(
        plan_leaf_prompt="keyed-leaf-fetch-2",
        plan_child_wording="canonical",
        plan_filter_enforcement="drop",
        plan_range_filters=False,
        plan_partition_guardrails=True,
        plan_split_policy="evidence",
        plan_split_threshold_rows=0,
    )
    assert properties == {
        "plan.leaf_prompt": "keyed-leaf-fetch-2",
        "plan.child_wording": "canonical",
        "plan.filter_enforcement": "drop",
        "plan.range_filters": False,
        "plan.partition_guardrails": True,
        "plan.split_policy": "evidence",
        "plan.split_threshold_rows": 0,
    }
    # Unset switches send nothing, so the library's own defaults apply.
    assert config.plan_properties() == {}


def test_cli_translates_the_ceilings_to_the_keys_the_source_reads() -> None:
    limits = config.build_limits(0.5, 30.0, 2, 1)
    assert config.limit_properties(limits) == {
        "limits.max_cost": 0.5,
        "limits.max_seconds": 30.0,
        "limits.max_calls": 2,
        "limits.max_retries": 1,
    }


def test_an_unset_retry_allowance_is_left_to_the_library_default() -> None:
    assert config.build_limits().max_retries == Limits().max_retries
    assert "limits.max_retries" not in config.limit_properties(config.build_limits())


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
    summary = meta_view(
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
