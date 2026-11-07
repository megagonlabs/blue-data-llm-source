"""Estimate discrepancies remain visible without changing execution outcomes."""

from __future__ import annotations

import json
from typing import Any, cast

import pytest

from blue_data_llm_source import Attr, FetchResult, LlmSource, OneOf, Outcome, meta_view
from blue_data_llm_source.planning.bounds import PlanLimits
from blue_data_llm_source.planning.cardinality import estimate_from_reported
from blue_data_llm_source.planning.partition_safety import IdentityPartitionRule
from blue_data_llm_source.planning.plan import (
    ROOT,
    FetchPlan,
    Gap,
    PartitionProposal,
    apply_partition_proposal,
    root_plan,
)
from blue_data_llm_source.planning.schema import Schema

PLANETS = ("Mercury", "Venus", "Earth", "Mars", "Jupiter", "Saturn", "Uranus", "Neptune")
SIGNALS = ("split_coverage_short:", "observed_coverage_short:")


def estimate(lower: int | None = 8) -> dict[str, object]:
    return {"expected": 8, "lower": lower, "upper": 8, "row_unit": "planets"}


class PlanetModel:
    def __init__(
        self,
        *,
        cardinality: object = None,
        values: tuple[str, ...] = PLANETS[:2],
        child_estimates: object = (0, 0),
        keys: tuple[str, ...] = ("name",),
        records: dict[str, list[dict[str, Any]]] | None = None,
    ) -> None:
        self.cardinality = estimate() if cardinality is None else cardinality
        self.values = values
        self.child_estimates = child_estimates
        self.keys = keys
        self.records = records or {name: [{"name": name}] for name in values}
        self.attrs = tuple(next(iter(self.records.values()))[0])

    def __call__(self, prompt: str) -> str:
        if prompt.startswith("Your task is to prepare a data request"):
            return json.dumps(
                {"ask": "Identify the planets of the Solar System.", "constraints": {}}
            )
        if prompt.startswith("Resolve the output schema"):
            return json.dumps(
                {"attrs": [{"name": name} for name in self.attrs], "primary_keys": self.keys}
            )
        if prompt.startswith("Estimate the number of records"):
            return json.dumps(self.cardinality)
        if prompt.startswith("Propose one useful partition"):
            proposal: dict[str, object] = {
                "key": "name",
                "values": self.values,
                "ambiguity": 0,
            }
            if self.child_estimates is not None:
                proposal["child_estimates"] = self.child_estimates
            return json.dumps(proposal)
        if prompt.startswith("Estimate the distribution of records"):
            counts = cast("tuple[int | None, ...]", self.child_estimates)
            return json.dumps(
                {
                    "key": "name",
                    "partitions": [
                        {"value": name, "estimate": {"expected": count}}
                        for name, count in zip(self.values, counts, strict=True)
                    ],
                    "ambiguity": 0,
                    "reasoning": "scripted domain",
                }
            )
        for name in self.values:
            if f'"{name}"' in prompt:
                return json.dumps(self.records[name])
        return json.dumps([record for rows in self.records.values() for record in rows])


def fetch(
    model: PlanetModel,
    *,
    distribution: bool = False,
    overrides: dict[str, object] | None = None,
) -> FetchResult:
    props: dict[str, object] = {
        "plan.mode": "partitioned",
        "plan.split_policy": "always",
        "plan.max_continuations": 0,
        "plan.max_depth": 1,
        "plan.max_partitions": len(model.values),
    }
    if model.keys:
        props["plan.keys"] = list(model.keys)
    if distribution:
        props["plan.distribution_search"] = True
        props["plan.identity_partitions"] = [
            IdentityPartitionRule(
                dimension="name",
                ordered_primary_keys=model.keys,
                allowed_domain=model.values,
                provenance="caller_declared",
            ).to_dict()
        ]
    props.update(overrides or {})
    return LlmSource().fetch(
        "the planets of the solar system",
        attrs=list(model.attrs),
        llm_fn=model,
        properties=props,
    )


def signals(result: FetchResult) -> list[str]:
    return [gap for gap in result.meta.gaps if gap.startswith(SIGNALS)]


@pytest.mark.parametrize("distribution", [False, True])
def test_short_fallback_and_declared_domain_keep_estimate_signals(distribution: bool) -> None:
    result = fetch(PlanetModel(), distribution=distribution)
    assert {record["name"] for record in result.records} == {"Mercury", "Venus"}
    assert result.meta.outcome == Outcome.OK
    assert result.meta.observed_key_count == 2
    assert any(gap.startswith(SIGNALS[0]) for gap in result.meta.gaps) == distribution
    assert any(gap.startswith(SIGNALS[1]) for gap in result.meta.gaps)
    assert meta_view(result.meta)["gaps"] == result.meta.gaps


