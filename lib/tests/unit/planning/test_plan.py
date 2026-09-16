"""The plan, and the guards that decide how much a request costs.

Three of the prototype's defects were guards that could never execute, so every guard here
has a case that reaches it and asserts the note it leaves. A guard without a test that
fires it is assumed broken.
"""

from __future__ import annotations

from typing import Any

import pytest

from blue_data_llm_source.planning.bounds import (
    MODE_FLAT,
    MODE_PARTITIONED,
    MODE_ROW_BY_ROW,
    PlanLimits,
)
from blue_data_llm_source.planning.cardinality import Cardinality
from blue_data_llm_source.planning.plan import (
    ROOT,
    FetchPlan,
    Gap,
    PartitionProposal,
    build_plan,
)
from blue_data_llm_source.planning.schema import Schema
from blue_data_llm_source.types import Attr, ConfigError, Exact, OneOf

ASK = "commercial airports"
SCHEMA = Schema(attrs=[Attr(name="name"), Attr(name="country")], key="name", key_from="inferred")


def plan(**kw: Any) -> FetchPlan:
    args: dict[str, Any] = {
        "mode": MODE_PARTITIONED,
        "ask": ASK,
        "constraints": {},
        "schema": SCHEMA,
        "estimate": Cardinality.HUNDREDS,
        "limits": PlanLimits(),
    }
    args.update(kw)
    return build_plan(**args)


def codes(p: FetchPlan) -> list[str]:
    return [n.code for n in p.notes]


class TestFlat:
    def test_a_flat_plan_is_a_plan_with_one_leaf(self) -> None:
        p = plan(mode=MODE_FLAT)
        assert p.leaf_count == 1
        assert p.nodes == []
        assert p.leaves[0].query == ASK

    def test_flat_never_enumerates_even_with_a_proposal_in_hand(self) -> None:
        p = plan(mode=MODE_FLAT, proposal=PartitionProposal(key="country", values=["FR", "DE"]))
        assert p.leaf_count == 1
        assert p.nodes == []

    def test_scalar_constraints_reach_the_leaf(self) -> None:
        p = plan(mode=MODE_FLAT, constraints={"year": "2023"})
        assert p.leaves[0].constraints == {"year": Exact("2023")}

    def test_json_scalar_constraints_keep_their_types(self) -> None:
        p = plan(mode=MODE_FLAT, constraints={"year": [2023, 2024], "active": True})
        assert p.leaves[0].constraints == {
            "year": OneOf((2023, 2024)),
            "active": Exact(True),
        }

    def test_a_list_constraint_is_still_a_filter(self) -> None:
        """A list is what a split reads as its entity set, and a filter besides. Dropping it
        because this plan does not split asks the unnarrowed question and says nothing."""
        p = plan(mode=MODE_FLAT, constraints={"country": ["FR", "DE"]})
        assert p.leaves[0].constraints == {"country": OneOf(("FR", "DE"))}

    def test_a_single_valued_list_survives_the_collapse(self) -> None:
        """The shape the guard sends here: one value buys no coverage from a split, which
        is a reason not to split and not a reason to forget what was named."""
        p = plan(proposal=PartitionProposal(key="region", values=["Corsica"]))
        assert p.leaf_count == 1
        assert p.leaves[0].constraints == {}, "the proposal is not itself a constraint"

        p = plan(mode=MODE_FLAT, constraints={"region": ["Corsica"]})
        # A one-element list stays a disjunction: the caller wrote alternatives and
        # named one, which says more were expected. A leaf the plan derived from a
        # single value says `Exact` instead, because no alternative was dropped.
        assert p.leaves[0].constraints == {"region": OneOf(("Corsica",))}


class TestPartitioned:
    def test_one_leaf_per_value_under_one_node(self) -> None:
        p = plan(proposal=PartitionProposal(key="country", values=["FR", "DE", "ES"]))
        assert p.mode == MODE_PARTITIONED
        assert p.leaf_count == 3
        assert len(p.nodes) == 1
        assert [leaf.constraints["country"] for leaf in p.leaves] == [
            Exact("FR"),
            Exact("DE"),
            Exact("ES"),
        ]

    def test_every_leaf_points_at_its_node(self) -> None:
        p = plan(proposal=PartitionProposal(key="country", values=["FR", "DE"]))
        assert {leaf.parent_id for leaf in p.leaves} == {ROOT}
        assert p.nodes[0].id == ROOT

    def test_ids_share_one_namespace_so_a_note_target_resolves(self) -> None:
        p = plan(proposal=PartitionProposal(key="country", values=["FR", "DE"]))
        known = {n.id for n in p.nodes} | {leaf.id for leaf in p.leaves}
        for note in p.notes:
            assert note.target_id is None or note.target_id in known

    def test_a_latent_key_is_marked_as_outside_the_schema(self) -> None:
        p = plan(proposal=PartitionProposal(key="genre", values=["rock", "jazz"]))
        assert p.nodes[0].key_in_schema is False

    def test_a_schema_key_is_marked_as_inside_it(self) -> None:
        p = plan(proposal=PartitionProposal(key="country", values=["FR", "DE"]))
        assert p.nodes[0].key_in_schema is True

    def test_the_node_keeps_every_value_even_when_expansion_stopped(self) -> None:
        limits = PlanLimits(max_leaves=4)
        p = plan(limits=limits, proposal=PartitionProposal(key="c", values=list("abcdefgh")))
        assert p.nodes[0].values == list("abcdefgh")


