"""fetch and afetch: validation, reduction, metadata, and the row budget."""

from __future__ import annotations

import json
from typing import Any

import pytest

from blue_data_llm_source import LlmSource
from blue_data_llm_source.llm.budget import Budget, Limits
from blue_data_llm_source.source import decide_outcome
from blue_data_llm_source.types import ConfigError, Outcome, ParseError, QueryError
from tests.doubles import ScriptedClient

ONE_RECORD = '[{"name": "Ada", "age": 36}]'
THREE_RECORDS = '[{"name": "Ada"}, {"name": "Grace"}, {"name": "Katherine"}]'


def test_a_fetch_returns_the_records_the_model_produced() -> None:
    source = LlmSource(client=ScriptedClient([ONE_RECORD]))
    result = source.fetch("who?")
    assert result.records == [{"name": "Ada", "age": 36}]


def test_a_fetch_result_is_countable_and_iterable() -> None:
    source = LlmSource(client=ScriptedClient([THREE_RECORDS]))
    result = source.fetch("who?")
    assert len(result) == 3
    assert [record["name"] for record in result] == ["Ada", "Grace", "Katherine"]


async def test_afetch_returns_the_same_records_as_fetch() -> None:
    source = LlmSource(client=ScriptedClient([ONE_RECORD]))
    result = await source.afetch("who?")
    assert result.records == [{"name": "Ada", "age": 36}]


# ------------------------------------------------------------------ validation --
@pytest.mark.parametrize("query", ["", "   ", "\n\t"])
def test_an_empty_query_is_an_empty_result_not_an_exception(query: str) -> None:
    """An empty ask is an empty answer, the way the other sources in this layer treat it.

    The reason is not lost with the exception: meta.outcome says the query was empty, so
    a caller can tell "you asked nothing" from "the model knows nothing".
    """
    client = ScriptedClient([ONE_RECORD])
    source = LlmSource(client=client)

    result = source.fetch(query)

    assert result.records == []
    assert result.is_empty
    assert result.meta.outcome == Outcome.EMPTY_QUERY
    assert client.prompts == [], "an empty query must not reach the model"


async def test_an_empty_query_to_afetch_also_skips_the_model() -> None:
    client = ScriptedClient([ONE_RECORD])
    source = LlmSource(client=client)

    result = await source.afetch("  ")

    assert result.records == []
    assert result.meta.outcome == Outcome.EMPTY_QUERY
    assert client.prompts == []


def test_a_non_string_query_still_raises_because_that_is_a_caller_mistake() -> None:
    client = ScriptedClient([ONE_RECORD])
    source = LlmSource(client=client)

    with pytest.raises(QueryError, match="must be a string"):
        source.fetch(123)

    assert client.prompts == []


def test_the_outcome_separates_the_ways_a_result_can_be_empty() -> None:
    """Three empties that a planner must be able to tell apart."""
    empty_query = LlmSource(client=ScriptedClient([ONE_RECORD])).fetch("")
    model_none = LlmSource(client=ScriptedClient(["[]"])).fetch("who?")
    stopped = LlmSource(
        client=ScriptedClient([ONE_RECORD]), properties={"limits.max_calls": 0}
    ).fetch("who?")

    assert empty_query.meta.outcome == Outcome.EMPTY_QUERY
    assert model_none.meta.outcome == Outcome.MODEL_RETURNED_NONE
    assert stopped.meta.outcome == Outcome.STOPPED_BY_LIMIT
    assert stopped.meta.stopped_by == "max_calls"
    assert len({empty_query.meta.outcome, model_none.meta.outcome, stopped.meta.outcome}) == 3


def test_a_successful_fetch_reports_ok() -> None:
    result = LlmSource(client=ScriptedClient([ONE_RECORD])).fetch("who?")
    assert result.meta.outcome == Outcome.OK


def test_a_bad_attribute_list_raises_before_the_model_is_called() -> None:
    client = ScriptedClient([ONE_RECORD])
    source = LlmSource(client=client)

    with pytest.raises(QueryError, match=r"attrs\[0\]"):
        source.fetch("who?", attrs=[{"type": "int"}])

    assert client.prompts == []


