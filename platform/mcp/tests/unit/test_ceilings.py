"""Bounding what a tool call can spend, and sizing it before it runs.

The tools are called the way the server calls them, against the fake client from
``conftest``. That client claims against the run's budget the way the real one does, so a
ceiling here is enforced by the library's ledger rather than by anything this file arranges.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from blue_data_llm_source import Capability

from blue_data_llm_mcp import tools

QUERY = "the member states of the European Union"

# What the double charges per call. A cost ceiling under it must refuse the first attempt,
# because the whole bound is claimed before the provider is contacted.
PER_CALL = 0.0012


@pytest.mark.parametrize(
    ("ceiling", "axis"),
    [
        ({"limits.max_cost": PER_CALL / 2}, "max_cost"),
        ({"limits.max_seconds": 0}, "max_seconds"),
        ({"limits.max_calls": 0}, "max_calls"),
    ],
    ids=["cost", "seconds", "calls"],
)
def test_each_ceiling_stops_the_fetch_before_the_model_is_called(
    make_source: Any, ceiling: dict[str, Any], axis: str
) -> None:
    """A ceiling in the tool arguments has to bind, or it is worse than no ceiling.

    An accepted argument that reaches nothing is the failure this package exists to
    prevent: the caller believes the run is bounded and it is not. So the assertion is not
    that the argument was accepted but that the provider was never reached and the result
    names the axis that refused it.

    Both dispatch paths, because the server reaches the async one and every direct test
    reaches the other.
    """
    for dispatch in ("sync", "async"):
        source, client = make_source()
        arguments = {"query": QUERY, **ceiling}
        if dispatch == "sync":
            payload = tools.dispatch("llm_fetch", arguments, source=source)
        else:
            payload = asyncio.run(tools.adispatch("llm_fetch", arguments, source=source))

        assert payload["records"] == []
        assert payload["meta"]["outcome"] == "stopped_by_limit"
        assert payload["meta"]["stopped_by"] == axis
        assert client.prompts == [], f"the model was called under a {axis} ceiling ({dispatch})"


def test_a_ceiling_that_leaves_room_does_not_get_in_the_way(make_source: Any) -> None:
    """The other half of the same claim: these arguments bound a run, they do not break it."""
    source, client = make_source()
    payload = tools.dispatch(
        "llm_fetch",
        {"query": QUERY, "limits.max_cost": 1.0, "limits.max_seconds": 60, "limits.max_calls": 5},
        source=source,
    )

    assert payload["meta"]["outcome"] == "ok"
    assert payload["records"][0]["country"] == "France"
    assert len(client.prompts) == 1


def test_the_ceilings_reach_the_library_under_the_names_it_reads(make_source: Any) -> None:
    """Passed as per-call properties, which is the same route the command line uses.

    Read off the properties the client was handed rather than off the result, because a
    ceiling that arrives misspelled is refused by the library and one that arrives under a
    key nobody reads is not — and the second is the one worth pinning.
    """
    source, client = make_source()
    tools.dispatch(
        "llm_fetch",
        {"query": QUERY, "limits.max_cost": 1.0, "limits.max_seconds": 60, "limits.max_calls": 5},
        source=source,
    )

    handed = client.properties[0]
    assert handed["limits.max_cost"] == 1.0
    assert handed["limits.max_seconds"] == 60
    assert handed["limits.max_calls"] == 5


def test_one_calls_ceiling_does_not_bind_the_next_call(make_source: Any) -> None:
    """One server serves many callers, and a ceiling belongs to the call it arrived with.

    The library merges per-call properties over the source's own and does not write them
    back. If it did, the first agent to set a ceiling would set it for everyone afterwards.
    """
    source, client = make_source()
    stopped = tools.dispatch("llm_fetch", {"query": QUERY, "limits.max_calls": 0}, source=source)
    assert stopped["meta"]["stopped_by"] == "max_calls"

    unbounded = tools.dispatch("llm_fetch", {"query": QUERY}, source=source)
    assert unbounded["meta"]["outcome"] == "ok"
    assert len(client.prompts) == 1


def test_a_refused_ceiling_is_a_tool_error_naming_it(make_source: Any) -> None:
    """The library validates the value; this layer passes the refusal through unreworded."""
    source, client = make_source()
    arguments = {"query": QUERY, "limits.max_cost": float("inf")}
    with pytest.raises(tools.ToolError) as caught:
        tools.dispatch("llm_fetch", arguments, source=source)

    assert caught.value.kind == "ConfigError"
    assert "limits.max_cost" in caught.value.message
    assert client.prompts == []


def test_llm_estimate_returns_a_bound_and_contacts_nothing(make_source: Any) -> None:
    """The point of the tool: a figure to size a request against, for free.

    An estimate that reached the provider would be a request, and an agent asking what
    something costs before deciding whether to pay for it would have paid for the question.
    """
    source, client = make_source()
    payload = tools.dispatch("llm_estimate", {"query": QUERY, "attrs": ["country"]}, source=source)

    assert payload["bounded"] is True
    assert payload["max_cost_per_attempt"] == PER_CALL
    # Three attempts: the shipped retry allowance is two, and a fetch may use all of them.
    assert payload["max_attempts"] == 3
    assert payload["max_cost"] == pytest.approx(PER_CALL * 3)
    assert payload["reason"] is None
    assert payload["model"]
    assert payload["prompt_bytes"] > 0
    assert payload["max_output_tokens"] == 4096

    assert client.prompts == [], "the estimate called the model"
    assert client.usages == [], "the estimate spent something"


def test_the_estimate_a_fetch_is_sized_against_is_the_one_a_ceiling_would_hold(
    make_source: Any,
) -> None:
    """A ceiling in the estimate call narrows the estimate, because it narrows the fetch.

    `limits.max_calls` caps the attempts a fetch may make, so it caps what a fetch can cost.
    An estimate that ignored it would tell a caller to reserve for three attempts a call
    ceiling would never allow.
    """
    source, _ = make_source()
    payload = tools.dispatch("llm_estimate", {"query": QUERY, "limits.max_calls": 1}, source=source)

    assert payload["max_attempts"] == 1
    assert payload["max_cost"] == pytest.approx(PER_CALL)


def test_the_bound_is_published_unrounded(make_source: Any) -> None:
    """Rounding a bound can round it down, and a figure a run can exceed is not a bound.

    The spend in a fetch's meta is rounded to six places, because that one is a measurement
    of what was already paid. These two are not: an agent that read a rounded-down bound and
    set `limits.max_cost` to it would have the last attempt the figure accounted for
    refused.
    """
    source, _ = make_source(cost=1.5e-7)
    payload = tools.dispatch("llm_estimate", {"query": QUERY}, source=source)

    assert payload["max_cost_per_attempt"] == 1.5e-7
    assert payload["max_cost"] > 0.0


def test_an_unpriceable_client_reports_why_rather_than_guessing(make_source: Any) -> None:
    """`bounded` false with a reason is a normal answer here, not a failed tool call.

    Asking what a request might cost is reasonable even where the answer cannot be given,
    and a guess published as a bound is the one answer that would be worse than none.
    """
    source, client = make_source(
        capabilities=frozenset({Capability.CALLS, Capability.ROWS, Capability.COST})
    )
    payload = tools.dispatch("llm_estimate", {"query": QUERY}, source=source)

    assert payload["bounded"] is False
    assert payload["max_cost"] is None
    assert "does not report what an attempt will cost" in payload["reason"]
    assert client.prompts == []


def test_an_empty_request_estimates_at_zero(source: Any) -> None:
    """It never reaches a model, so it cannot cost anything."""
    payload = tools.dispatch("llm_estimate", {}, source=source)

    assert payload["bounded"] is True
    assert payload["max_cost"] == 0.0


def test_llm_estimate_is_reachable_on_the_async_path(make_source: Any) -> None:
    """The server's handler is async, so a tool only in the sync table is unreachable there."""
    source, client = make_source()
    payload = asyncio.run(tools.adispatch("llm_estimate", {"query": QUERY}, source=source))

    assert payload["bounded"] is True
    assert client.prompts == []


