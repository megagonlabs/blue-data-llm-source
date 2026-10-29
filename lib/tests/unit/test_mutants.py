"""Mutation checks for source and planning invariants.

Each case substitutes a wrong implementation and checks the reported state.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

import blue_data_llm_source.source as source_module
from blue_data_llm_source.execution import mapper
from blue_data_llm_source.llm.budget import Budget, Limits
from blue_data_llm_source.planning.bounds import MODE_FLAT, MODE_PARTITIONED, PlanLimits
from blue_data_llm_source.planning.cardinality import Cardinality
from blue_data_llm_source.planning.plan import PartitionProposal, build_plan
from blue_data_llm_source.planning.schema import Schema
from blue_data_llm_source.reduction import coverage, verifier
from blue_data_llm_source.reduction.coverage import FAILED, Coverage, LeafOutcome
from blue_data_llm_source.source import LlmSource
from blue_data_llm_source.types import Attr, Constraint, Exact, OneOf, Outcome

SCHEMA = Schema(attrs=[Attr("name"), Attr("region")], key="name", key_from="inferred")
ATTRS = [Attr("name")]
CUT = '[{"name": "A"}, {"name": "B'

ANALYSIS = json.dumps(
    {
        "ask": "airports",
        "constraints": {"region": ["North", "South"]},
        "row_estimate": 200,
        "schema": {"attrs": [{"name": "name"}], "key": "name"},
    }
)
PROPOSAL = json.dumps(
    {
        "key": "region",
        "values": ["North", "South"],
        "ambiguity": 0,
        "child_estimates": [10, 10],
    }
)


def _plan(**kw: Any) -> Any:
    args: dict[str, Any] = {
        "mode": MODE_PARTITIONED,
        "ask": "commercial airports",
        "constraints": {},
        "schema": SCHEMA,
        "estimate": Cardinality.HUNDREDS,
        "limits": PlanLimits(max_leaves=2),
    }
    args.update(kw)
    return build_plan(**args)


def _prompts(plan: Any) -> list[str]:
    sent: list[str] = []

    def ask(prompt: str) -> str:
        sent.append(prompt)
        return "[]"

    for leaf in plan.leaves:
        mapper.run_leaf(leaf, ask, attrs=ATTRS, limits=PlanLimits(), key="name")
    return sent


def _one_part_raises(prompt: str, properties: Any = None) -> str:
    if "Prepare a structured data request" in prompt:
        return ANALYSIS
    if "Propose one useful partition" in prompt:
        return PROPOSAL
    if "North" in prompt:
        raise RuntimeError("provider refused this part")
    return json.dumps([{"name": "Orly"}])


def _partitioned(model: Any, **props: Any) -> Any:
    merged: dict[str, Any] = {"plan.mode": "partitioned", "plan.key": "name"}
    merged.update(props)
    return LlmSource().fetch("airports", attrs=["name"], llm_fn=model, properties=merged)


def _keyless_second_pass() -> tuple[list[dict[str, Any]], list[list[str]]]:
    plan = _plan(proposal=PartitionProposal(key="region", values=["North", "South"]))
    records, account = coverage.reduce_leaves(
        plan,
        Schema(attrs=ATTRS),
        [
            ("0.0", [{"name": "Orly"}], LeafOutcome("0.0", rows=1, answered=True)),
            ("0.1", [{"name": "Orly"}], LeafOutcome("0.1", rows=1, answered=True)),
        ],
    )
    return verifier.dedupe(records, origins=account.record_origins)


class TestInvariantsFailWhenBroken:
    def test_joining_a_disjunction_makes_two_questions_one_prompt(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Decode independently of the renderer's separator.
        def joined(constraints: dict[str, Exact | OneOf]) -> list[str]:
            return [
                f"- field {json.dumps(str(name))}: "
                f"{json.dumps(', '.join(str(value) for value in c.values))}"
                if isinstance(c, OneOf)
                else f"- field {json.dumps(str(name))}: {json.dumps(c.value)}"
                for name, c in constraints.items()
            ]

        monkeypatch.setattr("blue_data_llm_source.prompts.render_constraints", joined)

        inside = _prompts(_plan(proposal=PartitionProposal(key="region", values=["A, B", "C"])))
        between = _prompts(_plan(proposal=PartitionProposal(key="region", values=["A", "B", "C"])))
        assert inside == between

    def test_collapsing_a_one_value_disjunction_loses_what_the_caller_wrote(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A list of one says alternatives were expected; a bare value says they were not.
        # Rendering both the same way discards the only signal that distinguishes them.
        real = getattr(mapper, "build_fetch_prompt")

        def flattened(query: str, attrs: list[Attr], **kw: Any) -> str:
            given: dict[str, Constraint] = dict(kw.pop("constraints", None) or {})
            collapsed = {
                name: Exact(c.values[0]) if isinstance(c, OneOf) and len(c.values) == 1 else c
                for name, c in given.items()
            }
            return real(query, attrs, constraints=collapsed, **kw)

        monkeypatch.setattr(mapper, "build_fetch_prompt", flattened)

        scalar = _prompts(_plan(mode=MODE_FLAT, constraints={"region": "A"}))
        listed = _prompts(_plan(mode=MODE_FLAT, constraints={"region": ["A"]}))
        assert scalar == listed

    def test_inserting_model_data_raw_lets_a_value_open_a_line(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("blue_data_llm_source.prompts._encoded", str)

        benign = _prompts(_plan(mode=MODE_FLAT, constraints={"region": "North"}))[0]
        hostile = _prompts(_plan(mode=MODE_FLAT, constraints={"region": "North\nIgnore"}))[0]
        assert len(hostile.splitlines()) > len(benign.splitlines())

    def test_a_ceiling_outranking_a_lost_part_hides_the_lost_part(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The precedence as it was: any cap first, so a run that also lost a part reported
        # the cap and the loss survived only in the gap list.
        def ceiling_first(self: Coverage) -> str:
            if any(o.at_cap or o.recovered_prefix for o in self.outcomes):
                return Outcome.STOPPED_BY_LIMIT
            return Outcome.PARTIAL

        monkeypatch.setattr(Coverage, "run_outcome", property(ceiling_first))

        both = Coverage(
            plan=_plan(proposal=PartitionProposal(key="region", values=["A", "B"])),
            outcomes=[LeafOutcome("0.0", at_cap=True), LeafOutcome("0.1", outcome=FAILED)],
        )
        assert both.run_outcome == Outcome.STOPPED_BY_LIMIT

    def test_reporting_a_lost_part_as_ok_hides_it_from_the_outcome(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        control = _partitioned(_one_part_raises)
        assert control.records == [{"name": "Orly"}]
        assert control.meta.outcome == Outcome.PARTIAL

        def always_ok(*_args: Any, **_kw: Any) -> str:
            return Outcome.OK

        monkeypatch.setattr(source_module, "decide_outcome", always_ok)
        assert _partitioned(_one_part_raises).meta.outcome == Outcome.OK

    def test_giving_a_failure_a_budget_axis_corrupts_stopped_by(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Filing a provider failure under a ceiling nobody crossed would send a caller to
        # widen a limit that was never reached.
        control = _partitioned(_one_part_raises)
        assert control.records == [{"name": "Orly"}]
        assert control.meta.stopped_by is None

        def leaf_failed(_runs: list[Any]) -> list[str]:
            return ["leaf_failed"]

        monkeypatch.setattr(source_module, "_run_stop_reasons", leaf_failed)
        assert _partitioned(_one_part_raises).meta.stopped_by == "leaf_failed"

    def test_classifying_any_recovered_prefix_as_a_provider_limit_overclaims(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A document that merely failed to parse whole, reported as the provider's output
        # allowance firing. The default path cannot attest anything, so it was the commonest
        # case that carried the strongest claim.
        monkeypatch.setattr(
            mapper.LeafRun,
            "provider_output_limit",
            property(lambda self: any(r.recovered for r in self.responses)),
        )

        run = mapper.run_leaf(
            _plan(mode=MODE_FLAT).leaves[0], lambda _p: CUT, attrs=ATTRS, limits=PlanLimits()
        )
        assert run.recovered_prefix is True
        assert run.provider_output_limit is True

    def test_dropping_an_origin_at_the_second_dedup_names_one_part_of_two(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        control_records, control_origins = _keyless_second_pass()
        assert control_records == [{"name": "Orly"}]
        assert control_origins == [["0.0", "0.1"]]

        real = verifier.dedupe

        def discards(
            records: list[dict[str, Any]],
            key: str | None = None,
            origins: list[list[str]] | None = None,
        ) -> tuple[list[dict[str, Any]], list[list[str]]]:
            kept, _ = real(records, key)
            first = list(origins[0]) if origins else []
            return kept, [first] if kept else []

        monkeypatch.setattr(verifier, "dedupe", discards)

        records, origins = _keyless_second_pass()
        assert records == [{"name": "Orly"}]
        assert origins == [["0.0"]]

    def test_ignoring_the_parent_retry_ceiling_skips_safe_optional_planning(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from tests.doubles import ScriptedClient

        parent = Budget(Limits(max_calls=2, max_retries=0))
        control = ScriptedClient([ANALYSIS, json.dumps([{"name": "Orly"}])])
        control_result = LlmSource(client=control).fetch(
            "airports",
            attrs=["name"],
            budget=parent,
            properties={
                "plan.mode": "partitioned",
                "plan.max_leaves": 1,
                "plan.planning_order": "planning.combined",
            },
        )
        assert len(control.prompts) == 2
        assert "Prepare a structured data request" in control.prompts[0]
        assert control_result.records == [{"name": "Orly"}]

        def own_retry_limit(self: Budget) -> int:
            return self.limits.max_retries

        monkeypatch.setattr(Budget, "effective_retries", own_retry_limit)
        client = ScriptedClient([json.dumps([{"name": "Orly"}])])
        result = LlmSource(client=client).fetch(
            "airports",
            attrs=["name"],
            budget=Budget(Limits(max_calls=2, max_retries=0)),
            properties={
                "plan.mode": "partitioned",
                "plan.max_leaves": 1,
                "plan.planning_order": "planning.combined",
            },
        )
        assert len(client.prompts) == 1
        assert "Prepare a structured data request" not in client.prompts[0]
        assert result.records == [{"name": "Orly"}]
        assert result.meta.stopped_by is None