def test_a_non_positive_max_records_raises_before_the_model_is_called() -> None:
    client = ScriptedClient([ONE_RECORD])
    source = LlmSource(client=client)

    with pytest.raises(QueryError, match="max_records"):
        source.fetch("who?", max_records=0)

    assert client.prompts == []


def test_an_unparseable_response_raises_parse_error_with_the_raw_text() -> None:
    source = LlmSource(client=ScriptedClient(["I am afraid I cannot answer that."]))

    with pytest.raises(ParseError) as caught:
        source.fetch("who?")

    assert caught.value.raw == "I am afraid I cannot answer that."


# ------------------------------------------------------------------ attributes --
def test_a_list_of_names_and_a_list_of_dicts_align_identically() -> None:
    answer = '[{"first_name": "Ada", "Age": 36}]'
    by_name = LlmSource(client=ScriptedClient([answer])).fetch("who?", attrs=["first name", "age"])
    by_dict = LlmSource(client=ScriptedClient([answer])).fetch(
        "who?", attrs=[{"name": "first name"}, {"name": "age"}]
    )

    assert by_name.records == by_dict.records == [{"first name": "Ada", "age": 36}]
    assert by_name.meta.requested_attrs == by_dict.meta.requested_attrs
    assert by_name.meta.remapped_keys == by_dict.meta.remapped_keys


def test_a_declared_type_is_applied_to_the_returned_value() -> None:
    source = LlmSource(client=ScriptedClient(['[{"age": "36"}]']))
    result = source.fetch("who?", attrs=[{"name": "age", "type": "int"}])
    assert result.records == [{"age": 36}]


def test_a_declared_type_that_does_not_fit_keeps_the_value() -> None:
    source = LlmSource(client=ScriptedClient(['[{"age": "about forty"}]']))
    result = source.fetch("who?", attrs=[{"name": "age", "type": "int"}])
    assert result.records == [{"age": "about forty"}]


def test_an_attribute_the_model_omitted_comes_back_as_none() -> None:
    source = LlmSource(client=ScriptedClient(['[{"name": "Ada"}]']))
    result = source.fetch("who?", attrs=["name", "party"])
    assert result.records == [{"name": "Ada", "party": None}]


def test_an_unmappable_key_is_reported_in_the_metadata() -> None:
    source = LlmSource(client=ScriptedClient(['[{"name": "Ada", "nickname": "AL"}]']))
    result = source.fetch("who?", attrs=["name"])

    assert result.meta.unmapped_keys == ["nickname"]
    assert result.meta.observed_keys == ["name", "nickname"]
    assert result.records == [{"name": "Ada"}]


def test_a_remapped_key_is_reported_in_the_metadata() -> None:
    source = LlmSource(client=ScriptedClient(['[{"first_name": "Ada"}]']))
    result = source.fetch("who?", attrs=["first name"])
    assert result.meta.remapped_keys == {"first_name": "first name"}


def test_duplicate_records_collapse_before_they_are_returned() -> None:
    answer = '[{"name": "Ada"}, {"name": "Ada"}, {"name": "Grace"}]'
    source = LlmSource(client=ScriptedClient([answer]))
    assert source.fetch("who?").records == [{"name": "Ada"}, {"name": "Grace"}]


# --------------------------------------------------------------- prompt safety --
def test_template_markers_in_a_query_reach_the_model_verbatim() -> None:
    query = "Explain ${input}, {{jinja}} and {braces} in one sentence"
    client = ScriptedClient([ONE_RECORD])

    LlmSource(client=client).fetch(query)

    prompt = client.prompts[0]
    assert query in prompt
    assert "${input}" in prompt
    assert "{{jinja}}" in prompt
    assert "{braces}" in prompt


def test_template_markers_in_a_context_reach_the_model_verbatim() -> None:
    client = ScriptedClient([ONE_RECORD])
    LlmSource(client=client).fetch("who?", context="answer as ${name} or {{name}}")
    assert "${name}" in client.prompts[0]
    assert "{{name}}" in client.prompts[0]


def test_a_context_given_as_a_list_is_joined_into_the_prompt() -> None:
    client = ScriptedClient([ONE_RECORD])
    LlmSource(client=client).fetch("who?", context=["first note", "second note"])
    assert "first note\nsecond note" in client.prompts[0]