class TestEveryGuardFires:
    """One case per guard, each asserting the collapse and the note."""

    def test_zero_values(self) -> None:
        p = plan(proposal=PartitionProposal(key="country", values=[]))
        assert p.mode == MODE_FLAT
        assert Gap.GUARD_COLLAPSED in codes(p)

    def test_a_single_value(self) -> None:
        p = plan(proposal=PartitionProposal(key="country", values=["FR"]))
        assert p.mode == MODE_FLAT
        assert "one value" in p.notes[-1].detail

    def test_ambiguity_above_the_threshold(self) -> None:
        p = plan(
            limits=PlanLimits(ambiguity_threshold=0.5),
            proposal=PartitionProposal(key="type", values=["a", "b"], ambiguity=0.9),
        )
        assert p.mode == MODE_FLAT
        assert "ambiguity" in p.notes[-1].detail

    def test_ambiguity_at_the_threshold_is_allowed(self) -> None:
        """The guard is strictly above, so a score exactly at the line still partitions."""
        p = plan(
            limits=PlanLimits(ambiguity_threshold=0.5),
            proposal=PartitionProposal(key="type", values=["a", "b"], ambiguity=0.5),
        )
        assert p.mode == MODE_PARTITIONED

    def test_partition_explosion(self) -> None:
        """More partitions than rows means most leaves can only come back empty."""
        p = plan(
            estimate=Cardinality.TENS,
            proposal=PartitionProposal(key="c", values=[str(i) for i in range(60)]),
        )
        assert p.mode == MODE_FLAT
        assert "can only come back empty" in p.notes[-1].detail

    def test_explosion_does_not_fire_when_no_row_ceiling_is_known(self) -> None:
        p = plan(
            estimate=Cardinality.UNBOUNDED,
            proposal=PartitionProposal(key="c", values=[str(i) for i in range(60)]),
        )
        assert p.mode == MODE_PARTITIONED

    def test_no_proposal_at_all(self) -> None:
        p = plan(proposal=None)
        assert p.mode == MODE_FLAT
        assert Gap.GUARD_COLLAPSED in codes(p)


class TestTheCallersProposal:
    def test_a_rejected_proposal_is_reported_as_rejected(self) -> None:
        p = plan(proposal=PartitionProposal(key="country", values=["FR"], from_caller=True))
        assert Gap.PROPOSAL_REJECTED in codes(p)
        assert Gap.GUARD_COLLAPSED in codes(p)

    def test_the_planners_own_rejected_key_is_not_a_rejected_proposal(self) -> None:
        """PROPOSAL_REJECTED tells a caller their input was not used. Nothing else may."""
        p = plan(proposal=PartitionProposal(key="country", values=["FR"], from_caller=False))
        assert Gap.PROPOSAL_REJECTED not in codes(p)

    def test_an_accepted_proposal_leaves_no_rejection(self) -> None:
        p = plan(proposal=PartitionProposal(key="country", values=["FR", "DE"], from_caller=True))
        assert Gap.PROPOSAL_REJECTED not in codes(p)


class TestMaxLeaves:
    def test_partitioned_groups_rather_than_expanding_and_keeps_the_answer_space(self) -> None:
        """Expansion stops and the values run grouped, so no value is dropped and none is
        covered twice. A remainder leaf with no constraint of its own would do neither: it
        asks the whole question again, overlapping every leaf that was expanded."""
        p = plan(
            limits=PlanLimits(max_leaves=4),
            proposal=PartitionProposal(key="c", values=list("abcdefgh")),
        )
        assert p.leaf_count == 4
        assert p.refused is False
        assert Gap.PLAN_TRUNCATED in codes(p)

        groups = [leaf.constraints["c"] for leaf in p.leaves]
        assert groups == [
            OneOf(("a", "b")),
            OneOf(("c", "d")),
            OneOf(("e", "f")),
            OneOf(("g", "h")),
        ]
        # Read off the alternatives themselves. Splitting a rendered group on its
        # separator is the oracle that agreed with the defect this typing removed: it
        # recovers whatever the renderer joined, including a separator inside a value.
        covered = [value for group in groups if isinstance(group, OneOf) for value in group.values]
        assert covered == list("abcdefgh"), "every value in exactly one leaf"

    def test_a_repeated_value_does_not_buy_itself_a_second_leaf(self) -> None:
        """Analysis is model output and may name the same value twice. Expanded twice it
        spends a leaf and a call asking a question already asked, and returns the same rows
        again under a second identity."""
        p = plan(proposal=PartitionProposal(key="d", values=["North", "North", "South"]))
        assert [leaf.constraints["d"] for leaf in p.leaves] == [Exact("North"), Exact("South")]

    def test_all_values_the_same_collapses_the_way_one_value_does(self) -> None:
        p = plan(proposal=PartitionProposal(key="d", values=["North", "North"]))
        assert p.leaf_count == 1
        assert Gap.GUARD_COLLAPSED in codes(p)

    def test_row_by_row_refuses_rather_than_dropping_named_entities(self) -> None:
        p = plan(
            mode=MODE_ROW_BY_ROW,
            limits=PlanLimits(max_leaves=3),
            constraints={"iata": ["CDG", "ORY", "NCE", "LYS", "MRS"]},
        )
        assert p.refused is True
        assert Gap.PLAN_TRUNCATED in codes(p)
        assert "refused rather than dropping" in p.notes[-1].detail

    def test_a_refused_plan_still_carries_what_it_would_have_run(self) -> None:
        p = plan(
            mode=MODE_ROW_BY_ROW,
            limits=PlanLimits(max_leaves=3),
            constraints={"iata": ["CDG", "ORY", "NCE", "LYS"]},
        )
        assert p.leaf_count == 3
        assert p.nodes[0].values == ["CDG", "ORY", "NCE", "LYS"]

    def test_exactly_at_the_cap_is_not_truncated(self) -> None:
        p = plan(
            limits=PlanLimits(max_leaves=3), proposal=PartitionProposal(key="c", values=list("abc"))
        )
        assert p.leaf_count == 3
        assert Gap.PLAN_TRUNCATED not in codes(p)


