"""A leaf task seeded with the keys an earlier call of the same request returned.

Under `plan.split_policy="evidence"`, children split after a root probe are told what the
probe holds. The seed travels on wire version 6 for the default contract and on wire
version 5 for `keyed-leaf-fetch-2`; a task without one keeps its 0.2.1.0 wire version.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from blue_data_llm_source import prompts
from blue_data_llm_source.execution.local import run_local_leaf
from blue_data_llm_source.execution.mapper import run_leaf
from blue_data_llm_source.planning.bounds import PlanLimits
from blue_data_llm_source.planning.plan import Leaf
from blue_data_llm_source.planning.schema import KEY_FROM_CALLER, Schema
from blue_data_llm_source.planning.split_gate import held_from_records
from blue_data_llm_source.planning.transport import (
    COMPLETE_WIRE_VERSION,
    HELD_WIRE_VERSION,
    REASONING_WIRE_VERSION,
    WIRE_VERSION,
    LeafTask,
)
from blue_data_llm_source.types import Attr, ConfigError, Exact, Usage

SCHEMA = Schema(attrs=[Attr("name", "string")], key="name", key_from=KEY_FROM_CALLER)
LEAF = Leaf(
    id="0.0",
    parent_id="0",
    query="stations",
    original_query="stations",
    constraints={"region": Exact("North")},
    depth=1,
)
HELD = ({"name": "a"}, {"name": "b"})


def _task(contract: str = prompts.KEYED_LEAF_FETCH_PROMPT_VERSION, **kwargs: Any) -> LeafTask:
    return LeafTask.from_leaf(
        LEAF,
        caller_context="",
        schema=SCHEMA,
        settings=kwargs.pop("settings", {}),
        prompt_version=contract,
        **kwargs,
    )


def test_a_seeded_default_task_travels_on_wire_version_six() -> None:
    task = _task(held_exclusions=HELD, held_total=5)
    payload = json.loads(json.dumps(task.to_dict()))
    assert payload["wire_version"] == HELD_WIRE_VERSION
    assert payload["prompt_version"] == prompts.KEYED_LEAF_FETCH_PROMPT_VERSION
    assert payload["held_exclusions"] == [{"name": "a"}, {"name": "b"}]
    assert payload["held_total"] == 5
    assert LeafTask.from_dict(payload) == task


def test_a_seeded_task_carries_reasoning_on_the_same_version() -> None:
    task = _task(held_exclusions=HELD, held_total=2, settings={"reasoning_effort": "low"})
    assert task.wire_version == HELD_WIRE_VERSION
    assert LeafTask.from_dict(task.to_dict()) == task


def test_an_unseeded_task_keeps_its_version_and_payload() -> None:
    task = _task()
    assert task.wire_version == WIRE_VERSION
    assert "held_exclusions" not in task.to_dict()
    reasoning = _task(settings={"reasoning_effort": "low"})
    assert reasoning.wire_version == REASONING_WIRE_VERSION


def test_the_completeness_contract_carries_the_seed_on_wire_version_five() -> None:
    task = _task(prompts.COMPLETE_LEAF_FETCH_PROMPT_VERSION, held_exclusions=HELD, held_total=2)
    payload = task.to_dict()
    assert payload["wire_version"] == COMPLETE_WIRE_VERSION
    assert payload["held_total"] == 2
    assert LeafTask.from_dict(payload) == task
    empty = _task(prompts.COMPLETE_LEAF_FETCH_PROMPT_VERSION).to_dict()
    assert empty["held_exclusions"] == [] and empty["held_total"] == 0


@pytest.mark.parametrize(
    ("held", "total"),
    [(HELD, 1), (({"name": float("nan")},), 1), ((), -1), ((), True)],
)
def test_a_malformed_seed_is_refused(held: tuple[Any, ...], total: Any) -> None:
    with pytest.raises(ConfigError):
        _task(held_exclusions=held, held_total=total)


def test_an_empty_seed_on_wire_version_six_is_refused() -> None:
    payload = _task(held_exclusions=HELD, held_total=2).to_dict()
    payload["held_exclusions"] = []
    payload["held_total"] = 0
    with pytest.raises(ConfigError, match="non-empty held seed"):
        LeafTask.from_dict(payload)


def test_an_older_receiver_grammar_refuses_the_seed_fields() -> None:
    payload = _task(held_exclusions=HELD, held_total=2).to_dict()
    payload["wire_version"] = WIRE_VERSION
    with pytest.raises(ConfigError, match="fields do not match"):
        LeafTask.from_dict(payload)


# ------------------------------------------------------------------ the leaf loop ---


def test_the_first_ask_names_the_seed_and_its_full_count() -> None:
    sent: list[str] = []

    def ask(prompt: str) -> str:
        sent.append(prompt)
        return json.dumps([{"name": "c"}, {"name": "a"}])

    run = run_leaf(
        LEAF,
        ask,
        attrs=list(SCHEMA.attrs),
        limits=PlanLimits(),
        primary_keys=("name",),
        prompt_version=prompts.KEYED_LEAF_FETCH_PROMPT_VERSION,
        held=list(HELD),
        held_total=7,
    )
    assert "Already returned: 7 records, the 2 most recent being:" in sent[0]
    # A seeded key the leaf returns anyway is kept for reduction to merge.
    assert run.records == [{"name": "c"}, {"name": "a"}]


def test_an_unseeded_loop_sends_what_it_always_sent() -> None:
    sent: list[str] = []
    run_leaf(
        LEAF,
        lambda prompt: sent.append(prompt) or "[]",
        attrs=list(SCHEMA.attrs),
        limits=PlanLimits(),
        primary_keys=("name",),
        prompt_version=prompts.KEYED_LEAF_FETCH_PROMPT_VERSION,
    )
    assert "Already returned" not in sent[0]


def test_a_count_below_the_seed_is_refused() -> None:
    with pytest.raises(ConfigError, match="held_total"):
        run_leaf(
            LEAF,
            lambda _prompt: "[]",
            attrs=list(SCHEMA.attrs),
            limits=PlanLimits(),
            held=list(HELD),
            held_total=1,
        )


class _Recording:
    def __init__(self) -> None:
        self.prompts: list[str] = []

    async def acomplete(self, prompt: str, properties: dict[str, Any]) -> str:
        self.prompts.append(prompt)
        sink = properties.get("usage_sink")
        if isinstance(sink, list):
            sink.append(Usage(model="recorded"))  # pyright: ignore[reportUnknownMemberType]
        return "[]"


async def test_the_seed_crosses_the_local_executor_as_plain_json() -> None:
    client = _Recording()
    await run_local_leaf(
        _task(held_exclusions=HELD, held_total=2), client=client, properties={}, usage_sink=[]
    )
    assert '- {"name": "a"}' in client.prompts[0]
    assert "mappingproxy" not in client.prompts[0]


# ------------------------------------------------------------- building the seed ---


def test_the_seed_is_one_key_object_per_distinct_key_bounded_from_the_end() -> None:
    records = [{"name": "a", "v": 1}, {"name": "b"}, {"name": "A"}, {"name": "c", "v": 3}]
    held, total = held_from_records(
        records, ("name",), SCHEMA.attrs, max_excluded_keys=2, max_exclusion_bytes=16_384
    )
    assert total == 3
    assert held == ({"name": "b"}, {"name": "c"})


def test_a_record_without_a_complete_key_is_never_listed() -> None:
    """Only key values cross into another prompt, never a record's other fields."""
    records = [{"name": None, "secret": "x"}, {"name": "a", "secret": "y"}, {"secret": "z"}]
    held, total = held_from_records(
        records, ("name",), SCHEMA.attrs, max_excluded_keys=50, max_exclusion_bytes=16_384
    )
    assert held == ({"name": "a"},) and total == 1
    keyless, none = held_from_records(
        records, (), SCHEMA.attrs, max_excluded_keys=50, max_exclusion_bytes=16_384
    )
    assert keyless == () and none == 0