# -------------------------------------------------------------------- metadata --
def test_metadata_folds_the_usage_the_client_reported() -> None:
    client = ScriptedClient(
        [ONE_RECORD], model="scripted-4o", prompt_tokens=11, completion_tokens=7, cost=0.5
    )
    result = LlmSource(client=client).fetch("who?")

    assert result.meta.calls == 1
    assert result.meta.model == "scripted-4o"
    assert result.meta.prompt_tokens == 11
    assert result.meta.completion_tokens == 7
    assert result.meta.cost == pytest.approx(0.5)
    assert result.meta.cache_hits == 0


def test_latency_is_recorded_on_the_result() -> None:
    result = LlmSource(client=ScriptedClient([ONE_RECORD])).fetch("who?")
    assert result.meta.latency_seconds >= 0.0


def test_nothing_stopped_a_run_that_completed() -> None:
    result = LlmSource(client=ScriptedClient([ONE_RECORD])).fetch("who?")
    assert result.meta.stopped_by is None
    assert result.meta.truncated_to is None


# ------------------------------------------------------------------ row budget --
def test_max_records_truncates_the_returned_records() -> None:
    source = LlmSource(client=ScriptedClient([THREE_RECORDS]))
    result = source.fetch("who?", max_records=2)

    assert [record["name"] for record in result.records] == ["Ada", "Grace"]
    assert result.meta.truncated_to == 2
    assert result.meta.stopped_by == "max_rows"


def test_a_row_limit_from_properties_truncates_the_returned_records() -> None:
    source = LlmSource(properties={"limits.max_rows": 1}, client=ScriptedClient([THREE_RECORDS]))
    result = source.fetch("who?")

    assert len(result) == 1
    assert result.meta.truncated_to == 1
    assert result.meta.stopped_by == "max_rows"


def test_max_records_above_the_row_count_changes_nothing() -> None:
    source = LlmSource(client=ScriptedClient([THREE_RECORDS]))
    result = source.fetch("who?", max_records=10)

    assert len(result) == 3
    assert result.meta.truncated_to is None
    assert result.meta.stopped_by is None


def test_max_records_is_clamped_by_a_configured_row_limit() -> None:
    source = LlmSource(properties={"limits.max_rows": 1}, client=ScriptedClient([THREE_RECORDS]))
    result = source.fetch("who?", max_records=3)
    assert len(result) == 1
    assert result.meta.truncated_to == 1


def test_a_partial_truncation_reports_truncated() -> None:
    source = LlmSource(client=ScriptedClient([THREE_RECORDS]))
    result = source.fetch("who?", max_records=2)

    assert result.meta.outcome == Outcome.TRUNCATED


def test_a_row_cap_that_removes_every_record_reports_the_limit_not_a_filter() -> None:
    # The model answered and nothing was filtered; the ceiling emptied the result. Reporting
    # filtered_to_empty here would send a caller looking for a fault in its own request.
    source = LlmSource(properties={"limits.max_rows": 0}, client=ScriptedClient([THREE_RECORDS]))
    result = source.fetch("who?")

    assert len(result) == 0
    assert result.meta.outcome == Outcome.STOPPED_BY_LIMIT
    assert result.meta.stopped_by == "max_rows"
    assert result.meta.truncated_to == 0


# ------------------------------------------------------------- loop discipline --
# Treat leaked coroutines as errors on the sync bridge's success and failure paths.
LEAKED_COROUTINE = "error:coroutine .* was never awaited:RuntimeWarning"


@pytest.mark.filterwarnings(LEAKED_COROUTINE)
async def test_fetch_inside_a_running_loop_returns_records() -> None:
    source = LlmSource(client=ScriptedClient([ONE_RECORD]))
    assert source.fetch("who?").records == json.loads(ONE_RECORD)


@pytest.mark.filterwarnings(LEAKED_COROUTINE)
async def test_invalid_settings_inside_a_running_loop_do_not_call_the_model() -> None:
    client = ScriptedClient([ONE_RECORD])
    source = LlmSource(client=client)

    with pytest.raises(ConfigError, match="limits.max_calls"):
        source.fetch("who?", properties={"limits.max_calls": True})

    assert client.prompts == []