def test_the_estimate_result_is_json_serialisable(source: Any) -> None:
    payload = tools.dispatch("llm_estimate", {"query": QUERY}, source=source)
    assert json.loads(json.dumps(payload)) == payload


def test_a_library_refusal_carries_its_own_kind_and_message(source: Any) -> None:
    """The handler, not the dispatcher: this is the pass-through the tool result depends on.

    Reached through `dispatch` the argument check refuses this first, with its own message.
    Called the way the dispatcher calls it, the library's `QueryError` has to arrive intact,
    because the agent reading the failed tool call sees that text and nothing else.
    """
    with pytest.raises(tools.ToolError) as caught:
        tools.llm_estimate({"query": QUERY, "attrs": [{"type": "int"}]}, source=source)
    assert caught.value.kind == "QueryError"
    assert "non-empty string 'name'" in caught.value.message


def test_estimate_refuses_a_row_cap_rather_than_ignoring_one(source: Any) -> None:
    """`estimate` takes no row cap, so accepting one would be accepting a no-op.

    A caller reusing its fetch arguments learns that from the refusal instead of receiving a
    figure that silently ignored part of what it asked.
    """
    with pytest.raises(tools.ToolError, match="unknown argument"):
        tools.dispatch("llm_estimate", {"query": QUERY, "max_records": 5}, source=source)
