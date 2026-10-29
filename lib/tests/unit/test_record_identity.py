"""One rule for what makes two records the same record, checked at both stages that use it.

Execution decides whether a response holds a record the leaf already has. Reduction decides
whether two leaves returned the same record. Each is free to answer that on its own, each
answer is defensible in isolation — a string coercion at one, a raw dict key at the other —
and the two disagree on the values below. A pair that is one record at the first stage and
two at the second loses a row that nothing reports: the leaf returns ``ok`` with no cap and
no error, and the row it dropped is still counted by everything downstream.

So the property is agreement, and it is asserted against the two units rather than against
the shared function, which they could both import and still apply differently.
"""

from __future__ import annotations

import json
from typing import Any

from blue_data_llm_source.execution.mapper import run_leaf
from blue_data_llm_source.planning.bounds import PlanLimits
from blue_data_llm_source.planning.plan import ROOT, FetchPlan, Leaf
from blue_data_llm_source.planning.schema import KEY_FROM_CALLER, Schema
from blue_data_llm_source.reduction.coverage import LeafOutcome, reduce_leaves
from blue_data_llm_source.types import Attr, value_identity

KEY = "id"
ATTRS = [Attr(name=KEY), Attr(name="n")]

VALUES: list[Any] = [
    1,
    "1",
    True,
    0,
    "0",
    False,
    1.0,
    "two",
    None,
    [1],
    [1],
    {"a": 1},
]
"""The values the two rules split on, plus enough neighbors to pin what stays merged.

``1`` against ``"1"`` is where a string coercion loses a row. ``True`` against ``1`` and
``False`` against ``0`` are where a raw dict key loses one, since Python hashes a bool as
the int it subclasses. ``1.0`` against ``1`` is the pair that must *not* split: JSON has
one number type, and a model writing a key both ways has written one key.
"""

SURVIVORS = [0, 1, 2, 3, 4, 5, 7, 8, 9, 11]
"""Which positions in ``VALUES`` are the first of their identity, and so are kept.

Position 6 is ``1.0``, held by ``1`` at position 0. Position 10 is the second ``[1]``.
"""


def _records() -> list[dict[str, Any]]:
    """One record per value, each tagged with the position it came from."""
    return [{KEY: value, "n": position} for position, value in enumerate(VALUES)]


def _kept_by_execution() -> list[int]:
    """The positions the continuation loop treats as records it does not already hold."""
    answers = [json.dumps(_records()), "[]"]
    run = run_leaf(
        Leaf(id="0.0", parent_id=ROOT, query="airports", constraints={}),
        lambda _prompt: answers.pop(0),
        attrs=ATTRS,
        limits=PlanLimits(),
        key=KEY,
    )
    return [int(record["n"]) for record in run.records]


def _kept_by_reduction() -> list[int]:
    """The positions that survive merging one leaf's records against an identified key."""
    plan = FetchPlan(
        mode="flat",
        leaves=[Leaf(id="0.0", parent_id=ROOT, query="airports", constraints={})],
    )
    schema = Schema(attrs=ATTRS, key=KEY, key_from=KEY_FROM_CALLER)
    records, _ = reduce_leaves(plan, schema, [("0.0", _records(), LeafOutcome("0.0"))])
    return [int(record["n"]) for record in records]


def test_execution_and_reduction_keep_the_same_records() -> None:
    """The property that was missing. Neither stage is wrong by its own rule, so only a
    comparison across the two can fail when they part."""
    assert _kept_by_execution() == _kept_by_reduction()


def test_both_stages_keep_the_records_the_shared_rule_says_to_keep() -> None:
    """Agreement alone would also be satisfied by two units agreeing on a wrong rule."""
    assert _kept_by_execution() == SURVIVORS
    assert _kept_by_reduction() == SURVIVORS


def test_a_value_and_its_string_are_two_records() -> None:
    assert value_identity(1) != value_identity("1")


def test_a_bool_and_the_int_it_subclasses_are_two_records() -> None:
    """``hash(True) == hash(1)`` and ``True == 1``, so a dict files them together."""
    assert value_identity(True) != value_identity(1)
    assert value_identity(False) != value_identity(0)


def test_one_number_written_two_ways_is_one_record() -> None:
    """JSON has a single number type. Splitting these would trade the lost row for a
    duplicate one."""
    assert value_identity(1) == value_identity(1.0)


def test_an_unhashable_value_gets_an_identity_rather_than_an_error() -> None:
    identity = value_identity([1, {"a": 2}])
    assert hash(identity) == hash(value_identity([1, {"a": 2}]))


def test_an_object_written_in_two_key_orders_is_one_value() -> None:
    """JSON object order is not semantic, so the order a model wrote it in is not identity."""
    assert value_identity({"a": 1, "b": 2}) == value_identity({"b": 2, "a": 1})


def test_a_container_is_not_confused_with_its_own_text() -> None:
    assert value_identity([1]) != value_identity("[1]")
