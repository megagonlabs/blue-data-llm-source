"""A refused proposal or distribution call keeps the answer the root probe already gave."""

from __future__ import annotations

import json
from typing import Any, cast

import pytest

from blue_data_llm_source import BudgetExceeded, LlmSource
from blue_data_llm_source.llm.budget import Budget, Limits
from blue_data_llm_source.planning.partition_safety import IdentityPartitionRule
from blue_data_llm_source.planning.receipt import PlanningReceipt
from blue_data_llm_source.types import Exact, LlmSourceError, Usage
from tests.unit.test_split_policy import ProbeClient, Reply, kind, responder, rows


@pytest.mark.parametrize("order", ["planning.combined", "planning.schema_first"])
@pytest.mark.parametrize("limit", ["max_seconds", "max_cost", "max_calls"])
@pytest.mark.parametrize("root", [rows(["a"]), rows(["a", "b", "c"])])
def test_answer_survives_proposal_refusal(limit: str, root: str, order: str) -> None:
    answer = responder(estimate=300, root=root)

    def refuse(prompt: str) -> Reply:
        if prompt.startswith("Your task is to prepare"):
            return json.dumps({"ask": "stations", "constraints": {}})
        if prompt.startswith("Resolve the output schema"):
            return json.dumps(
                {"attrs": [{"name": "name"}, {"name": "region"}], "primary_keys": ["name"]}
            )
        if prompt.startswith("Estimate the number of records"):
            return json.dumps({"expected": 300, "lower": 300, "upper": 300})
        if kind(prompt) == "proposal":
            raise BudgetExceeded("synthetic proposal refusal", limit=limit)
        return answer(prompt)

    client = ProbeClient(refuse)
    result = LlmSource(client=client).fetch(
        "stations",
        attrs=["name", "region"],
        properties={
            "plan.mode": "partitioned",
            "plan.max_continuations": 0,
            "plan.split_policy": "evidence",
            "plan.planning_order": order,
            "limits.max_rows": 2,
        },
    )
    assert result.records == json.loads(root)[:2]
    assert result.meta.calls == (3 if order == "planning.combined" else 5)
    assert result.meta.outcome == "partial"
    assert result.meta.stopped_by == limit
    assert result.meta.stop_reasons == (
        [limit, "max_rows"] if len(json.loads(root)) > 2 else [limit]
    )
    assert result.meta.primary_keys == ["name"]
    assert result.meta.split_decision == "probe_split_shortfall"
    assert result.meta.record_origins == [["0"]] * len(result.records)
    assert any("planning_unserved:" in gap for gap in result.meta.gaps)


@pytest.mark.parametrize("row_only", [False, True])
def test_failed_probe_keeps_original_failure(row_only: bool) -> None:
    answer = responder(estimate=300, root="[]")

    def refuse(prompt: str) -> Reply:
        if kind(prompt) == "proposal":
            raise BudgetExceeded("synthetic proposal refusal", limit="max_seconds")
        if kind(prompt) == "fetch":
            raise RuntimeError("synthetic extraction failure")
        return answer(prompt)

    source = LlmSource(
        client=ProbeClient(refuse),
        properties={
            "plan.mode": "partitioned",
            "plan.max_continuations": 0,
            "plan.split_policy": "evidence",
            "plan.planning_order": "planning.combined",
            "limits.max_retries": 0,
        },
    )
    with pytest.raises(LlmSourceError) as caught:
        if row_only:
            source.execute_query(
                {"op": "LLM.FETCH", "query": "stations", "attrs": ["name", "region"]}
            )
        else:
            source.fetch("stations", attrs=["name", "region"])
    assert not isinstance(caught.value, BudgetExceeded)
    assert isinstance(caught.value.__cause__, RuntimeError)


@pytest.mark.parametrize("after_split", [False, True])
def test_distribution_refusal_keeps_probe_and_receipt(after_split: bool) -> None:
    answer = responder(estimate=300, root=rows(["a"], "North"))
    proposals = 0

    def refuse(prompt: str) -> Reply:
        nonlocal proposals
        if prompt.startswith("Estimate the distribution"):
            proposals += 1
            if not after_split or proposals > 1:
                raise BudgetExceeded("synthetic distribution refusal", limit="max_seconds")
            return json.dumps(
                {
                    "key": "region",
                    "partitions": [
                        {"value": "North", "estimate": {"expected": 150}},
                        {"value": "South", "estimate": {"expected": 150}},
                    ],
                    "ambiguity": 0,
                    "reasoning": "declared domain",
                }
            )
        return answer(prompt)

    rules = [
        IdentityPartitionRule(
            dimension="region",
            ordered_primary_keys=("region", "name"),
            allowed_domain=("North", "South"),
        ).to_dict()
    ]
    if after_split:
        rules.append(
            IdentityPartitionRule(
                dimension="name",
                ordered_primary_keys=("region", "name"),
                allowed_domain=("a", "b"),
                parent_scope={"region": Exact("North")},
            ).to_dict()
        )
    client = ProbeClient(refuse)
    result = LlmSource(client=client).fetch(
        "stations",
        attrs=["name", "region"],
        properties={
            "plan.mode": "partitioned",
            "plan.max_continuations": 0,
            "plan.split_policy": "evidence",
            "plan.planning_order": "planning.combined",
            "plan.keys": ["region", "name"],
            "plan.distribution_search": True,
            "plan.identity_partitions": rules,
        },
    )
    assert result.records == [{"name": "a", "region": "North"}]
    assert result.meta.outcome == "partial"
    assert result.meta.stopped_by == "max_seconds"
    assert result.planning_receipt is not None
    receipt = PlanningReceipt.from_dict(result.planning_receipt.to_dict())
    assert receipt.facts.proposal_attempts == (2 if after_split else 1)
    assert receipt.facts.distribution_attempts == (2 if after_split else 1)
    assert receipt.facts.accepted_internal_nodes == int(after_split)
    assert len(receipt.plan.leaves) == (2 if after_split else 1)
    assert receipt.facts.context_digest is not None
    assert result.meta.record_origins == [["root" if after_split else "0"]]
    assert len(client.prompts) == (4 if after_split else 3)