# ------------------------------------------------------- empty after reduction --
def test_a_record_answering_none_of_the_requested_attributes_is_dropped() -> None:
    """A record of nothing but None says the model answered when it did not."""
    source = LlmSource(client=ScriptedClient(['[{"unrelated": 1}]']))
    result = source.fetch("who?", attrs=["wanted"])

    assert result.records == []
    assert result.meta.outcome == Outcome.FILTERED_TO_EMPTY
    assert result.meta.unmapped_keys == ["unrelated"]


def test_a_record_answering_one_of_several_attributes_is_kept() -> None:
    source = LlmSource(client=ScriptedClient(['[{"name": "Ada"}]']))
    result = source.fetch("who?", attrs=["name", "party"])

    assert result.records == [{"name": "Ada", "party": None}]
    assert result.meta.outcome == Outcome.OK


def test_records_pass_through_when_no_attributes_were_requested() -> None:
    """With nothing requested there is nothing to be missing."""
    source = LlmSource(client=ScriptedClient(['[{"anything": 1}]']))
    result = source.fetch("who?")

    assert result.records == [{"anything": 1}]
    assert result.meta.outcome == Outcome.OK


def _a_partly_answered_run() -> str:
    """A split request where one part raises and the others answer.

    Through `llm_fn` rather than a scripted client, because which part fails has to depend
    on which part was asked. A double answering in call order cannot express that.
    """
    analysis = json.dumps(
        {
            "ask": "who?",
            "constraints": {"region": ["North", "South"]},
            "row_estimate": 200,
            "schema": {"attrs": [{"name": "name"}], "key": "name"},
        }
    )
    proposal = json.dumps(
        {
            "key": "region",
            "values": ["North", "South"],
            "ambiguity": 0,
            "child_estimates": [10, 10],
        }
    )

    def model(prompt: str, properties: Any = None) -> str:
        if "Prepare a structured data request" in prompt:
            return analysis
        if "Propose one useful partition" in prompt:
            return proposal
        if "North" in prompt:
            raise RuntimeError("provider refused this part")
        return '[{"name": "Ada"}]'

    result = LlmSource().fetch(
        "who?",
        attrs=["name"],
        llm_fn=model,
        properties={"plan.mode": "partitioned", "plan.key": "name"},
    )
    return str(result.meta.outcome)


def test_every_outcome_value_is_reachable_from_the_fetch_path() -> None:
    """Every advertised outcome must be reachable through fetch."""
    produced = {
        _a_partly_answered_run(),
        LlmSource(client=ScriptedClient(['[{"name": "Ada"}]'])).fetch("who?").meta.outcome,
        LlmSource(client=ScriptedClient(['[{"name": "Ada"}]'])).fetch("").meta.outcome,
        LlmSource(client=ScriptedClient(["[]"])).fetch("who?").meta.outcome,
        LlmSource(client=ScriptedClient(['[{"unrelated": 1}]']))
        .fetch("who?", attrs=["wanted"])
        .meta.outcome,
        LlmSource(client=ScriptedClient(['[{"name": "Ada"}]']), properties={"limits.max_calls": 0})
        .fetch("who?")
        .meta.outcome,
        LlmSource(client=ScriptedClient([THREE_RECORDS])).fetch("who?", max_records=2).meta.outcome,
    }

    assert produced == set(Outcome.ALL)


def test_an_explicit_null_answer_survives_the_whole_fetch_path() -> None:
    """End to end, not only in the reduction unit: a null is an answer."""
    source = LlmSource(client=ScriptedClient(['[{"party": null}]']))
    result = source.fetch("who?", attrs=["party"])

    assert result.records == [{"party": None}]
    assert result.meta.outcome == Outcome.OK


def test_a_declared_type_does_not_turn_a_null_answer_into_an_absence() -> None:
    source = LlmSource(client=ScriptedClient(['[{"age": null}]']))
    result = source.fetch("who?", attrs=[{"name": "age", "type": "int"}])

    assert result.records == [{"age": None}]
    assert result.meta.outcome == Outcome.OK


