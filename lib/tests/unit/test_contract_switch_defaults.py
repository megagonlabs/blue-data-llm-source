"""Check default switches against current prompts and retained release behavior."""

from __future__ import annotations

from copy import copy
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
"""Prompt and result digests from 0.2.1.0, before the optional switches."""


CURRENT_DIGESTS = {
    "partitioned": "5ffe78b10c1eab2f1ea5c202453edc87376b36498767b729c5668864a2705049",
    "row_by_row": "269013b1d461a157245b8348c4f9cb41dd77fbbef033f223aba5f3868aec74d5",
    "flat": BASELINE_DIGESTS["flat"],
}
"""Current digests include key inference and whole-slice partition guidance."""

_KEY_INFERENCE_CLARIFICATION = (
    "Caller-supplied primary keys must remain unchanged. When none were supplied, infer fields\n"
    "from the schema and request that jointly identify one record, only when defensible.\n"
    "An empty declared primary_keys list means no key was supplied; "
    "it does not disable inference.\n"
)

_IDENTITY_COVERAGE_GUIDANCE = (
    "The values together must cover every record matching this slice and preserve its filters.\n"
    "Choose categories or periods, rather than record identifiers or identity-field values.\n"
    "Do not sample or truncate a domain to fit the value limit. If no useful complete split\n"
    "fits, return an empty values list so the parent slice can run intact.\n\n"
)

DEFAULTS_SPELLED_OUT: dict[str, Any] = {
    "plan.leaf_prompt": "keyed-leaf-fetch-1",
    "plan.child_wording": "parent",
    "plan.filter_enforcement": "flag",
    "plan.range_filters": False,
    "plan.partition_guardrails": False,
    "plan.split_policy": "always",
    "plan.split_threshold_rows": 120,
}
"""The default values of the optional switches."""

SWITCHES_ON: list[dict[str, Any]] = [
    {"plan.leaf_prompt": "keyed-leaf-fetch-2"},
    {"plan.child_wording": "canonical"},
    {"plan.filter_enforcement": "drop"},
    # Ranges travel only on the completeness contract's wire version, so they need it on.
    {"plan.leaf_prompt": "keyed-leaf-fetch-2", "plan.range_filters": True},
    {"plan.partition_guardrails": True},
]
"""Non-default settings tested independently, with required dependencies."""


@pytest.mark.parametrize("scenario", sorted(CURRENT_DIGESTS))
def test_an_unset_switch_sends_the_prompts_and_returns_the_rows_of_the_release(
    scenario: str,
) -> None:
    client, result = run_scenario(scenario)
    assert digest(client, result) == CURRENT_DIGESTS[scenario]


@pytest.mark.parametrize("scenario", sorted(CURRENT_DIGESTS))
def test_the_defaults_written_out_change_nothing(scenario: str) -> None:
    client, result = run_scenario(scenario, DEFAULTS_SPELLED_OUT)
    assert digest(client, result) == CURRENT_DIGESTS[scenario]


@pytest.mark.parametrize("scenario", sorted(CURRENT_DIGESTS))
def test_prompt_clarifications_preserve_other_release_defaults(scenario: str) -> None:
    client, result = run_scenario(scenario)
    occurrences = sum(prompt.count(_KEY_INFERENCE_CLARIFICATION) for prompt in client.prompts)
    assert occurrences == (0 if scenario == "flat" else 1)
    prior = copy(client)
    prior.prompts = [
        prompt.replace(_KEY_INFERENCE_CLARIFICATION, "").replace(_IDENTITY_COVERAGE_GUIDANCE, "")
        for prompt in client.prompts
    ]
    assert digest(prior, result) == BASELINE_DIGESTS[scenario]


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