@pytest.mark.parametrize("failed", [False, True])
def test_ordinary_refusal_after_accepted_split(failed: bool) -> None:
    answer = responder(estimate=300, root=(rows(["a"]), "length"))
    proposals = 0

    def refuse(prompt: str) -> Reply:
        nonlocal proposals
        if kind(prompt) == "fetch" and failed:
            raise RuntimeError("synthetic extraction failure")
        if kind(prompt) == "proposal":
            proposals += 1
            if proposals > 1:
                raise BudgetExceeded("synthetic proposal refusal", limit="max_cost")
        return answer(prompt)

    source = LlmSource(
        client=ProbeClient(refuse),
        properties={
            "plan.mode": "partitioned",
            "plan.max_continuations": 0,
            "plan.split_policy": "evidence",
            "plan.planning_order": "planning.combined",
        },
    )
    if failed:
        with pytest.raises(LlmSourceError) as caught:
            source.fetch("stations", attrs=["name", "region"])
        assert isinstance(caught.value.__cause__, RuntimeError)
    else:
        result = source.fetch("stations", attrs=["name", "region"])
        assert result.records == [{"name": "a", "region": None}]
        assert result.meta.outcome == "partial"
        assert result.meta.stop_reasons == ["max_cost", "provider_output_limit"]
        assert result.meta.record_origins == [["root"]]


def test_answered_empty_root_survives_proposal_refusal() -> None:
    answer = responder(estimate=300, root="[]")

    def refuse(prompt: str) -> Reply:
        if kind(prompt) == "proposal":
            raise BudgetExceeded("synthetic proposal refusal", limit="max_calls")
        return answer(prompt)

    source = LlmSource(
        client=ProbeClient(refuse),
        properties={
            "plan.mode": "partitioned",
            "plan.max_continuations": 0,
            "plan.split_policy": "evidence",
            "plan.planning_order": "planning.combined",
        },
    )
    result = source.fetch("stations", attrs=["name", "region"])
    assert result.records == []
    assert result.meta.stop_reasons == ["max_calls"]
    assert source.execute_query({"op": "LLM.FETCH", "query": "stations", "attrs": ["name"]}) == []


def test_trusted_zero_needs_no_probe_or_proposal() -> None:
    client = ProbeClient(responder(estimate=300, root=rows(["a"])))
    result = LlmSource(client=client).fetch(
        "stations",
        attrs=["name", "region"],
        properties={
            "plan.mode": "partitioned",
            "plan.max_continuations": 0,
            "plan.split_policy": "evidence",
            "plan.planning_order": "planning.combined",
            "plan.exact_rows": 0,
        },
    )
    assert result.records == []
    assert [kind(prompt) for prompt in client.prompts] == ["bootstrap"]


def test_real_deadline_after_probe() -> None:
    now = [0.0]
    budget = Budget(Limits(max_seconds=1), clock=lambda: now[0])
    answer = responder(estimate=300, root=rows(["a"]))

    def refuse(prompt: str) -> Reply:
        if kind(prompt) == "proposal":
            now[0] = 2.0
            budget.check_deadline()
        return answer(prompt)

    result = LlmSource(client=ProbeClient(refuse)).fetch(
        "stations",
        attrs=["name", "region"],
        budget=budget,
        properties={
            "plan.mode": "partitioned",
            "plan.max_continuations": 0,
            "plan.split_policy": "evidence",
            "plan.planning_order": "planning.combined",
        },
    )
    assert result.records == [{"name": "a", "region": None}]
    assert result.meta.calls == 3
    assert result.meta.stop_reasons == ["max_seconds"]
    assert budget.stopped_by == "max_seconds"


@pytest.mark.parametrize("root", ["[]", rows(["a"])])
def test_cached_probe_survives_proposal_refusal(root: str) -> None:
    answer = responder(estimate=300, root=root)

    def refuse(prompt: str) -> Reply:
        if kind(prompt) == "proposal":
            raise BudgetExceeded("synthetic proposal refusal", limit="max_calls")
        return answer(prompt)

    class CachedProbeClient(ProbeClient):
        async def acomplete(self, prompt: str, properties: dict[str, Any] | None = None) -> str:
            if kind(prompt) != "fetch":
                return await super().acomplete(prompt, properties)
            assert properties is not None
            budget = properties["budget"]
            assert isinstance(budget, Budget)
            with budget.attempt() as reservation:
                self.prompts.append(prompt)
                reservation.settle(0, cache_hit=True)
            cast("list[Usage]", properties["usage_sink"]).append(Usage(cache_hit=True))
            return root

    result = LlmSource(client=CachedProbeClient(refuse)).fetch(
        "stations",
        attrs=["name", "region"],
        properties={
            "plan.mode": "partitioned",
            "plan.max_continuations": 0,
            "plan.split_policy": "evidence",
            "plan.planning_order": "planning.combined",
        },
    )
    assert result.records == json.loads(root)
    assert result.meta.calls == 2
    assert result.meta.cache_hits == 1
    assert result.meta.stop_reasons == ["max_calls"]