class TestRowByRow:
    def test_a_list_constraint_becomes_the_entity_set(self) -> None:
        p = plan(mode=MODE_ROW_BY_ROW, constraints={"iata": ["CDG", "ORY"]})
        assert p.leaf_count == 2
        assert [leaf.constraints["iata"] for leaf in p.leaves] == [Exact("CDG"), Exact("ORY")]
        assert p.nodes[0].values_from == "caller_constraints"

    def test_the_callers_list_wins_over_an_enumeration(self) -> None:
        p = plan(
            mode=MODE_ROW_BY_ROW,
            constraints={"iata": ["CDG"]},
            proposal=PartitionProposal(key="other", values=["x", "y", "z"]),
        )
        assert p.nodes[0].key == "iata"
        assert p.leaf_count == 1

    def test_an_enumeration_is_used_when_no_list_was_given(self) -> None:
        p = plan(
            mode=MODE_ROW_BY_ROW, proposal=PartitionProposal(key="iata", values=["CDG", "ORY"])
        )
        assert p.leaf_count == 2
        assert p.nodes[0].values_from == "enumeration"

    def test_it_charges_a_root_node_so_its_call_stays_inside_the_bound(self) -> None:
        p = plan(
            mode=MODE_ROW_BY_ROW, proposal=PartitionProposal(key="iata", values=["CDG", "ORY"])
        )
        assert len(p.nodes) == 1

    def test_no_entities_at_all_collapses_and_says_so(self) -> None:
        p = plan(mode=MODE_ROW_BY_ROW)
        assert p.mode == MODE_FLAT
        assert Gap.GUARD_COLLAPSED in codes(p)


class TestTheUnknownEstimate:
    def test_an_unknown_estimate_is_recorded(self) -> None:
        p = plan(
            estimate=Cardinality.UNKNOWN, proposal=PartitionProposal(key="c", values=["a", "b"])
        )
        assert Gap.ESTIMATE_UNKNOWN in codes(p)

    def test_a_known_estimate_records_nothing(self) -> None:
        p = plan(
            estimate=Cardinality.HUNDREDS, proposal=PartitionProposal(key="c", values=["a", "b"])
        )
        assert Gap.ESTIMATE_UNKNOWN not in codes(p)

    def test_unknown_is_sized_as_hundreds_for_the_explosion_guard(self) -> None:
        """Not as tens, or an unknown estimate would collapse plans it should not."""
        p = plan(
            estimate=Cardinality.UNKNOWN,
            proposal=PartitionProposal(key="c", values=[str(i) for i in range(60)]),
        )
        assert p.mode == MODE_PARTITIONED


class TestModeIsRefusedByName:
    def test_an_unknown_mode_raises(self) -> None:
        with pytest.raises(ConfigError, match="unknown mode"):
            plan(mode="auto")

    @pytest.mark.parametrize("mode", [MODE_FLAT, MODE_PARTITIONED, MODE_ROW_BY_ROW])
    def test_each_mode_produces_a_plan(self, mode: str) -> None:
        p = plan(mode=mode, proposal=PartitionProposal(key="c", values=["a", "b"]))
        assert p.leaf_count >= 1


class TestEveryModeProducesADistinctPlan:
    def test_the_mode_knob_changes_the_shape(self) -> None:
        proposal = PartitionProposal(key="c", values=["a", "b", "c"])
        flat = plan(mode=MODE_FLAT, proposal=proposal)
        part = plan(mode=MODE_PARTITIONED, proposal=proposal)
        row = plan(mode=MODE_ROW_BY_ROW, constraints={"c": ["a", "b", "c", "d"]})
        assert flat.leaf_count == 1
        assert part.leaf_count == 3
        assert row.leaf_count == 4
