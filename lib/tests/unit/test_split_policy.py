"""Evidence-gated splitting: the root is asked once before any split is proposed.

Under `plan.split_policy="evidence"` a partitioned request first runs its root leaf as a
probe. The plan splits only when the provider said the probe's answer hit its output
allowance, or the probe holds fewer than half the keys an estimate above
`plan.split_threshold_rows` expects. Otherwise the probe's answer is the result.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any, cast

import pytest

from blue_data_llm_source import meta_view
from blue_data_llm_source.llm.budget import Budget
from blue_data_llm_source.planning import bounds
from blue_data_llm_source.source import LlmSource
from blue_data_llm_source.types import BudgetExceeded, Capability, ConfigError, FetchResult, Usage

Reply = str | tuple[str, str]

ATTRS = [{"name": "name", "type": "string"}, {"name": "region", "type": "string"}]


class ProbeClient:
    """Answers each stage from the prompt, with an optional finish reason per reply."""

    def __init__(self, responder: Callable[[str], Reply]) -> None:
        self.responder = responder
        self.prompts: list[str] = []

    def budget_capabilities(self) -> frozenset[str]:
        return Capability.ALL

    def bound_for_attempt(self, _prompt: str, _properties: dict[str, Any]) -> float:
        return 0.0

    def complete(self, prompt: str, properties: dict[str, Any] | None = None) -> str:
        raise AssertionError("the library drives the async boundary")

    async def acomplete(self, prompt: str, properties: dict[str, Any] | None = None) -> str:
        budget = (properties or {}).get("budget")
        assert isinstance(budget, Budget)
        with budget.attempt() as reservation:
            self.prompts.append(prompt)
            reservation.settle(0.0)
        reply = self.responder(prompt)
        text, finish = reply if isinstance(reply, tuple) else (reply, "stop")
        sink = (properties or {}).get("usage_sink")
        if isinstance(sink, list):
            cast("list[Usage]", sink).append(Usage(model="probe-double", finish_reason=finish))
        return text


def kind(prompt: str) -> str:
    if prompt.startswith("Prepare a structured data request"):
        return "bootstrap"
    if prompt.startswith("Propose one useful partition"):
        return "proposal"
    return "fetch"


def is_root(prompt: str) -> bool:
    return "\nFilters: {}\n" in prompt


def rows(names: list[str], region: str | None = None) -> str:
    return json.dumps([{"name": name, "region": region} for name in names])


def responder(
    *,
    estimate: int,
    root: Reply,
    children: dict[str, Reply] | None = None,
    values: list[str] | None = None,
) -> Callable[[str], Reply]:
    children = children or {"North": rows(["n1", "n2"], "North"), "South": rows(["s1"], "South")}

    def answer(prompt: str) -> Reply:
        step = kind(prompt)
        if step == "bootstrap":
            return json.dumps(
                {
                    "ask": "stations",
                    "constraints": {},
                    "row_estimate": estimate,
                    "schema": {"attrs": ATTRS, "key": "name"},
                }
            )
        if step == "proposal":
            chosen = values if values is not None else list(children)
            return json.dumps({"key": "region", "values": chosen, "ambiguity": 0})
        if is_root(prompt):
            return root
        for value, reply in children.items():
            if f'"{value}"' in prompt.split("Filters:", 1)[1]:
                return reply
        return "[]"

    return answer


def fetch(answer: Callable[[str], Reply], **settings: Any) -> tuple[ProbeClient, FetchResult]:
    client = ProbeClient(answer)
    result = LlmSource(
        properties={"plan.planning_order": "planning.combined"}, client=client
    ).fetch(
        "stations",
        attrs=ATTRS,
        properties={"plan.mode": "partitioned", "plan.split_policy": "evidence", **settings},
    )
    return client, result


def kinds(client: ProbeClient) -> list[str]:
    return [kind(prompt) for prompt in client.prompts]


def within_bound(client: ProbeClient, result: FetchResult, **settings: Any) -> None:
    limits = LlmSource._plan_limits(settings)  # pyright: ignore[reportPrivateUsage]
    ceiling = bounds.planned_provider_attempts(
        limits, "partitioned", planning_order="planning.combined", split_policy="evidence"
    )
    assert result.meta.calls == len(client.prompts) <= ceiling


# ---------------------------------------------------------------------- the decisions ---


def test_an_accepted_probe_is_the_answer_with_one_leaf_call_and_no_proposal() -> None:
    client, result = fetch(responder(estimate=40, root=rows(["a", "b", "c"], "North")))
    assert kinds(client) == ["bootstrap", "fetch"]
    assert [record["name"] for record in result.records] == ["a", "b", "c"]
    assert result.meta.split_decision == "probe_accepted"
    assert result.meta.record_origins == [["0"], ["0"], ["0"]]
    assert result.meta.outcome == "ok" and result.meta.gaps == []
    within_bound(client, result)


def test_a_probe_cut_by_the_output_allowance_splits() -> None:
    client, result = fetch(
        responder(estimate=40, root=(rows(["a", "n1"], "North"), "length")),
        **{"plan.max_depth": 1},
    )
    assert kinds(client) == ["bootstrap", "fetch", "proposal", "fetch", "fetch"]
    assert result.meta.split_decision == "probe_split_length"
    # The split answered the truncation, so it is not reported as a stop.
    assert "provider_output_limit" not in result.meta.stop_reasons
    assert {record["name"] for record in result.records} == {"a", "n1", "n2", "s1"}
    within_bound(client, result)


def test_a_probe_short_of_a_large_estimate_splits() -> None:
    client, result = fetch(responder(estimate=300, root=rows([f"r{i}" for i in range(149)])))
    assert result.meta.split_decision == "probe_split_shortfall"
    assert "proposal" in kinds(client)
    within_bound(client, result)


@pytest.mark.parametrize(
    ("estimate", "held", "settings", "decision"),
    [
        (300, 150, {}, "probe_accepted"),
        (120, 1, {}, "probe_accepted"),
        (121, 60, {}, "probe_split_shortfall"),
        (300, 10, {"plan.split_threshold_rows": 300}, "probe_accepted"),
        (300, 10, {"plan.split_threshold_rows": 0}, "probe_split_shortfall"),
    ],
)
def test_the_shortfall_rule_reads_half_of_an_estimate_above_the_threshold(
    estimate: int, held: int, settings: dict[str, Any], decision: str
) -> None:
    _, result = fetch(
        responder(estimate=estimate, root=rows([f"r{i}" for i in range(held)])), **settings
    )
    assert result.meta.split_decision == decision


def test_an_unreadable_probe_splits_as_the_default_would() -> None:
    client, result = fetch(responder(estimate=40, root="not json"), **{"plan.max_depth": 1})
    assert result.meta.split_decision == "probe_split_failed"
    assert kinds(client) == ["bootstrap", "fetch", "proposal", "fetch", "fetch"]
    assert {record["name"] for record in result.records} == {"n1", "n2", "s1"}
    assert not any("leaf_failed" in gap for gap in result.meta.gaps)


def test_a_split_no_proposal_survives_reuses_the_probe_rather_than_asking_again() -> None:
    client, result = fetch(
        responder(estimate=40, root=(rows(["a"], "North"), "length"), values=["only"])
    )
    assert kinds(client) == ["bootstrap", "fetch", "proposal"]
    assert [record["name"] for record in result.records] == ["a"]
    # Nothing split, so the truncation stands as the reason the answer may be short.
    assert "provider_output_limit" in result.meta.stop_reasons
    within_bound(client, result)


# ----------------------------------------------------------- root records in the merge ---


def test_root_records_merge_with_the_children_on_key_and_carry_the_root_origin() -> None:
    root = (
        json.dumps([{"name": "n1", "region": None}, {"name": "r1", "region": "West"}]),
        "length",
    )
    _, result = fetch(responder(estimate=40, root=root))
    by_name = {
        record["name"]: origin
        for record, origin in zip(result.records, result.meta.record_origins, strict=True)
    }
    assert by_name["n1"] == ["0.0", "root"]
    assert by_name["r1"] == ["root"]
    merged = next(record for record in result.records if record["name"] == "n1")
    assert merged["region"] == "North"


def test_root_records_count_toward_the_row_cap() -> None:
    client = ProbeClient(responder(estimate=40, root=(rows(["a", "b", "c"]), "length")))
    result = LlmSource(
        properties={"plan.planning_order": "planning.combined"}, client=client
    ).fetch(
        "stations",
        attrs=ATTRS,
        max_records=4,
        properties={"plan.mode": "partitioned", "plan.split_policy": "evidence"},
    )
    assert len(result.records) == 4
    assert result.meta.truncated_to == 4


# -------------------------------------------------------------------- the held list ---


def test_children_are_told_what_the_probe_holds() -> None:
    client, _ = fetch(responder(estimate=40, root=(rows(["a", "b"]), "length")))
    children = [p for p in client.prompts if kind(p) == "fetch" and not is_root(p)]
    assert children
    for prompt in children:
        assert "Already returned: 2 records, the 2 most recent being:" in prompt
        assert '- {"name": "a"}' in prompt and '- {"name": "b"}' in prompt


@pytest.mark.parametrize(
    ("settings", "shown"),
    [({}, 50), ({"plan.max_excluded_keys": 5}, 5), ({"plan.max_exclusion_bytes": 60}, 3)],
)
def test_the_held_list_is_bounded_by_the_exclusion_settings(
    settings: dict[str, Any], shown: int
) -> None:
    names = [f"k{i:02d}" for i in range(80)]
    client, _ = fetch(responder(estimate=40, root=(rows(names), "length")), **settings)
    child = next(p for p in client.prompts if kind(p) == "fetch" and not is_root(p))
    held = [line for line in child.splitlines() if line.startswith('- {"name": "k')]
    assert len(held) == shown
    assert f"Already returned: 80 records, the {shown} most recent being:" in child
    # The most recent keys are the ones kept.
    assert held[-1] == '- {"name": "k79"}'


# ------------------------------------------------------------------------ the budget ---


def test_a_probe_the_budget_refuses_stops_the_request_before_any_proposal() -> None:
    """The planning look-ahead keeps room for the root leaf, so a refused probe is a ceiling
    crossed by something else in the same budget; the client refusing it stands in for that."""
    answer = responder(estimate=40, root=rows(["a"]))

    def refusing(prompt: str) -> Reply:
        if kind(prompt) == "fetch":
            raise BudgetExceeded("the call ceiling was reached", "max_calls")
        return answer(prompt)

    client, result = fetch(refusing)
    assert kinds(client) == ["bootstrap", "fetch"]
    assert result.records == []
    assert result.meta.outcome == "stopped_by_limit"
    assert result.meta.stopped_by == "max_calls"
    assert result.meta.split_decision is None


# ------------------------------------------------------------------ the meta and wiring ---


def test_the_decision_is_published_only_when_a_probe_ran() -> None:
    _, probed = fetch(responder(estimate=40, root=rows(["a"])))
    assert meta_view(probed.meta)["split_decision"] == "probe_accepted"
    client = ProbeClient(responder(estimate=40, root=rows(["a"])))
    default = LlmSource(
        properties={"plan.planning_order": "planning.combined"}, client=client
    ).fetch("stations", attrs=ATTRS, properties={"plan.mode": "partitioned"})
    assert default.meta.split_decision is None
    assert "split_decision" not in meta_view(default.meta)


def test_a_root_that_cannot_split_is_not_probed() -> None:
    client, result = fetch(responder(estimate=40, root=rows(["a"])), **{"plan.max_depth": 0})
    assert kinds(client) == ["bootstrap", "fetch"]
    assert result.meta.split_decision is None


@pytest.mark.parametrize("mode", ["flat", "row_by_row"])
def test_evidence_is_refused_outside_partitioned_mode(mode: str) -> None:
    with pytest.raises(ConfigError, match="plan.split_policy"):
        LlmSource(client=ProbeClient(lambda _p: "[]")).fetch(
            "x", properties={"plan.mode": mode, "plan.split_policy": "evidence"}
        )


@pytest.mark.parametrize(
    ("settings", "message"),
    [
        ({"plan.split_policy": "sometimes"}, "unknown plan.split_policy"),
        ({"plan.split_policy": None}, "unknown plan.split_policy"),
        ({"plan.split_threshold_rows": -1}, "non-negative integer"),
        ({"plan.split_threshold_rows": True}, "non-negative integer"),
        ({"plan.split_threshold_rows": 50}, "read only under plan.split_policy='evidence'"),
    ],
)
def test_bad_split_settings_are_refused(settings: dict[str, Any], message: str) -> None:
    with pytest.raises(ConfigError, match=message):
        LlmSource(client=ProbeClient(lambda _p: "[]")).fetch(
            "x", properties={"plan.mode": "partitioned", **settings}
        )


# ------------------------------------------------------------------------- the bounds ---


@pytest.mark.parametrize("mode", ["flat", "partitioned", "row_by_row"])
def test_the_probe_is_one_logical_call_in_every_accessor(mode: str) -> None:
    limits = bounds.PlanLimits()
    extra = 1 if mode == "partitioned" else 0
    assert bounds.probe_calls(mode, "evidence") == extra
    assert bounds.probe_calls(mode, "always") == 0
    assert bounds.planned_logical_calls(limits, mode, split_policy="evidence") == (
        bounds.planned_logical_calls(limits, mode) + extra
    )
    assert bounds.planned_provider_attempts(limits, mode, split_policy="evidence") == (
        bounds.planned_provider_attempts(limits, mode) + extra * (limits.max_retries + 1)
    )
    assert bounds.request_calls(limits, mode, split_policy="evidence") == (
        bounds.request_calls(limits, mode) + extra
    )
    assert bounds.planning_calls(limits, mode, split_policy="evidence") == (
        bounds.planning_calls(limits, mode) + extra
    )
    assert bounds.worst_case_attempts(limits, mode, split_policy="evidence") == (
        bounds.worst_case_attempts(limits, mode) + extra * (limits.max_retries + 1)
    )


def test_an_unknown_policy_is_refused_by_the_bounds() -> None:
    with pytest.raises(ConfigError, match="split policy"):
        bounds.probe_calls("partitioned", "sometimes")


# --------------------------------------------- cut answers, failed probes, row targets ---


def test_a_salvaged_but_unattested_probe_splits_as_incomplete() -> None:
    cut = '[{"name": "a", "region": "North"}, {"name": "b", "reg'
    client, result = fetch(responder(estimate=40, root=cut), **{"plan.max_depth": 1})
    assert result.meta.split_decision == "probe_split_incomplete"
    assert kinds(client) == ["bootstrap", "fetch", "proposal", "fetch", "fetch"]
    assert "response_incomplete" not in result.meta.stop_reasons
    assert {record["name"] for record in result.records} == {"a", "n1", "n2", "s1"}


def test_a_failed_probe_that_led_to_a_split_is_reported_as_a_gap() -> None:
    _, result = fetch(responder(estimate=40, root="not json"), **{"plan.max_depth": 1})
    assert result.meta.split_decision == "probe_split_failed"
    assert {record["name"] for record in result.records} == {"n1", "n2", "s1"}
    assert any(gap.startswith("probe_failed: ") for gap in result.meta.gaps)


@pytest.mark.parametrize("estimate", [40, 300])
def test_a_zero_row_target_asks_nothing_under_evidence_as_without_it(estimate: int) -> None:
    client, result = fetch(responder(estimate=estimate, root=rows(["a"])), **{"plan.exact_rows": 0})
    assert kinds(client) == ["bootstrap"]
    assert result.records == []
    # No probe call was made, so there is no probe decision to report.
    assert result.meta.split_decision is None
    assert "split_decision" not in cast("dict[str, Any]", result.to_dict()["meta"])
    assert "split_decision" not in meta_view(result.meta)


def test_a_probe_that_meets_the_row_target_is_accepted_whatever_the_estimate() -> None:
    client, result = fetch(responder(estimate=300, root=rows(["a", "b"])), **{"plan.exact_rows": 2})
    assert kinds(client) == ["bootstrap", "fetch"]
    assert result.meta.split_decision == "probe_accepted"


def test_an_accepted_probe_is_one_ask_even_with_continuations_configured() -> None:
    client, result = fetch(
        responder(estimate=40, root=rows(["a"])), **{"plan.max_continuations": 3}
    )
    assert kinds(client) == ["bootstrap", "fetch"]
    assert result.meta.split_decision == "probe_accepted"


@pytest.mark.parametrize("mode", ["flat", "partitioned", "row_by_row"])
def test_a_default_result_serializes_without_the_split_decision(mode: str) -> None:
    from tests.unit.scenario_doubles import run_scenario

    _, result = run_scenario(mode)
    assert "split_decision" not in cast("dict[str, Any]", result.to_dict()["meta"])
    _, probed = fetch(responder(estimate=40, root=rows(["a"])))
    assert cast("dict[str, Any]", probed.to_dict()["meta"])["split_decision"] == "probe_accepted"


@pytest.mark.parametrize(
    ("root", "decision"),
    [
        ((rows(["a", "b"], "North"), "length"), "probe_split_length"),
        (
            '[{"name": "a", "region": "North"}, {"name": "b", "region": "North"}, {"name": "c',
            "probe_split_incomplete",
        ),
    ],
    ids=["cut-by-allowance", "cut-mid-document"],
)
def test_a_cut_probe_holding_exactly_the_row_target_still_splits(
    root: Reply, decision: str
) -> None:
    client, result = fetch(
        responder(estimate=40, root=root), **{"plan.exact_rows": 2, "plan.max_depth": 1}
    )
    assert result.meta.split_decision == decision
    assert "proposal" in kinds(client)


def test_a_probe_record_without_a_key_never_reaches_a_child_prompt() -> None:
    root = (
        json.dumps(
            [
                {"name": "kept", "region": "North"},
                {"name": None, "region": "private detail"},
            ]
        ),
        "length",
    )
    client, result = fetch(responder(estimate=40, root=root), **{"plan.max_depth": 1})
    children = [p for p in client.prompts if kind(p) == "fetch" and not is_root(p)]
    assert children
    for prompt in children:
        assert "Already returned: 1 records, the 1 most recent being:" in prompt
        assert '- {"name": "kept"}' in prompt
        assert "private detail" not in prompt
    assert result.meta.split_decision == "probe_split_length"


def test_a_failed_probe_gap_names_the_failure_type_and_nothing_the_provider_said() -> None:
    answer = responder(estimate=40, root=rows(["a"]))
    # Assemble the opaque token at run time; the simulated provider error must
    # still have the shape that redaction protects.
    token = "-".join(["sk", "live", "0123456789abcdef" + "ABCDEF"])
    leaked = f"{token} https://internal.example.test/v1/trace?token=abc"

    def failing(prompt: str) -> Reply:
        if kind(prompt) == "fetch" and is_root(prompt):
            raise RuntimeError(f"provider said: {leaked}")
        return answer(prompt)

    _, result = fetch(failing, **{"plan.max_depth": 1})
    assert result.meta.split_decision == "probe_split_failed"
    gaps = [gap for gap in result.meta.gaps if gap.startswith("probe_failed")]
    assert gaps == ["probe_failed: RuntimeError"]
    published = json.dumps(meta_view(result.meta)) + json.dumps(result.to_dict())
    assert token not in published and "internal.example.test" not in published


def test_an_unreadable_probe_gap_carries_no_model_text() -> None:
    _, result = fetch(responder(estimate=40, root="not json at all"), **{"plan.max_depth": 1})
    assert [gap for gap in result.meta.gaps if gap.startswith("probe_failed")] == [
        "probe_failed: ParseError"
    ]