@pytest.mark.parametrize("children", [None, (), (None, 0), (True, 0), (-1, 0), ("x", 0), (4, 4)])
def test_final_count_reports_shortfall_when_child_estimates_cannot(children: object) -> None:
    result = fetch(PlanetModel(child_estimates=children))
    assert not any(gap.startswith(SIGNALS[0]) for gap in result.meta.gaps)
    assert any(gap.startswith(SIGNALS[1]) for gap in result.meta.gaps)


@pytest.mark.parametrize("distribution", [False, True])
def test_split_estimate_warning_does_not_assert_actual_record_loss(distribution: bool) -> None:
    result = fetch(PlanetModel(cardinality={"expected": 2, "lower": 2}), distribution=distribution)
    assert result.meta.observed_key_count == 2
    assert result.meta.outcome == Outcome.OK
    assert any(gap.startswith(SIGNALS[0]) for gap in result.meta.gaps) == distribution
    assert not any(gap.startswith(SIGNALS[1]) for gap in result.meta.gaps)


@pytest.mark.parametrize(
    "cardinality",
    [estimate(None), {"expected": 0, "lower": 0}, {"expected": 2, "lower": 8}],
)
def test_unknown_zero_or_invalid_parent_lower_does_not_create_signals(cardinality: object) -> None:
    assert signals(fetch(PlanetModel(cardinality=cardinality))) == []


@pytest.mark.parametrize("distribution", [False, True])
def test_complete_split_has_no_estimate_warning(distribution: bool) -> None:
    model = PlanetModel(values=PLANETS, child_estimates=(1,) * len(PLANETS))
    result = fetch(model, distribution=distribution)
    assert len(result.records) == 8
    assert signals(result) == []


def test_row_cap_uses_the_private_pre_cap_count_for_composite_identities() -> None:
    records = {
        "Mercury": [{"name": "Mercury", "system": "A"}, {"name": "Mercury", "system": "B"}],
        "Venus": [{"name": "Venus", "system": "A"}, {"name": "Venus", "system": "B"}],
    }
    result = fetch(
        PlanetModel(
            cardinality={"expected": 4, "lower": 4},
            child_estimates=(2, 2),
            keys=("name", "system"),
            records=records,
        ),
        overrides={"limits.max_rows": 1},
    )
    assert len(result.records) == 1
    assert result.meta.observed_key_count == 1
    assert result.meta.outcome == Outcome.TRUNCATED
    assert "max_rows" in result.meta.stop_reasons
    assert signals(result) == []


def test_unkeyed_result_has_no_observed_identity_warning() -> None:
    result = fetch(PlanetModel(keys=(), child_estimates=None))
    assert result.meta.observed_key_count == 0
    assert signals(result) == []


def test_incomplete_composite_keys_do_not_count_as_complete_identities() -> None:
    result = fetch(
        PlanetModel(
            cardinality={"expected": 2, "lower": 2},
            child_estimates=None,
            keys=("name", "system"),
            records={
                "Mercury": [{"name": "Mercury", "system": "A"}],
                "Venus": [{"name": "Venus", "system": None}],
            },
        )
    )
    assert len(result.records) == 2
    assert result.meta.observed_key_count == 1
    assert any(gap.startswith("observed_coverage_short: 1 distinct") for gap in result.meta.gaps)


def test_unsplit_fallback_still_reports_the_observed_estimate_discrepancy() -> None:
    result = fetch(PlanetModel(), overrides={"plan.max_depth": 0})
    assert not any(gap.startswith(SIGNALS[0]) for gap in result.meta.gaps)
    assert any(gap.startswith(SIGNALS[1]) for gap in result.meta.gaps)


def test_flat_result_does_not_use_a_planning_estimate() -> None:
    result = fetch(PlanetModel(), overrides={"plan.mode": "flat"})
    assert result.meta.cardinality_estimate is None
    assert signals(result) == []


def test_split_estimate_uses_only_children_retained_by_known_domain_intersection() -> None:
    plan = root_plan(
        mode="partitioned",
        original_query="planets",
        retrieval_query="planets",
        constraints={"name": OneOf(PLANETS[:2])},
        estimate=8,
        cardinality=estimate_from_reported(estimate()),
    )
    candidate = apply_partition_proposal(
        plan,
        leaf_id=ROOT,
        proposal=PartitionProposal(
            key="name",
            values=PLANETS[:3],
            child_estimates=(0, 0, 100),
        ),
        schema=Schema(attrs=[Attr("name")], primary_keys=("name",)),
        limits=PlanLimits(max_partitions=3),
    )
    assert len(candidate.leaves) == 2
    notes = [note for note in candidate.notes if note.code == Gap.SPLIT_COVERAGE_SHORT]
    assert len(notes) == 1
    assert notes[0].target_id == ROOT
    assert "total 0" in notes[0].detail
    assert FetchPlan.from_dict(candidate.to_dict()).notes == candidate.notes
