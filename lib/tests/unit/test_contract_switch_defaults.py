"""Every prompt-contract switch left at its default reproduces the release, and none adds a call.

The switches exist to be compared one at a time before any default moves, so the comparison
is only meaningful if the default arm is the release itself. The digests below were recorded
against 2.1.0, before any switch existed, over every prompt sent and every published fact
the switches could move. Each switch is then turned on alone and shown not to move a
published call bound or to spend a call the default does not.
"""

from __future__ import annotations

from typing import Any

import pytest

from blue_data_llm_source.planning import bounds
from blue_data_llm_source.source import LlmSource
from tests.unit.scenario_doubles import digest, run_scenario

BASELINE_DIGESTS = {
    "partitioned": "2ee0bf810fdd09b1ce60d31a75d198fb23a56ba2499e673d2ad8dd8c0182f49c",
    "row_by_row": "55a5fa86e3d39213441bec1cb657f4b2e22cbbf849e2c2b4a84b7ed5a5076c64",
    "flat": "5eb6924387fa3750d668133047548adefc827e28e1e91535a0dfe7632848320b",
}
"""Prompts and results of each scenario under 2.1.0, recorded before any switch existed."""

DEFAULTS_SPELLED_OUT: dict[str, Any] = {
    "plan.leaf_prompt": "keyed-leaf-fetch-1",
    "plan.child_wording": "parent",
    "plan.filter_enforcement": "flag",
    "plan.range_filters": False,
    "plan.partition_guardrails": False,
    "plan.split_policy": "always",
    "plan.split_threshold_rows": 120,
}
"""Every contract switch at its default, written explicitly."""

SWITCHES_ON: list[dict[str, Any]] = [
    {"plan.leaf_prompt": "keyed-leaf-fetch-2"},
    {"plan.child_wording": "canonical"},
    {"plan.filter_enforcement": "drop"},
    # Ranges travel only on the completeness contract's wire version, so they need it on.
    {"plan.leaf_prompt": "keyed-leaf-fetch-2", "plan.range_filters": True},
    {"plan.partition_guardrails": True},
]
"""Each switch on alone, in the mode that accepts it, with only what it depends on."""


@pytest.mark.parametrize("scenario", sorted(BASELINE_DIGESTS))
def test_an_unset_switch_sends_the_prompts_and_returns_the_rows_of_the_release(
    scenario: str,
) -> None:
    client, result = run_scenario(scenario)
    assert digest(client, result) == BASELINE_DIGESTS[scenario]


@pytest.mark.parametrize("scenario", sorted(BASELINE_DIGESTS))
def test_the_defaults_written_out_change_nothing(scenario: str) -> None:
    client, result = run_scenario(scenario, DEFAULTS_SPELLED_OUT)
    assert digest(client, result) == BASELINE_DIGESTS[scenario]


def _published_counts(settings: dict[str, Any], mode: str) -> tuple[int, ...]:
    merged = {"plan.mode": mode, **settings}
    limits = LlmSource._plan_limits(merged)  # pyright: ignore[reportPrivateUsage]
    return (
        bounds.planned_logical_calls(limits, mode),
        bounds.planned_provider_attempts(limits, mode),
        bounds.request_calls(limits, mode),
        bounds.planning_calls(limits, mode),
        bounds.worst_case_attempts(limits, mode),
        bounds.max_nodes(limits, mode),
        bounds.execution_calls(limits, bounds.leaf_limit(limits, mode)),
    )


@pytest.mark.parametrize("settings", SWITCHES_ON, ids=lambda s: ",".join(s))
@pytest.mark.parametrize("mode", ["partitioned", "row_by_row"])
def test_no_switch_moves_a_published_call_bound(settings: dict[str, Any], mode: str) -> None:
    assert _published_counts(settings, mode) == _published_counts({}, mode)


@pytest.mark.parametrize("settings", SWITCHES_ON, ids=lambda s: ",".join(s))
def test_no_switch_spends_a_call_the_default_does_not(settings: dict[str, Any]) -> None:
    baseline, _ = run_scenario("partitioned")
    client, result = run_scenario("partitioned", settings)
    limits = LlmSource._plan_limits({})  # pyright: ignore[reportPrivateUsage]
    ceiling = bounds.planned_provider_attempts(
        limits, "partitioned", planning_order="planning.combined"
    )
    assert result.meta.calls == len(client.prompts) == len(baseline.prompts) <= ceiling


CUMULATIVE_ARMS: list[dict[str, Any]] = [
    {},
    {"plan.leaf_prompt": "keyed-leaf-fetch-2"},
    {"plan.leaf_prompt": "keyed-leaf-fetch-2", "plan.child_wording": "canonical"},
    {
        "plan.leaf_prompt": "keyed-leaf-fetch-2",
        "plan.child_wording": "canonical",
        "plan.range_filters": True,
    },
    {
        "plan.leaf_prompt": "keyed-leaf-fetch-2",
        "plan.child_wording": "canonical",
        "plan.range_filters": True,
        "plan.partition_guardrails": True,
    },
]
"""The baseline and each cumulative combination of the completeness parts, in the order a
comparison would add them, so each adjacent pair differs by one part."""


@pytest.mark.parametrize("arm", CUMULATIVE_ARMS, ids=lambda s: ",".join(s) or "baseline")
@pytest.mark.parametrize("enforcement", ["flag", "drop"])
def test_every_cumulative_arm_runs_within_the_published_bound(
    arm: dict[str, Any], enforcement: str
) -> None:
    baseline, _ = run_scenario("partitioned")
    client, result = run_scenario("partitioned", {**arm, "plan.filter_enforcement": enforcement})
    limits = LlmSource._plan_limits({})  # pyright: ignore[reportPrivateUsage]
    ceiling = bounds.planned_provider_attempts(
        limits, "partitioned", planning_order="planning.combined"
    )
    assert len(client.prompts) == len(baseline.prompts) <= ceiling
    assert result.meta.calls == len(client.prompts)
    assert _published_counts({**arm, "plan.filter_enforcement": enforcement}, "partitioned") == (
        _published_counts({}, "partitioned")
    )
