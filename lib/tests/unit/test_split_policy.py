"""Evidence-gated splitting: the root is asked, with its continuations, before any split.

Under `plan.split_policy="evidence"` a partitioned request first runs its root leaf as a
probe. The plan splits only when that answer was cut off, was still growing when its
continuations ran out, failed, holds no keys although records are expected, or holds fewer
than half the keys an estimate above `plan.split_threshold_rows` expects. Otherwise the
probe's answer is the result.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any, cast

import pytest

from blue_data_llm_source import meta_view
from blue_data_llm_source.llm.budget import Budget
from blue_data_llm_source.planning import bounds, coordinator
from blue_data_llm_source.planning.plan import PartitionPolicy, PartitionProposal, root_plan
from blue_data_llm_source.planning.schema import Schema
from blue_data_llm_source.planning.split_gate import ProbeOutcome
from blue_data_llm_source.source import LlmSource
from blue_data_llm_source.types import (
    Attr,
    BudgetExceeded,
    Capability,
    ConfigError,
    FetchResult,
    JsonScalar,
    OneOf,
    Range,
    Usage,
)

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
        properties={
            "plan.mode": "partitioned",
            "plan.max_continuations": 0,
            "plan.split_policy": "evidence",
            **settings,
        },
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
    client, result = fetch(responder(estimate=40, root=(rows(["a"]), "length"), values=["only"]))
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
        properties={
            "plan.mode": "partitioned",
            "plan.max_continuations": 0,
            "plan.split_policy": "evidence",
        },
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
    ).fetch(
        "stations",
        attrs=ATTRS,
        properties={
            "plan.mode": "partitioned",
            "plan.split_policy": "always",
            "plan.max_continuations": 0,
        },
    )
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
            "x",
            properties={
                "plan.mode": "partitioned",
                "plan.split_policy": "always",
                "plan.max_continuations": 0,
                **settings,
            },
        )


# ------------------------------------------------------------------------- the bounds ---


@pytest.mark.parametrize("mode", ["flat", "partitioned", "row_by_row"])
def test_every_accessor_charges_the_probe_and_its_continuations(mode: str) -> None:
    limits = bounds.PlanLimits()
    extra = 1 if mode == "partitioned" else 0
    assert bounds.probe_calls(mode, "evidence") == extra
    assert bounds.probe_calls(mode, "evidence", 3) == 4 * extra
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


def test_a_probe_asks_its_continuations_before_it_is_accepted() -> None:
    # The second ask returns nothing new, so the probe stops there and stands as the answer.
    client, result = fetch(
        responder(estimate=40, root=rows(["a"])), **{"plan.max_continuations": 3}
    )
    assert kinds(client) == ["bootstrap", "fetch", "fetch"]
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


# ------------------------------------------- probe values, continuations and defaults ---


def test_a_value_the_probe_returned_is_added_to_a_short_proposal() -> None:
    root = (
        json.dumps([{"name": "a", "region": "North"}, {"name": "b", "region": "South"}]),
        "length",
    )
    client, result = fetch(responder(estimate=40, root=root, values=["North"]))
    children = [p for p in client.prompts if kind(p) == "fetch" and not is_root(p)]
    assert any('"South"' in p.split("Filters:", 1)[1] for p in children)
    assert {record["name"] for record in result.records} == {"a", "b", "n1", "n2", "s1"}
    within_bound(client, result)


def test_a_proposal_missing_more_values_than_fit_is_refused_and_the_probe_stands() -> None:
    probe = [("a", "North"), ("b", "South"), ("c", "East")]
    root = (json.dumps([{"name": name, "region": region} for name, region in probe]), "length")
    client, result = fetch(
        responder(estimate=40, root=root, values=["North", "West"]),
        **{"plan.max_partitions": 3},
    )
    assert kinds(client) == ["bootstrap", "fetch", "proposal"]
    assert {record["name"] for record in result.records} == {"a", "b", "c"}


def test_a_probe_still_growing_when_its_continuations_run_out_splits() -> None:
    asks = 0
    base = responder(estimate=40, root=rows(["a"], "North"))

    def answer(prompt: str) -> Reply:
        nonlocal asks
        if kind(prompt) == "fetch" and is_root(prompt):
            asks += 1
            return rows([f"r{asks}"], "North")
        return base(prompt)

    client, result = fetch(answer, **{"plan.max_continuations": 1})
    assert asks == 2
    assert result.meta.split_decision == "probe_split_length"
    assert "proposal" in kinds(client)
    within_bound(client, result, **{"plan.max_continuations": 1})


def test_partitioned_mode_defaults_to_the_evidence_policy_with_continuations() -> None:
    plan_limits = LlmSource._plan_limits  # pyright: ignore[reportPrivateUsage]
    assert plan_limits({"plan.mode": "partitioned"}).max_continuations == 3
    assert plan_limits({"plan.mode": "flat"}).max_continuations == 0
    client = ProbeClient(responder(estimate=40, root=rows(["a", "b"], "North")))
    result = LlmSource(
        properties={"plan.planning_order": "planning.combined"}, client=client
    ).fetch("stations", attrs=ATTRS, properties={"plan.mode": "partitioned"})
    assert result.meta.split_decision == "probe_accepted"
    ceiling = bounds.planned_provider_attempts(
        plan_limits({"plan.mode": "partitioned"}),
        "partitioned",
        planning_order="planning.combined",
        split_policy="evidence",
    )
    assert result.meta.calls == len(client.prompts) <= ceiling


def test_an_empty_probe_against_a_positive_estimate_splits() -> None:
    client, result = fetch(responder(estimate=40, root="[]"))
    assert result.meta.split_decision == "probe_split_shortfall"
    assert {record["name"] for record in result.records} == {"n1", "n2", "s1"}
    within_bound(client, result)


# ------------------------------------------------- what the probe's records add or keep ---


def test_a_refused_continuation_keeps_the_answer_the_root_gave() -> None:
    client = ProbeClient(responder(estimate=40, root=rows(["a"], "North")))
    result = LlmSource(
        properties={
            "plan.planning_order": "planning.combined",
            "limits.max_calls": 2,
            "limits.max_retries": 0,
        },
        client=client,
    ).fetch(
        "stations",
        attrs=ATTRS,
        properties={"plan.mode": "partitioned", "plan.max_continuations": 1},
    )
    assert [record["name"] for record in result.records] == ["a"]
    assert result.meta.outcome == "partial"
    assert result.meta.stopped_by == "max_calls"
    assert "proposal" not in kinds(client)


def test_a_keyless_probe_does_not_continue() -> None:
    base = responder(estimate=40, root=rows(["a"], "North"))

    def answer(prompt: str) -> Reply:
        if kind(prompt) == "bootstrap":
            return json.dumps(
                {
                    "ask": "stations",
                    "constraints": {},
                    "row_estimate": 40,
                    "schema": {"attrs": ATTRS},
                }
            )
        return base(prompt)

    client, result = fetch(answer, **{"plan.max_continuations": 3})
    assert kinds(client) == ["bootstrap", "fetch"]
    assert any(gap.startswith("key_unavailable") for gap in result.meta.gaps)


def probe_values(
    records: list[dict[str, Any]],
    values: tuple[JsonScalar, ...],
    attrs: list[Attr],
    *,
    key: str | None = None,
    policy: PartitionPolicy | None = None,
) -> PartitionProposal:
    plan = root_plan(
        mode="partitioned",
        original_query="stations",
        retrieval_query="stations",
        constraints={},
        estimate=None,
    )
    probed = ProbeOutcome(run=SimpleNamespace(records=records), decision="probe_split_length")
    proposal, _ = coordinator._with_probe_values(  # pyright: ignore[reportPrivateUsage]
        plan,
        plan.leaves[0],
        PartitionProposal(key=key or attrs[1].name, values=values),
        probed,
        Schema(attrs=attrs, primary_keys=("name",)),
        policy,
    )
    return proposal


TEXT = [Attr("name", "string"), Attr("region", "string")]


def test_a_value_a_child_already_asks_for_is_not_added_again() -> None:
    proposal = probe_values([{"name": "a", "region": "north"}], ("North", "South"), TEXT)
    assert proposal.values == ("North", "South")
    years = [Attr("name", "string"), Attr("year", "integer")]
    assert probe_values([{"name": "a", "year": 1}], ("1", "2"), years).values == ("1", "2")


def test_a_value_read_through_an_aliased_field_is_added() -> None:
    proposal = probe_values([{"Name": "c", "Region": "South"}], ("North",), TEXT)
    assert proposal.values == ("North", "South")


def test_an_added_value_is_estimated_by_its_distinct_keys() -> None:
    proposal = probe_values([{"name": "a", "region": "South"}] * 4, ("North",), TEXT)
    assert proposal.child_estimates == (None, 1)


def test_a_range_child_asks_for_the_years_inside_it() -> None:
    asks_for = coordinator._asks_for  # pyright: ignore[reportPrivateUsage]
    assert asks_for(Range(2000, 2004), 2000, "integer")
    assert not asks_for(Range(2000, 2004), 2005, "integer")


def test_a_proposal_key_spelled_differently_still_reads_the_probe() -> None:
    proposal = probe_values(
        [{"name": "e0", "region": "East"}], ("North", "South"), TEXT, key="Region"
    )
    assert proposal.values == ("North", "South", "East")


UNTYPED = [Attr("name", "string"), Attr("region")]


def test_an_untyped_value_keeps_its_json_identity() -> None:
    # The number 1 is not the text "1", but 1 and 1.0 are one number.
    assert probe_values([{"name": "a", "region": 1}], ("1", "2"), UNTYPED).values == ("1", "2", 1)
    twice = [{"name": "a", "region": 1}, {"name": "b", "region": 1.0}]
    proposal = probe_values(twice, ("North", "South"), UNTYPED)
    assert proposal.values == ("North", "South", 1)
    assert proposal.child_estimates == (None, None, 2)


def test_a_single_span_proposal_adds_only_years_outside_it() -> None:
    years = [Attr("name", "string"), Attr("year", "integer")]
    proposal = probe_values(
        [{"name": "a", "year": 2000}, {"name": "b", "year": 2005}],
        ("2000-2004",),
        years,
        policy=PartitionPolicy(range_filters=True),
    )
    assert proposal.values == ("2000-2004", 2005)


def test_a_known_year_list_recovers_through_a_differently_spelled_range_proposal() -> None:
    # The request names 2000 and 2005; the model proposes "Year" as a span and a text year.
    attrs = [{"name": "name", "type": "string"}, {"name": "year", "type": "integer"}]

    def answer(prompt: str) -> Reply:
        if kind(prompt) == "bootstrap":
            return json.dumps(
                {
                    "ask": "stations in years 2000 and 2005",
                    "constraints": {"year": [2000, 2005]},
                    "row_estimate": 40,
                    "schema": {"attrs": attrs, "key": "name"},
                }
            )
        if kind(prompt) == "proposal":
            return json.dumps({"key": "Year", "values": ["2000-2004", "2005"], "ambiguity": 0})
        scope = json.loads(prompt.split("Request scope: ", 1)[1].splitlines()[0])["year"]
        if scope["kind"] != "exact":
            return json.dumps([{"name": "a", "year": 2000}, {"name": "b", "year": 2005}]), "length"
        year = int(scope["value"])
        return json.dumps([{"name": {2000: "c", 2005: "d"}[year], "year": year}])

    result = LlmSource(
        properties={"plan.planning_order": "planning.combined"}, client=ProbeClient(answer)
    ).fetch(
        "stations in years 2000 and 2005",
        attrs=attrs,
        properties={
            "plan.mode": "partitioned",
            "plan.max_continuations": 0,
            "plan.max_depth": 1,
            "plan.range_filters": True,
            "plan.leaf_prompt": "keyed-leaf-fetch-2",
            "plan.partition_key": "Year",
        },
    )
    assert {record["name"] for record in result.records} == {"a", "b", "c", "d"}


def test_a_requested_partition_key_matches_whatever_its_spelling() -> None:
    def answer(prompt: str) -> Reply:
        if kind(prompt) == "proposal":
            return json.dumps({"key": "Region", "values": ["North", "South"], "ambiguity": 0})
        return responder(estimate=40, root=(rows(["a"], "North"), "length"))(prompt)

    client, result = fetch(answer, **{"plan.partition_key": "region"})
    assert {record["name"] for record in result.records} == {"a", "n1", "n2", "s1"}
    within_bound(client, result)


def test_a_request_scope_spelled_differently_is_narrowed_in_place() -> None:
    # The analysis writes the scope as "Year"; the plan keys it by the schema's year column.
    attrs = [{"name": "name", "type": "string"}, {"name": "year", "type": "integer"}]

    def answer(prompt: str) -> Reply:
        if kind(prompt) == "bootstrap":
            return json.dumps(
                {
                    "ask": "stations in years 2000 and 2005",
                    "constraints": {"Year": ["2000", "2005"]},
                    "row_estimate": 40,
                    "schema": {"attrs": attrs, "key": "name"},
                }
            )
        if kind(prompt) == "proposal":
            return json.dumps({"key": "Year", "values": ["2000", "2005", "2010"], "ambiguity": 0})
        scope = json.loads(prompt.split("Request scope: ", 1)[1].splitlines()[0])
        assert set(scope) == {"year"}
        if scope["year"]["kind"] != "exact":
            return json.dumps([{"name": "a", "year": 2000}, {"name": "b", "year": 2005}]), "length"
        year = int(scope["year"]["value"])
        return json.dumps([{"name": {2000: "c", 2005: "d"}[year], "year": year}])

    result = LlmSource(
        properties={"plan.planning_order": "planning.combined"}, client=ProbeClient(answer)
    ).fetch(
        "stations in years 2000 and 2005",
        attrs=attrs,
        properties={
            "plan.mode": "partitioned",
            "plan.max_continuations": 0,
            "plan.max_depth": 1,
            "plan.max_leaves": 2,
            "plan.max_partitions": 3,
            "plan.leaf_prompt": "keyed-leaf-fetch-2",
            "plan.partition_key": "Year",
        },
    )
    assert {record["name"] for record in result.records} == {"a", "b", "c", "d"}
    assert result.meta.outcome == "ok"


@pytest.mark.parametrize(
    "scope",
    [
        {"Country": "US"},
        {"Country": "US", "COUNTRY": "US"},
        {"Country": "US", "tag": ["a", "b"], "Tag": ["a", "b"]},
    ],
    ids=["one-spelling", "two-spellings", "unrelated-collision"],
)
def test_a_field_fixed_under_several_spellings_still_closes_its_key(scope: dict[str, Any]) -> None:
    attrs = [
        {"name": "country", "type": "string"},
        {"name": "year", "type": "integer"},
        {"name": "tag", "type": "string"},
    ]
    every = [{"country": "US", "year": year, "tag": "a"} for year in (2000, 2005, 2010)]

    def answer(prompt: str) -> Reply:
        if kind(prompt) == "bootstrap":
            return json.dumps(
                {
                    "ask": "US records",
                    "constraints": scope,
                    "row_estimate": 3,
                    "schema": {"attrs": attrs, "primary_keys": ["country", "year"]},
                }
            )
        if kind(prompt) == "proposal":
            return json.dumps(
                {
                    "key": "year",
                    "values": ["2000", "2005"],
                    "child_estimates": [2, 1],
                    "ambiguity": 0,
                }
            )
        return json.dumps(every)

    result = LlmSource(
        properties={"plan.planning_order": "planning.combined"}, client=ProbeClient(answer)
    ).fetch(
        "US records",
        attrs=attrs,
        properties={
            "plan.mode": "partitioned",
            "plan.split_policy": "always",
            "plan.max_depth": 1,
            "plan.max_continuations": 0,
            "plan.leaf_prompt": "keyed-leaf-fetch-2",
        },
    )
    assert {record["year"] for record in result.records} == {2000, 2005, 2010}
    assert any("lists individual records" in gap for gap in result.meta.gaps)


def test_two_spellings_of_a_scoped_field_are_intersected() -> None:
    by_schema_name = coordinator._by_schema_name  # pyright: ignore[reportPrivateUsage]
    schema = Schema(attrs=[Attr("name", "string"), Attr("year", "integer")], primary_keys=("name",))
    scope = {"Year": OneOf((2000, 2005, 2010)), "YEAR": OneOf((2000, 2005))}
    assert by_schema_name(scope, schema) == {"year": OneOf((2000, 2005))}


@pytest.mark.parametrize("split_policy", ["always", "evidence"])
def test_contradicting_spellings_of_a_scope_stop_before_any_extraction(split_policy: str) -> None:
    attrs = [{"name": "name", "type": "string"}, {"name": "year", "type": "integer"}]

    def answer(prompt: str) -> Reply:
        if kind(prompt) == "bootstrap":
            return json.dumps(
                {
                    "ask": "stations",
                    "constraints": {"Year": [2000], "YEAR": [2005]},
                    "row_estimate": 40,
                    "schema": {"attrs": attrs, "key": "name"},
                }
            )
        raise AssertionError("an unsatisfiable scope asks nothing more")

    client = ProbeClient(answer)
    result = LlmSource(
        properties={"plan.planning_order": "planning.combined"}, client=client
    ).fetch(
        "stations",
        attrs=attrs,
        properties={"plan.mode": "partitioned", "plan.split_policy": split_policy},
    )
    assert kinds(client) == ["bootstrap"]
    assert result.records == []
    assert any("no value every spelling allows" in gap for gap in result.meta.gaps)
