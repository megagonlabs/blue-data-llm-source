"""What a multi-value constraint must survive on its way to the model.

Every assertion here is made against the prompt text a leaf actually sends, because that
is the whole of what the model sees and therefore the whole of what a caller's question
becomes. A test reading ``Leaf.constraints`` instead would pass on a representation that
is unambiguous in memory and ambiguous by the time it is asked.

The decoder below is deliberately not the renderer run backwards. A test that recovers a
list by splitting on the separator the renderer joined with agrees with any separator the
renderer chooses, including one that appears inside the values -- which is the defect this
file exists to make impossible. ``json`` is the independent half: it is not the code under
test, and it refuses an ambiguous document rather than guessing at one.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from blue_data_llm_source.execution.mapper import run_leaf
from blue_data_llm_source.planning.bounds import (
    MODE_FLAT,
    MODE_PARTITIONED,
    MODE_ROW_BY_ROW,
    PlanLimits,
)
from blue_data_llm_source.planning.cardinality import Cardinality
from blue_data_llm_source.planning.plan import FetchPlan, PartitionProposal, build_plan
from blue_data_llm_source.planning.schema import Schema
from blue_data_llm_source.types import Attr, ConfigError

ASK = "commercial airports"
SCHEMA = Schema(attrs=[Attr(name="name"), Attr(name="region")], key="name", key_from="inferred")
ATTRS = [Attr(name="name"), Attr(name="region")]

# Two readings of the same rendered text. The first names one region whose name contains a
# comma; the second names two regions. Nothing downstream can recover which was meant, so
# the two must not arrive as the same prompt.
COMMA_INSIDE = ["A, B", "C"]
COMMA_BETWEEN = ["A", "B", "C"]


def plan(**kw: Any) -> FetchPlan:
    args: dict[str, Any] = {
        "mode": MODE_PARTITIONED,
        "ask": ASK,
        "constraints": {},
        "schema": SCHEMA,
        "estimate": Cardinality.HUNDREDS,
        "limits": PlanLimits(max_leaves=2),
    }
    args.update(kw)
    return build_plan(**args)


def prompts(p: FetchPlan, limits: PlanLimits | None = None) -> list[str]:
    """The prompt each leaf of *p* opens with, in plan order."""
    out: list[str] = []

    def ask(prompt: str) -> str:
        out.append(prompt)
        return "[]"

    for leaf in p.leaves:
        run_leaf(leaf, ask, attrs=ATTRS, limits=limits or PlanLimits(), key="name")
    return out


def constraint_block(prompt: str) -> list[str]:
    """The constraint lines of a rendered prompt, without deciding what they mean."""
    lines = prompt.splitlines()
    if "Constraints:" not in lines:
        return []
    start = lines.index("Constraints:") + 1
    out: list[str] = []
    for line in lines[start:]:
        if not line.startswith("- "):
            break
        out.append(line)
    return out


def decode(line: str) -> tuple[str, str | list[str]]:
    """One rendered constraint back into the name and value it was built from.

    Independent of the renderer by construction: the operator words are matched literally
    and both operands go through ``json``, which either yields exactly one value or raises.
    Nothing here splits, strips, or partitions on a separator, so a value that contains the
    separator decodes to itself rather than to the pieces it looks like.
    """
    body = line.removeprefix("- ").removeprefix("field ")
    scanner = json.JSONDecoder()
    name, at = scanner.raw_decode(body)
    rest = body[at:]
    for operator, is_list in (("must equal one of ", True), ("must equal ", False)):
        if rest.startswith(f" {operator}"):
            value, _ = scanner.raw_decode(rest[len(operator) + 1 :])
            if is_list is isinstance(value, list) and isinstance(name, str):
                return name, value
            break
    raise AssertionError(f"not a decodable constraint line: {line!r}")


def constraints_of(prompt: str) -> dict[str, str | list[str]]:
    return dict(decode(line) for line in constraint_block(prompt))


class TestDistinctAsksStayDistinct:
    """The invariant a caller depends on: a different question sends a different prompt."""

    def test_a_comma_inside_a_value_is_not_a_value_boundary(self) -> None:
        # The adversarial twin. Neither list is unusual -- region names with commas are
        # ordinary -- and the two ask different questions. One prompt for both means the
        # model answers a question nobody asked and nothing in the result says so.
        inside = prompts(plan(proposal=PartitionProposal(key="region", values=COMMA_INSIDE)))
        between = prompts(plan(proposal=PartitionProposal(key="region", values=COMMA_BETWEEN)))
        assert inside != between

    def test_grouping_several_values_into_one_leaf_keeps_them_separable(self) -> None:
        # More values than leaves, so the values run grouped. Grouping is allowed to cost
        # granularity; it is not allowed to cost the boundary between one value and the
        # next.
        grouped = plan(
            proposal=PartitionProposal(key="region", values=["A", "B", "C", "D"]),
            limits=PlanLimits(max_leaves=2),
        )
        rendered = prompts(grouped)
        assert len(rendered) == 2
        recovered = [constraints_of(p)["region"] for p in rendered]
        assert recovered == [["A", "B"], ["C", "D"]]

    def test_every_planned_value_reaches_exactly_one_leaf(self) -> None:
        # The claim this file is allowed to make. Not that the enumeration is exhaustive
        # and not that the model will honour it: only that each distinct alternative the
        # plan enumerated is encoded in exactly one leaf's disjunction.
        values = [f"v{i}" for i in range(7)]
        p = plan(
            proposal=PartitionProposal(key="region", values=values),
            limits=PlanLimits(max_leaves=3),
        )
        encoded: list[str] = []
        for rendered in prompts(p):
            value = constraints_of(rendered)["region"]
            encoded.extend(value if isinstance(value, list) else [value])
        assert sorted(encoded) == sorted(values)
        assert len(encoded) == len(values)


class TestValuesDecodeToWhatWasPlanned:
    """Rendering is reversible into the exact values, not into something that looks like them."""

    @pytest.mark.parametrize(
        "value",
        [
            "A, B",
            'a "quoted" name',
            "line\nbreak",
            "trailing space ",
            "]",
        ],
    )
    def test_a_single_value_decodes_to_itself(self, value: str) -> None:
        p = plan(mode=MODE_FLAT, constraints={"region": value})
        assert constraints_of(prompts(p)[0])["region"] == value

    def test_an_empty_constraint_value_is_rejected(self) -> None:
        with pytest.raises(ConfigError, match="must not be empty"):
            plan(mode=MODE_FLAT, constraints={"region": ""})

    def test_a_list_constraint_the_plan_does_not_split_on_stays_a_list(self) -> None:
        # A list-valued filter is a disjunction wherever it appears, not only where a
        # partition happens to split on it. Flattened here it becomes one long value, and
        # the model is asked to match a string no record carries.
        p = plan(mode=MODE_FLAT, constraints={"region": COMMA_INSIDE})
        assert constraints_of(prompts(p)[0])["region"] == COMMA_INSIDE

    def test_a_scalar_and_a_one_value_list_are_not_the_same_constraint(self) -> None:
        # The degenerate case of the same defect: a one-element disjunction that renders
        # as a bare value has lost the fact that it was a disjunction, which is what tells
        # a reader whether more alternatives were expected.
        scalar = prompts(plan(mode=MODE_FLAT, constraints={"region": "A"}))
        listed = prompts(plan(mode=MODE_FLAT, constraints={"region": ["A"]}))
        assert scalar != listed

    def test_row_by_row_carries_one_named_entity_per_leaf(self) -> None:
        # The mode whose whole contract is one entity per leaf. Each leaf's own value is a
        # single alternative even though the constraint it came from held several.
        p = plan(
            mode=MODE_ROW_BY_ROW,
            constraints={"region": COMMA_INSIDE},
            limits=PlanLimits(max_leaves=4),
        )
        recovered = [constraints_of(rendered)["region"] for rendered in prompts(p)]
        assert recovered == COMMA_INSIDE


class TestTheDegenerateDisjunction:
    """An enumeration that named nothing is not the same as no enumeration.

    **A unit test of ``_filters``, not the contract probe.** These call ``build_plan``
    directly, and an earlier version of this class was cited as the guard for the
    consumer-visible behaviour — while the analysis parser was dropping an empty list before
    the planner could ever receive one, so it proved a note fires for an input the public path
    could not produce. The contract now has its own probe at the public entry point in
    ``tests/unit/test_dropped_constraint.py``; what is left here is the narrower question of
    what ``_filters`` does with a value it is handed.
    """

    def test_an_empty_disjunction_is_not_the_same_plan_as_no_constraint(self) -> None:
        # The adversarial twin: a caller who named a filter and a caller who named none must
        # not receive the same plan, whatever the algebra decides an empty disjunction means.
        emptied = plan(mode=MODE_FLAT, constraints={"region": [], "country": "FR"})
        absent = plan(mode=MODE_FLAT, constraints={"country": "FR"})
        assert (prompts(emptied), emptied.notes) != (prompts(absent), absent.notes)

    def test_dropping_an_empty_disjunction_is_reported(self) -> None:
        # Which code carries it is a design decision; that the widening is visible at all is
        # not. Today the filter vanishes and nothing is said.
        emptied = plan(mode=MODE_FLAT, constraints={"region": [], "country": "FR"})
        assert any("region" in note.detail for note in emptied.notes)


class TestTheDecoderCannotBeSatisfiedByAccident:
    """The oracle's own guard.

    A decoder that quietly accepts a malformed line would let the tests above pass on a
    renderer that had stopped rendering. These pin what it refuses.
    """

    def test_a_joined_disjunction_does_not_decode_as_a_list(self) -> None:
        with pytest.raises(AssertionError):
            decode('- field "region" must equal one of "A, B, C"')

    def test_a_bare_colon_rendering_does_not_decode(self) -> None:
        with pytest.raises(AssertionError):
            decode('- "region": "A, B"')

    def test_a_list_decodes_to_its_elements_and_not_to_its_text(self) -> None:
        name, value = decode('- field "region" must equal one of ["A, B", "C"]')
        assert (name, value) == ("region", ["A, B", "C"])