# ------------------------------------------------------------ the outcome matrix --
@pytest.mark.parametrize(
    ("records", "model_returned", "row_cap_fired", "stopped_by", "expected"),
    [
        ([{"a": 1}], [{"a": 1}], False, None, Outcome.OK),
        ([], [], False, None, Outcome.MODEL_RETURNED_NONE),
        ([], [{"b": 1}], False, None, Outcome.FILTERED_TO_EMPTY),
        ([{"a": 1}], [{"a": 1}, {"a": 2}], True, "max_rows", Outcome.TRUNCATED),
        ([], [{"a": 1}], True, "max_rows", Outcome.STOPPED_BY_LIMIT),
        # A crossed ceiling takes precedence over the result shape.
        ([{"a": 1}], [{"a": 1}], False, "max_cost", Outcome.STOPPED_BY_LIMIT),
        ([], [], False, "max_seconds", Outcome.STOPPED_BY_LIMIT),
        ([], [{"b": 1}], False, "max_cost", Outcome.STOPPED_BY_LIMIT),
        ([], [], False, "max_calls", Outcome.STOPPED_BY_LIMIT),
        # A crossed call, cost, or time ceiling takes precedence over a row cap.
        # The data model treats `truncated` as usable; these runs must not report it.
        ([{"a": 1}], [{"a": 1}, {"a": 2}], True, "max_seconds", Outcome.STOPPED_BY_LIMIT),
        ([{"a": 1}], [{"a": 1}, {"a": 2}], True, "max_cost", Outcome.STOPPED_BY_LIMIT),
        ([{"a": 1}], [{"a": 1}, {"a": 2}], True, "max_calls", Outcome.STOPPED_BY_LIMIT),
    ],
)
def test_the_outcome_matrix_is_total_and_unambiguous(
    records: list[dict[str, object]],
    model_returned: list[dict[str, object]],
    row_cap_fired: bool,
    stopped_by: str | None,
    expected: str,
) -> None:
    """Every reachable combination has one decided answer, in one place."""
    assert decide_outcome(records, model_returned, row_cap_fired, stopped_by) == expected


@pytest.mark.parametrize("records", [[{"a": 1}], []])
def test_an_interrupted_answer_is_partial_even_when_a_limit_also_fired(
    records: list[dict[str, object]],
) -> None:
    assert (
        decide_outcome(
            records,
            records,
            False,
            "max_calls",
            answered=True,
            interrupted=True,
            stop_reasons=["max_calls"],
        )
        == Outcome.PARTIAL
    )


def test_a_row_cap_and_harder_limit_remain_stopped_without_interruption() -> None:
    assert (
        decide_outcome(
            [{"a": 1}],
            [{"a": 1}, {"a": 2}],
            True,
            "max_seconds",
            stop_reasons=["max_seconds", "max_rows"],
        )
        == Outcome.STOPPED_BY_LIMIT
    )


def test_a_child_does_not_inherit_a_parents_earlier_refusal() -> None:
    """A budget's history is not this request's stop.

    A later affordable request must not inherit the parent budget's earlier stop reason.
    """
    budget = Budget(Limits(max_rows=2))
    source = LlmSource(client=ScriptedClient([THREE_RECORDS, ONE_RECORD, ONE_RECORD]))

    # The first request is cut short, which sets stopped_by on the shared budget.
    first = source.fetch("who?", budget=budget)
    assert first.meta.stopped_by == "max_rows"
    assert len(first) == 2

    # The second is refused by the same spent ceiling, which is its own stop, not the
    # first one's. The distinction matters when the axis is cost or time, where a later
    # affordable request would otherwise report a ceiling that never fired for it.
    second = source.fetch("who?", budget=budget)
    assert second.records == []
    assert second.meta.stopped_by == "max_rows"
    assert second.meta.outcome == Outcome.STOPPED_BY_LIMIT


def test_a_spent_cost_budget_does_not_mark_a_request_that_never_ran_over() -> None:
    budget = Budget(Limits(max_calls=1))
    source = LlmSource(client=ScriptedClient([ONE_RECORD, ONE_RECORD]))

    first = source.fetch("who?", budget=budget)
    assert first.meta.stopped_by is None, "spending the last allowed call refuses nothing"
    assert first.meta.outcome == Outcome.OK
