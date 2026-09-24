"""The surface the data layer calls: execute_query, execute_query_nl, and metadata."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from typing import Any, cast

import pytest
from blue_data.source.source import DataSource  # pyright: ignore[reportMissingTypeStubs]

from blue_data_llm_source import Budget, Capability, Limits, LlmSource
from blue_data_llm_source import _constants as C
from blue_data_llm_source.types import ConfigError, LlmSourceError, ParseError, QueryError
from tests.doubles import LitellmStub, ScriptedClient, chat_payload

ONE_RECORD = '[{"name": "Ada", "age": 36}]'
TWO_RECORDS = '[{"name": "Ada"}, {"name": "Grace"}]'


# ------------------------------------------------------------- execute_query --
def test_execute_query_accepts_a_plain_string() -> None:
    client = ScriptedClient([ONE_RECORD])
    records = LlmSource(client=client).execute_query("who is the president?")

    assert records == [{"name": "Ada", "age": 36}]
    assert "who is the president?" in client.prompts[0]


def test_execute_query_accepts_a_fetch_op() -> None:
    client = ScriptedClient([ONE_RECORD])
    records = LlmSource(client=client).execute_query(
        {"op": C.OP_FETCH, "query": "who?", "attr_names": ["name"], "context": "be brief"}
    )

    assert records == [{"name": "Ada"}]
    assert "be brief" in client.prompts[0]


def test_a_fetch_op_accepts_attrs_as_well_as_attr_names() -> None:
    client = ScriptedClient([ONE_RECORD])
    records = LlmSource(client=client).execute_query(
        {"op": C.OP_FETCH, "query": "who?", "attrs": [{"name": "age", "type": "int"}]}
    )
    assert records == [{"age": 36}]


def test_a_fetch_op_honours_max_records() -> None:
    client = ScriptedClient([TWO_RECORDS])
    records = LlmSource(client=client).execute_query(
        {"op": C.OP_FETCH, "query": "who?", "max_records": 1}
    )
    assert records == [{"name": "Ada"}]


def test_a_models_op_reports_the_protocol_and_variant() -> None:
    client = ScriptedClient()
    records = LlmSource(client=client).execute_query({"op": C.OP_MODELS})

    assert records == [{"protocol": "llm", "variant": "openai"}]
    assert client.prompts == []


def test_an_unknown_op_raises_query_error_listing_the_supported_ops() -> None:
    client = ScriptedClient()
    with pytest.raises(QueryError) as caught:
        LlmSource(client=client).execute_query({"op": "LLM.DROP"})

    message = str(caught.value)
    assert "LLM.DROP" in message
    assert C.OP_FETCH in message
    assert C.OP_MODELS in message
    assert client.prompts == []


def test_a_dict_query_without_an_op_is_rejected() -> None:
    with pytest.raises(QueryError, match="unknown op"):
        LlmSource(client=ScriptedClient()).execute_query({"query": "who?"})


def test_optional_properties_reach_the_client() -> None:
    client = ScriptedClient([ONE_RECORD])
    LlmSource(client=client).execute_query(
        "who?",
        optional_properties={"llm.reasoning_effort": "none", "llm.temperature": 0.0},
    )
    assert client.seen_properties[0] is not None
    assert client.seen_properties[0]["llm.temperature"] == 0.0


ESTIMATE_PRICED = {"llm.model": "gpt-4o-mini", "llm.max_tokens": 32}


def _estimate(spec: dict[str, Any], **options: Any) -> dict[str, Any]:
    """One `LLM.ESTIMATE` record, priced against a model with published rates."""
    records = LlmSource(properties=dict(ESTIMATE_PRICED)).execute_query(
        spec, optional_properties=options or None
    )
    assert len(records) == 1
    return records[0]


def test_an_estimate_op_reads_the_request_the_way_a_fetch_op_does() -> None:
    """The estimate must use the same request arguments as the fetch.

    Attributes and context change the prompt, so omitting either would price a different
    request.
    """
    plain = _estimate({"op": C.OP_ESTIMATE, "query": "who?"})
    from_spec = _estimate({"op": C.OP_ESTIMATE, "query": "who?", "attrs": ["name", "age"]})
    from_options = _estimate({"op": C.OP_ESTIMATE, "query": "who?"}, attrs=["name", "age"])

    assert from_options == from_spec
    assert from_options != plain

    by_name = _estimate({"op": C.OP_ESTIMATE, "query": "who?"}, attr_names=["name", "age"])
    assert by_name == from_spec

    with_context = _estimate({"op": C.OP_ESTIMATE, "query": "who?"}, context="be brief")
    assert with_context["prompt_bytes"] != plain["prompt_bytes"]


def test_an_estimate_op_gives_optional_properties_the_precedence_a_fetch_op_gives_them() -> None:
    """Both ops apply optional properties with the same precedence."""
    both = _estimate(
        {"op": C.OP_ESTIMATE, "query": "who?", "attrs": ["name"]}, attrs=["name", "age"]
    )
    assert both == _estimate({"op": C.OP_ESTIMATE, "query": "who?", "attrs": ["name", "age"]})


def test_an_estimate_op_refuses_the_row_cap_it_cannot_honour() -> None:
    """`estimate` takes no row cap: the model is asked once and the surplus is dropped.

    Accepting one would report a figure the caller believes their cap moved.
    """
    for spec, options in (
        ({"op": C.OP_ESTIMATE, "query": "who?"}, {"max_records": 3}),
        ({"op": C.OP_ESTIMATE, "query": "who?", "max_records": 3}, {}),
    ):
        with pytest.raises(ConfigError, match="max_records"):
            LlmSource(properties=dict(ESTIMATE_PRICED)).execute_query(
                spec, optional_properties=options or None
            )


# ---------------------------------------------------------- execute_query_nl --
def test_execute_query_nl_returns_records() -> None:
    client = ScriptedClient([ONE_RECORD])
    records = LlmSource(client=client).execute_query_nl("who is the president?")

    assert records == [{"name": "Ada", "age": 36}]
    assert "who is the president?" in client.prompts[0]


def test_execute_query_nl_reads_attrs_from_optional_properties() -> None:
    client = ScriptedClient([ONE_RECORD])
    records = LlmSource(client=client).execute_query_nl(
        "who?", optional_properties={"attr_names": ["name"]}
    )
    assert records == [{"name": "Ada"}]


def test_a_supplied_llm_fn_is_used_as_the_client() -> None:
    seen: list[str] = []

    def injected_model(prompt: str) -> str:
        seen.append(prompt)
        return '[{"name": "Ada"}]'

    configured = ScriptedClient(['[{"name": "must not be used"}]'])
    records = LlmSource(client=configured).execute_query_nl("who?", llm_fn=injected_model)

    assert records == [{"name": "Ada"}]
    assert len(seen) == 1
    assert "who?" in seen[0]
    assert configured.prompts == []


def test_a_supplied_llm_fn_may_be_a_lambda() -> None:
    configured = ScriptedClient(['[{"name": "must not be used"}]'])
    # Bound rather than passed inline: llm_fn is Any on the platform signature, so a bare
    # lambda has nothing to infer its parameter from.
    stub: Callable[[str], str] = lambda prompt: '[{"name": "Ada"}]'  # noqa: E731
    records = LlmSource(client=configured).execute_query_nl("who?", llm_fn=stub)

    assert records == [{"name": "Ada"}]
    assert configured.prompts == []


def test_translate_nl_to_native_query_is_the_identity() -> None:
    source = LlmSource(client=ScriptedClient())
    assert source.translate_nl_to_native_query("who?", None, None) == "who?"


def test_translate_nl_to_native_query_maps_an_empty_query_to_none() -> None:
    source = LlmSource(client=ScriptedClient())
    assert source.translate_nl_to_native_query("", None, None) is None


# ------------------------------------------------------- protocol and variant --
def test_protocol_is_always_llm() -> None:
    assert LlmSource().protocol == C.PROTOCOL


def test_protocol_cannot_be_overridden_by_caller_properties() -> None:
    source = LlmSource(properties={"connection": {"protocol": "sql"}})
    assert source.protocol == "llm"


def test_variant_defaults_to_openai() -> None:
    assert LlmSource().variant == C.DEFAULT_VARIANT


def test_provider_defaults_are_gpt_6_luna_without_sampling_temperature() -> None:
    client = ScriptedClient([ONE_RECORD])
    source = LlmSource(client=client)
    source.fetch("who?")
    sent = client.seen_properties[0]
    assert sent is not None
    assert sent["llm.model"] == "gpt-6-luna"
    assert sent["llm.reasoning_effort"] == "medium"
    assert sent["llm.max_tokens"] == 25_000
    assert "llm.temperature" not in sent


def test_no_package_default_is_published_as_a_setting() -> None:
    """A rebuild reads this map back as caller settings, so no package default is in it."""
    properties = LlmSource().properties
    assert set(properties) == {"connection"}
    assert "llm.model" not in properties
    assert "llm.stream" not in properties
    assert "llm.reasoning_effort" not in properties
    assert "llm.max_tokens" not in properties
    assert "llm.temperature" not in properties


def test_selecting_gpt_4o_restores_its_legacy_defaults() -> None:
    client = ScriptedClient([ONE_RECORD])
    LlmSource(properties={"llm.model": "gpt-4o"}, client=client).fetch("who?")
    assert client.seen_properties[0] is not None
    assert client.seen_properties[0]["llm.model"] == "gpt-4o"
    assert client.seen_properties[0]["llm.max_tokens"] == 4_096
    assert "llm.reasoning_effort" not in client.seen_properties[0]


def test_dated_gpt_6_luna_uses_its_profile_defaults() -> None:
    client = ScriptedClient([ONE_RECORD])
    LlmSource(properties={"llm.model": "gpt-6-luna-2026-09-22"}, client=client).fetch("who?")
    assert client.seen_properties[0] is not None
    assert client.seen_properties[0]["llm.reasoning_effort"] == "medium"
    assert client.seen_properties[0]["llm.max_tokens"] == 25_000


def test_model_output_allowance_follows_the_model_profile() -> None:
    source = LlmSource()
    assert source.max_tokens_for_model("gpt-4o") == 4_096
    assert source.max_tokens_for_model("gpt-6-luna-2026-09-22") == 25_000


def test_explicit_output_allowance_applies_to_every_model() -> None:
    source = LlmSource(properties={"llm.max_tokens": 8_192})
    assert source.max_tokens_for_model("gpt-4o") == 8_192
    assert source.max_tokens_for_model("gpt-6-luna") == 8_192


def test_selecting_gpt_4o_keeps_an_explicit_output_allowance() -> None:
    client = ScriptedClient([ONE_RECORD])
    source = LlmSource(properties={"llm.model": "gpt-4o", "llm.max_tokens": 8_192}, client=client)
    source.fetch("who?")
    assert client.seen_properties[0] is not None
    assert client.seen_properties[0]["llm.max_tokens"] == 8_192


@pytest.mark.parametrize("effort", ["minimal", "extreme", 1, True])
def test_invalid_reasoning_effort_is_refused(effort: object) -> None:
    with pytest.raises(ConfigError, match="reasoning"):
        LlmSource(properties={"llm.reasoning_effort": effort}).fetch(
            "who?", llm_fn=_answering(ONE_RECORD)
        )


def test_gpt_4o_refuses_reasoning_effort() -> None:
    with pytest.raises(ConfigError, match="gpt-4o"):
        LlmSource(properties={"llm.model": "gpt-4o", "llm.reasoning_effort": "medium"}).fetch(
            "who?", llm_fn=_answering(ONE_RECORD)
        )


def test_variant_can_be_set_by_caller_properties() -> None:
    source = LlmSource(properties={"connection": {"variant": "anthropic"}})
    assert source.variant == "anthropic"
    assert source.protocol == "llm"


def test_connection_properties_are_merged_not_replaced() -> None:
    source = LlmSource(properties={"connection": {"user": "ada"}})
    assert source.properties["connection"]["user"] == "ada"
    assert source.properties["connection"]["variant"] == C.DEFAULT_VARIANT


def test_non_connection_properties_are_kept_as_given() -> None:
    source = LlmSource(properties={"llm.model": "gpt-4o-mini"})
    assert source.properties["llm.model"] == "gpt-4o-mini"


def test_the_source_keeps_the_name_it_was_given() -> None:
    assert LlmSource(name="knowledge").name == "knowledge"


# -------------------------------------------------------------------- metadata --
def test_fetch_metadata_reports_the_protocol_and_variant() -> None:
    assert LlmSource().fetch_metadata() == {"protocol": "llm", "variant": "openai"}


def test_fetch_databases_returns_the_one_virtual_database() -> None:
    assert LlmSource().fetch_databases() == [C.DATABASE]


def test_fetch_database_collections_returns_the_one_virtual_collection() -> None:
    assert LlmSource().fetch_database_collections(C.DATABASE) == [C.COLLECTION]


def test_fetch_database_collection_entities_returns_the_virtual_entity() -> None:
    entities = LlmSource().fetch_database_collection_entities(C.DATABASE, C.COLLECTION)
    assert entities == {
        C.ENTITY: {"properties": {C.ENTITY_ATTRIBUTE: {"type": C.ENTITY_ATTRIBUTE_TYPE}}}
    }


def test_the_virtual_hierarchy_is_walkable_end_to_end() -> None:
    source = LlmSource()
    database = source.fetch_databases()[0]
    collection = source.fetch_database_collections(database)[0]
    entities = source.fetch_database_collection_entities(database, collection)
    assert list(entities) == [C.ENTITY]


def test_the_remaining_metadata_calls_are_empty() -> None:
    source = LlmSource()
    assert source.fetch_schema() == {}
    assert source.fetch_database_metadata(C.DATABASE) == {}
    assert source.fetch_database_schema(C.DATABASE) == {}
    assert source.fetch_database_collection_metadata(C.DATABASE, C.COLLECTION) == {}
    assert source.fetch_database_collection_relations(C.DATABASE, C.COLLECTION) == {}


def test_the_stats_calls_report_nothing() -> None:
    source = LlmSource()
    assert source.fetch_source_stats() is None
    assert source.fetch_database_stats(C.DATABASE) is None


def test_connect_and_disconnect_hold_no_state() -> None:
    source = LlmSource()
    assert source.connect() == {}
    assert source.disconnect() is None


# ----------------------------------------------- what a caller gets for a wrong client --
def test_a_client_missing_the_methods_is_refused_with_the_contract() -> None:
    """The error should name the client contract instead of exposing an AttributeError."""
    with pytest.raises(ConfigError, match=r"complete\(prompt, properties\)"):
        LlmSource(client=object()).fetch("who?")


def test_a_client_whose_signature_cannot_take_properties_is_refused() -> None:
    """The client must accept properties to receive the run's budget and usage sink."""

    class Narrow:
        def complete(self, prompt: str) -> str:
            return "[]"

        async def acomplete(self, prompt: str) -> str:
            return "[]"

    with pytest.raises(ConfigError, match=r"cannot be called as"):
        LlmSource(client=Narrow()).fetch("who?")


@pytest.mark.parametrize(
    "shape",
    ["keyword_only", "keyword_catchall"],
)
def test_a_signature_that_cannot_take_properties_positionally_is_refused(shape: str) -> None:
    """The source must be able to pass properties as the second positional argument.

    `acomplete(prompt, *, properties=None)` and `acomplete(prompt, **kwargs)` both have two
    parameters and neither can receive the second argument positionally, so both reached the
    first call and failed there with the raw TypeError this check replaces.
    """

    class KeywordOnly:
        def complete(self, prompt: str, *, properties: object = None) -> str:
            return "[]"

        async def acomplete(self, prompt: str, *, properties: object = None) -> str:
            return "[]"

    class KeywordCatchall:
        def complete(self, prompt: str, **kwargs: object) -> str:
            return "[]"

        async def acomplete(self, prompt: str, **kwargs: object) -> str:
            return "[]"

    client = KeywordOnly() if shape == "keyword_only" else KeywordCatchall()
    with pytest.raises(ConfigError, match=r"cannot be called as"):
        LlmSource(client=client).fetch("who?")


def test_a_client_taking_kwargs_is_accepted() -> None:
    """A client with a permissive signature satisfies the contract."""

    class Flexible:
        def __init__(self) -> None:
            self.budget = Budget()

        def budget_capabilities(self) -> frozenset[str]:
            return Capability.ALL

        def complete(self, *args: object, **kwargs: object) -> str:
            return '[{"name": "Ada"}]'

        async def acomplete(self, *args: object, **kwargs: object) -> str:
            return '[{"name": "Ada"}]'

    assert LlmSource(client=Flexible()).fetch("who?").records == [{"name": "Ada"}]


def test_an_unknown_adapter_key_is_refused_on_every_client_path() -> None:
    """Validate adapter keys on every client path, not only in the built-in client."""

    def stub(prompt: str) -> str:
        return "[]"

    with pytest.raises(ConfigError, match="unknown adapter override"):
        LlmSource(properties={"llm.temprature": 0.1}).fetch("who?", llm_fn=stub)

    with pytest.raises(ConfigError, match="unknown adapter override"):
        LlmSource(properties={"llm.temprature": 0.1}).estimate("who?")


def test_a_callable_reports_its_accounting_as_unknown() -> None:
    """Do not infer provider usage for a caller-supplied callable.

    A callable may answer from memory, call one provider, or fan out to several. Publishing
    `calls=1` and `cost=0.0` for it looked measured and was not: a callable making five real
    provider calls reported one call and nothing spent, and a planner or audit log could not
    tell that apart from free local work.
    """
    made: list[str] = []

    def fans_out(prompt: str) -> str:
        made.extend(["one", "two", "three", "four", "five"])
        return '[{"country": "France"}]'

    result = LlmSource().fetch("who?", llm_fn=fans_out)

    assert len(made) == 5
    assert result.meta.accounting_known is False, (
        "a callable's accounting was published as though it described provider work"
    )


def test_the_default_client_reports_its_accounting_as_known() -> None:
    """The flag should report known accounting for the default client."""
    from tests.doubles import ScriptedClient

    source = LlmSource(client=ScriptedClient(['[{"country": "France"}]']))
    assert source.fetch("who?").meta.accounting_known is True


def _answering(text: str) -> Callable[[str], str]:
    """A callable answering with *text*, typed so the checker can see through it."""

    def answer(prompt: str) -> str:
        return text

    return answer


def test_a_number_too_large_to_represent_is_refused_at_the_parser() -> None:
    """Not reported as a type mismatch: it never becomes a record value at all.

    This case used to be parametrized into the mismatch test above, whose label said the
    parser had already refused it. The parser had not — ``parse_constant`` sees only the
    bare words ``NaN`` and ``Infinity``, and a literal that overflows in ``float()`` took a
    different route into the record. The test passed on the verifier's finite check while
    describing a parser guard that was not there.
    """
    with pytest.raises(ParseError, match="out of range"):
        LlmSource().fetch(
            "q", attrs=[{"name": "v", "type": "float"}], llm_fn=_answering('[{"v": 1e400}]')
        )
    # Refused whether or not a type is declared — the value cannot be written back as JSON.
    with pytest.raises(ParseError, match="out of range"):
        LlmSource().fetch("q", llm_fn=_answering('[{"v": 1e400}]'))


@pytest.mark.parametrize(
    ("declared", "answer", "why"),
    [
        ("string", '[{"v": 123}]', "a number in a declared string field"),
        ("date", '[{"v": "2026-02-30"}]', "a date shaped correctly that does not exist"),
    ],
)
def test_a_declared_type_that_is_not_satisfied_is_reported(
    declared: str, answer: str, why: str
) -> None:
    """Being told the records are typed, while one of them is not, is the failure.

    A declared type with no coercer passed everything, floats were checked with `isinstance`
    alone so a coercion could reintroduce the non-finite value the JSON parser had just
    refused, and dates were checked against a digit pattern that accepts the thirtieth of
    February.
    """
    result = LlmSource().fetch(
        "q", attrs=[{"name": "v", "type": declared}], llm_fn=_answering(answer)
    )
    assert result.meta.type_mismatches, why


def test_a_real_date_still_passes() -> None:
    """The calendar check must not refuse dates that exist."""
    result = LlmSource().fetch(
        "q", attrs=[{"name": "v", "type": "date"}], llm_fn=_answering('[{"v": "2026-02-28"}]')
    )
    assert result.meta.type_mismatches == []


def test_two_non_latin_attribute_names_are_distinct() -> None:
    """Normalisation deleted every character of both, so both became the empty key.

    The request was then refused as ambiguous before any answer was looked at, for a schema
    whose returned keys matched the requested ones exactly and needed no fuzzy matching.
    """
    result = LlmSource().fetch(
        "q",
        attrs=["国", "年"],
        llm_fn=_answering('[{"国": "日本", "年": 1958}]'),
    )
    assert result.records == [{"国": "日本", "年": 1958}]
    assert result.meta.outcome == "ok"


@pytest.mark.parametrize("value", [0, -1, True, "x", 2.5])
def test_an_unusable_max_parallel_is_refused_at_the_boundary(value: object) -> None:
    """Zero waited for ever and a negative escaped as a raw asyncio ValueError.

    Both are ordinary values for a documented integer property, and neither belongs outside
    this package's error hierarchy.
    """
    with pytest.raises(ConfigError, match="max_parallel"):
        LlmSource(properties={"llm.max_parallel": value}).fetch("q", llm_fn=_answering("[]"))


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("llm.model", 123),
        ("llm.model", ""),
        ("llm.model", "   "),
        ("llm.model", ["gpt-4o"]),
        ("llm.max_tokens", 0),
        ("llm.max_tokens", -5),
        ("llm.max_tokens", 1.5),
        ("llm.max_tokens", "many"),
        ("llm.max_tokens", True),
        ("llm.temperature", "warm"),
        ("llm.temperature", float("nan")),
        ("llm.temperature", float("inf")),
        ("llm.temperature", True),
        ("llm.timeout", 0),
        ("llm.timeout", -1.0),
        ("llm.timeout", "soon"),
        ("llm.timeout", float("nan")),
        ("llm.timeout", True),
    ],
)
def test_an_adapter_override_outside_its_range_is_refused(key: str, value: object) -> None:
    """The output allowance is the one that costs money on an unbounded request.

    A zero, negative or non-numeric `llm.max_tokens` only raised from `pricing.bound_cost`,
    which does not run unless a cost ceiling is set, so it reached a billed call untouched
    on every other request. `llm.model` is the same shape of hole with no numeric range at
    all: the call builder asks whether the name is truthy, so `123` was sent as a model.
    """
    with pytest.raises(ConfigError, match=key.replace(".", r"\.")):
        LlmSource(properties={key: value}).fetch("q", llm_fn=_answering("[]"))


@pytest.mark.parametrize(
    "properties",
    [
        {"llm.model": "gpt-4o-mini"},
        {"llm.model": None},
        {"llm.max_tokens": 1},
        {"llm.max_tokens": 4096},
        {"llm.max_tokens": None},
        {"llm.reasoning_effort": "none", "llm.temperature": 0},
        {"llm.reasoning_effort": "none", "llm.temperature": 2.0},
        {"llm.reasoning_effort": "none", "llm.temperature": -0.5},
        {"llm.timeout": 30},
        {"llm.timeout": 0.5},
    ],
)
def test_a_value_its_consumer_can_use_is_still_accepted(properties: dict[str, Any]) -> None:
    """The half of the range that matters: a bound too tight refuses a working request.

    `None` for the allowance means no cap. A negative temperature is outside every provider's
    documented range but inside the one the wire transport keeps, and this library does not
    hold a table of provider ranges to be more specific than that.
    """
    assert LlmSource(properties=properties).fetch("q", llm_fn=_answering("[]")).records == []


def test_accounting_is_known_only_when_the_answering_client_can_attest() -> None:
    """Read from the client that answered, not from which argument the caller used.

    Deciding it from `llm_fn is None` meant the same opaque callable reported its accounting
    as unknown through `llm_fn=` and as known through `client=`, and an injected client
    implementing only the required pair reported zero calls and zero cost as measured fact.
    """
    from blue_data_llm_source.llm.client import FunctionClient
    from tests.doubles import ScriptedClient

    answer = '[{"name": "Ada"}]'

    class Opaque:
        """Exactly the documented required pair, and nothing else."""

        def complete(self, prompt: str, properties: dict[str, Any] | None = None) -> str:
            return answer

        async def acomplete(self, prompt: str, properties: dict[str, Any] | None = None) -> str:
            return answer

    cases = {
        "llm_fn": LlmSource().fetch("q", llm_fn=_answering(answer)),
        "injected FunctionClient": LlmSource(client=FunctionClient(_answering(answer))).fetch("q"),
        "opaque injected": LlmSource(client=Opaque()).fetch("q"),
    }
    for label, result in cases.items():
        assert result.meta.accounting_known is False, f"{label} claimed its accounting was known"

    # A client that declares it can be held to the call and cost ceilings does know.
    assert LlmSource(client=ScriptedClient([answer])).fetch("q").meta.accounting_known is True


@pytest.mark.parametrize(
    ("properties", "match"),
    [
        ({"llm.max_parallel": 0}, "max_parallel"),
        ({"llm.rate_limit": 10}, "call spacing"),
        ({"llm.nosuchkey": 1}, "unknown adapter override"),
    ],
)
def test_an_injected_clients_own_properties_cross_the_same_boundary(
    properties: dict[str, Any], match: str
) -> None:
    """A deliberate setting on an injected client is still configuration.

    Validation ran on what the caller passed and then the client's own properties were merged
    in afterwards, so they reached the provider having crossed nothing: `llm.max_parallel=0`
    built a semaphore with no permits and waited, and a misspelled key was accepted silently.
    """
    from blue_data_llm_source.llm.client import DefaultLlmClient

    with pytest.raises(ConfigError, match=match):
        LlmSource(client=DefaultLlmClient(properties=properties)).fetch("who?")


@pytest.mark.parametrize(
    ("carried", "expected_stop"),
    [
        ({"limits.max_calls": 0}, "max_calls"),
        ({"limits.max_cost": 0.0}, "max_cost"),
    ],
)
def test_a_ceiling_an_injected_client_carries_actually_binds(
    carried: dict[str, Any], expected_stop: str
) -> None:
    """Validating a ceiling is not the same as enforcing it.

    The resolved three-layer map was validated and then `Limits` was built from a different
    merge that omitted the client's own properties, so a client configured with
    `limits.max_calls: 0` reached the provider and `estimate` published three attempts for a
    client configured for one.
    """
    from blue_data_llm_source.llm.client import DefaultLlmClient

    client = DefaultLlmClient(properties={"llm.model": "gpt-4o-mini", **carried})
    result = LlmSource(client=client).fetch("who?")

    assert result.meta.stopped_by == expected_stop
    assert result.meta.calls == 0, "a provider entry happened under a zero ceiling"


def test_the_estimate_reads_the_retry_depth_an_injected_client_carries() -> None:
    """The same merge, so the published attempt count is the one the run will use."""
    from blue_data_llm_source.llm.client import DefaultLlmClient

    client = DefaultLlmClient(properties={"llm.model": "gpt-4o-mini", "limits.max_retries": 0})
    assert LlmSource(client=client).estimate("who?").max_attempts == 1


def test_an_unreadable_provider_cost_is_not_reported_as_a_known_zero() -> None:
    """The default client may answer a model it cannot price, and then must say so.

    With no cost ceiling configured there is no bound either, so the figure is zero and
    means nothing. Publishing it as measured makes an unpriced call look free — the same
    false precision the callable case already refuses.
    """
    import sys
    import types

    answer = '[{"name": "Ada"}]'
    module = types.ModuleType("litellm")

    def completion(**_: object) -> dict[str, Any]:
        return {"choices": [{"message": {"content": answer}}], "usage": {}}

    async def acompletion(**_: object) -> dict[str, Any]:
        return completion()

    def unpriceable(**_: object) -> float:
        raise RuntimeError("no published price for this model")

    module.completion = completion  # pyright: ignore[reportAttributeAccessIssue]
    module.acompletion = acompletion  # pyright: ignore[reportAttributeAccessIssue]
    module.completion_cost = unpriceable  # pyright: ignore[reportAttributeAccessIssue]

    previous = sys.modules.get("litellm")
    sys.modules["litellm"] = module
    try:
        result = LlmSource(properties={"llm.model": "unfamiliar"}).fetch("who?")
    finally:
        if previous is None:
            sys.modules.pop("litellm", None)
        else:
            sys.modules["litellm"] = previous

    assert result.meta.calls == 1
    assert result.meta.accounting_known is False, "an unreadable cost was published as measured"


@pytest.mark.parametrize(
    ("shape", "why"),
    [
        ({"choices": []}, "an empty choices list"),
        ({"choices": [{"message": {"content": None}}]}, "a null message content"),
    ],
)
def test_a_malformed_completion_is_retried_and_not_cached(shape: dict[str, Any], why: str) -> None:
    """What a refusal or a non-text completion looks like, and both bypassed the retry loop.

    They passed the materialise check because `choices` was a list, so the attempt settled
    and the payload was cached; extraction raised afterwards, outside tenacity. The
    documented retries never ran and one transient bad response became a cached failure.
    """
    import sys
    import types

    from blue_data_llm_source.types import TransportError

    entries: list[int] = []
    module = types.ModuleType("litellm")

    def completion(**_: object) -> dict[str, Any]:
        entries.append(1)
        return dict(shape)

    async def acompletion(**_: object) -> dict[str, Any]:
        return completion()

    module.completion = completion  # pyright: ignore[reportAttributeAccessIssue]
    module.acompletion = acompletion  # pyright: ignore[reportAttributeAccessIssue]

    def free(**_: object) -> float:
        return 0.0

    module.completion_cost = free  # pyright: ignore[reportAttributeAccessIssue]

    class Recording:
        """Counts what reaches the cache, which is the thing under test."""

        def __init__(self) -> None:
            self.written: list[str] = []

        def get(self, key: str) -> None:
            return None

        def set(self, key: str, value: Any) -> None:
            self.written.append(key)

    cache = Recording()
    previous = sys.modules.get("litellm")
    sys.modules["litellm"] = module
    try:
        source = LlmSource(properties={"llm.model": "gpt-4o-mini", "cache_store": cache})
        with pytest.raises(TransportError):
            source.fetch("who?")
    finally:
        if previous is None:
            sys.modules.pop("litellm", None)
        else:
            sys.modules["litellm"] = previous

    assert len(entries) == 3, f"{why} was not retried: {len(entries)} attempt(s)"
    assert cache.written == [], f"{why} was written to the cache"


def test_a_value_that_will_not_coerce_is_returned_as_it_arrived() -> None:
    """`float("1e309")` succeeds as `inf`, which the JSON parser refuses to read back."""
    result = LlmSource().fetch(
        "q", attrs=[{"name": "v", "type": "float"}], llm_fn=_answering('[{"v": "1e309"}]')
    )
    assert result.records == [{"v": "1e309"}]
    assert result.meta.type_mismatches


def test_nested_key_order_does_not_defeat_deduplication() -> None:
    """JSON object order is not semantic, and the two records are equal in Python."""
    answer = (
        '[{"name": "same", "meta": {"a": 1, "b": 2}}, {"name": "same", "meta": {"b": 2, "a": 1}}]'
    )
    result = LlmSource().fetch("q", llm_fn=_answering(answer))
    assert len(result.records) == 1, result.records


def test_a_failed_attempt_that_could_not_be_priced_is_not_hidden_by_a_retry() -> None:
    """The property is every contacted attempt, not every attempt that left a record.

    A contacted failure calls `keep_unpriced` and used to append no usage entry at all, so a
    successful retry was the only entry the result saw and the whole fetch claimed its
    accounting was measured — while an unpriced attempt had already reached the provider and
    may have been billed.
    """
    import sys
    import types

    entries: list[int] = []
    module = types.ModuleType("litellm")

    def completion(**_: object) -> dict[str, Any]:
        entries.append(1)
        if len(entries) == 1:
            raise RuntimeError("transient provider failure")
        return {"choices": [{"message": {"content": '[{"name": "Ada"}]'}}], "usage": {}}

    async def acompletion(**_: object) -> dict[str, Any]:
        return completion()

    def priced(**_: object) -> float:
        return 0.001

    module.completion = completion  # pyright: ignore[reportAttributeAccessIssue]
    module.acompletion = acompletion  # pyright: ignore[reportAttributeAccessIssue]
    module.completion_cost = priced  # pyright: ignore[reportAttributeAccessIssue]

    previous = sys.modules.get("litellm")
    sys.modules["litellm"] = module
    try:
        result = LlmSource(properties={"llm.model": "gpt-4o-mini"}).fetch("who?")
    finally:
        if previous is None:
            sys.modules.pop("litellm", None)
        else:
            sys.modules["litellm"] = previous

    assert len(entries) == 2, "the retry did not happen, so nothing was tested"
    assert result.meta.calls == 2
    assert result.meta.accounting_known is False, (
        "a successful retry hid a contacted attempt that could not be priced"
    )


def test_non_text_message_content_is_retried_and_not_cached() -> None:
    """`None` was refused and every other wrong type was not.

    A list, mapping or number settled, cached, and then failed in `str()` outside the retry
    guard, so a transient anomaly became a permanently cached failure.
    """
    import sys
    import types

    from blue_data_llm_source.types import TransportError

    entries: list[int] = []
    written: list[str] = []
    module = types.ModuleType("litellm")

    def completion(**_: object) -> dict[str, Any]:
        entries.append(1)
        return {"choices": [{"message": {"content": [{"type": "text"}]}}], "usage": {}}

    async def acompletion(**_: object) -> dict[str, Any]:
        return completion()

    def free(**_: object) -> float:
        return 0.0

    module.completion = completion  # pyright: ignore[reportAttributeAccessIssue]
    module.acompletion = acompletion  # pyright: ignore[reportAttributeAccessIssue]
    module.completion_cost = free  # pyright: ignore[reportAttributeAccessIssue]

    class Recording:
        def get(self, key: str) -> None:
            return None

        def set(self, key: str, value: Any) -> None:
            written.append(key)

    previous = sys.modules.get("litellm")
    sys.modules["litellm"] = module
    try:
        source = LlmSource(properties={"llm.model": "gpt-4o-mini", "cache_store": Recording()})
        with pytest.raises(TransportError):
            source.fetch("who?")
    finally:
        if previous is None:
            sys.modules.pop("litellm", None)
        else:
            sys.modules["litellm"] = previous

    assert len(entries) == 3, f"non-text content was not retried: {len(entries)} attempt(s)"
    assert written == [], "non-text content was written to the cache"


def test_a_repeated_attribute_is_refused_before_the_provider_is_contacted() -> None:
    """A repeated `--attr` is an ordinary slip and must not be billed for.

    Detection lived in the aligner, after the answer came back, so the provider was contacted
    for a request the library already knew it would refuse.
    """
    contacted: list[int] = []

    def answer(prompt: str) -> str:
        contacted.append(1)
        return "[]"

    with pytest.raises(QueryError, match="appears twice"):
        LlmSource().fetch("who?", attrs=["name", "name"], llm_fn=answer)

    assert contacted == [], "a malformed request reached the provider"


def test_a_mismatch_index_points_at_the_record_the_caller_received() -> None:
    """Verification numbers records before dropping, deduping and capping renumber them.

    Published unchanged, ``record_index`` addressed a list the caller never sees: it named a
    different record than the one it described, and past the row cap it ran off the end.
    """
    # Record 0 answers nothing and is dropped, so the bad record moves from index 1 to 0.
    result = LlmSource().fetch(
        "q",
        attrs=[{"name": "n", "type": "int"}],
        llm_fn=_answering('[{"other": 1}, {"n": "not-a-number"}]'),
    )
    (mismatch,) = result.meta.type_mismatches
    assert result.records[mismatch["record_index"]]["n"] == "not-a-number"

    # A mismatch on a record the row cap removed is dropped with it, not left dangling.
    capped = LlmSource().fetch(
        "q",
        attrs=[{"name": "n", "type": "int"}],
        llm_fn=_answering('[{"n": 1}, {"n": 2}, {"n": "not-a-number"}]'),
        budget=Budget(Limits(max_rows=2)),
    )
    assert capped.meta.type_mismatches == []
    assert all(m["record_index"] < len(capped.records) for m in capped.meta.type_mismatches)


def test_a_collision_index_points_at_the_record_the_caller_received() -> None:
    """The collision list carried the same pre-filter numbering as the mismatch list."""
    result = LlmSource().fetch(
        "q",
        attrs=["name"],
        llm_fn=_answering('[{"other": 1}, {"name": "a", "NAME": "b"}]'),
    )
    (collision,) = result.meta.key_collisions
    assert 0 <= collision["record_index"] < len(result.records)
    assert result.records[collision["record_index"]]["name"] == "a"


def test_a_name_answered_in_a_different_unicode_form_still_maps() -> None:
    """Precomposed and decomposed spellings render identically and must align.

    Models return either form. The combining accent in the decomposed spelling is not a
    word character, so the fuzzy key stripped it and the two forms normalized apart — the
    attribute came back unmapped and the record was filtered away as answering nothing.
    """
    import unicodedata

    requested = unicodedata.normalize("NFC", "café")
    answered = unicodedata.normalize("NFD", "café")
    assert requested != answered

    result = LlmSource().fetch(
        "q", attrs=[requested], llm_fn=_answering(json.dumps([{answered: "yes"}]))
    )
    assert result.meta.unmapped_keys == []
    assert result.records == [{requested: "yes"}]


def test_an_accented_key_answers_the_same_way_in_either_unicode_form() -> None:
    """The invariant behind the fix: the outcome cannot depend on an invisible spelling.

    Requesting an unaccented name used to match an accented answer when the model happened
    to send the decomposed form — the combining mark was stripped as punctuation — and not
    match when it sent the precomposed one. Same request, same rendered answer, two
    different results. Both are now unmapped, and that is reported rather than guessed at:
    an accent is contrastive in plenty of languages, so folding it away would merge names
    the caller means to keep apart. Folding is recorded as a v1.1 question, not assumed here.
    """
    import unicodedata

    outcomes = [
        LlmSource()
        .fetch(
            "q",
            attrs=["cafe"],
            llm_fn=_answering(json.dumps([{unicodedata.normalize(form, "café"): "yes"}])),
        )
        .meta.unmapped_keys
        for form in ("NFC", "NFD")
    ]
    # Compared after normalizing, because the report echoes the key the model actually sent
    # and the two spellings differ as strings. What must agree is the mapping decision.
    folded = [[unicodedata.normalize("NFC", k) for k in o] for o in outcomes]
    assert folded[0] == folded[1] == ["café"]


def test_an_oversized_integer_is_a_type_mismatch_not_an_escaping_error() -> None:
    """A JSON integer wider than the float range against a declared float.

    Both the coercion and the finite check called `float()` on it unguarded, so the whole
    fetch died with `OverflowError` — outside the documented hierarchy, and after the call
    had been paid for. The contract for a value that will not coerce is that it is returned
    as it arrived and reported, which is what happens now.
    """
    huge = "9" * 400
    result = LlmSource().fetch(
        "q", attrs=[{"name": "v", "type": "float"}], llm_fn=_answering(f'[{{"v": {huge}}}]')
    )
    assert result.records[0]["v"] == int(huge), "returned as it arrived"
    assert [m["attribute"] for m in result.meta.type_mismatches] == ["v"]
    assert result.meta.outcome == "ok"

    # An ordinary integer still coerces, which is the behaviour the guard must not cost.
    fine = LlmSource().fetch(
        "q", attrs=[{"name": "v", "type": "float"}], llm_fn=_answering('[{"v": 3}]')
    )
    assert fine.records == [{"v": 3.0}]
    assert fine.meta.type_mismatches == []


def test_a_ceiling_too_large_to_be_a_float_is_refused_as_configuration() -> None:
    """`float()` is guarded at four sites in this package and was unguarded at this one."""
    with pytest.raises(ConfigError, match="too large"):
        Limits(max_seconds=10**400)
    with pytest.raises(ConfigError, match="too large"):
        Limits(max_cost=10**400)


def test_nothing_escapes_the_documented_error_hierarchy() -> None:
    """The promise four documents make, enforced in one place instead of at every site.

    The CLI's exit-code table and the MCP server's error format are both built on it, and it
    was previously maintained by per-site discipline with no chokepoint — so it failed at
    every site someone missed. The per-site guards remain; this is what makes the promise
    structural rather than a convention.
    """
    from blue_data_llm_source.types import LlmSourceError

    def exploding(prompt: str) -> str:
        raise ZeroDivisionError("something inside a caller's callable")

    with pytest.raises(LlmSourceError) as caught:
        LlmSource().fetch("q", llm_fn=exploding)
    assert isinstance(caught.value.__cause__, ZeroDivisionError), "the original is chained"


def test_the_boundary_does_not_swallow_a_cancellation() -> None:
    """A KeyboardInterrupt or a cancellation is not this package's to reclassify.

    Catching BaseException here would also break the reservation lifecycle, which depends on
    those unwinding cleanly.
    """

    def interrupted(prompt: str) -> str:
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        LlmSource().fetch("q", llm_fn=interrupted)


def test_a_type_name_this_library_cannot_check_is_reported_once_not_per_value() -> None:
    """The declared type is sent to the model verbatim, so an unlisted name is legitimate.

    An earlier version made it a mismatch on every value, to stop a misspelled type from
    checking nothing while the caller believed their records were typed. That risk is real,
    but the cure measured one mismatch per record — twenty-one on a batch holding a single
    genuinely bad number — which destroys the signal callers are told to monitor, and it
    condemned `price: money`, a declaration four shipped documents invite. The name is
    reported once, against the request; the values are carried through, as those documents
    say.
    """
    result = LlmSource().fetch(
        "q",
        attrs=[{"name": "price", "type": "money"}],
        llm_fn=_answering('[{"price": 1}, {"price": 2}, {"price": 3}]'),
    )
    assert result.records == [{"price": 1}, {"price": 2}, {"price": 3}]
    assert result.meta.type_mismatches == [], "not one accusation per value"
    assert result.meta.unchecked_types == ["money"], "and not silent either"

    # A type that *can* be checked still reports the value that failed it.
    checked = LlmSource().fetch(
        "q",
        attrs=[{"name": "n", "type": "int"}],
        llm_fn=_answering('[{"n": 1}, {"n": "not-a-number"}]'),
    )
    assert [m["attribute"] for m in checked.meta.type_mismatches] == ["n"]
    assert checked.meta.unchecked_types == []


# -------------------------------------------------------- the property map --
UNKNOWN = "unknown property"


def _injected_model(_prompt: str) -> str:
    return "[]"


def test_a_key_outside_every_namespace_is_refused() -> None:
    """The map is closed, not only the three namespaces inside it."""
    client = ScriptedClient([ONE_RECORD])

    with pytest.raises(ConfigError, match=UNKNOWN) as caught:
        LlmSource(client=client).fetch("who?", properties={"llm_fn": _injected_model})

    message = str(caught.value)
    assert "'llm_fn'" in message, "the refusal has to name the key the caller wrote"
    assert "llm.model" in message, "and list what it would have accepted"
    assert client.prompts == []


def test_the_refusal_arrives_before_a_client_exists() -> None:
    """No client is injected here, so the only thing that can answer is the default one.

    Moving the check past client resolution turns this into a transport failure or a
    connection the suite's offline guard counts, rather than a ConfigError.
    """
    with pytest.raises(ConfigError, match=UNKNOWN):
        LlmSource().fetch("who?", properties={"llm_fn": _injected_model})


def test_an_unknown_property_is_refused_for_an_empty_request_too() -> None:
    """A closed map that only applies to requests reaching the model is not closed."""
    client = ScriptedClient([])

    with pytest.raises(ConfigError, match=UNKNOWN):
        LlmSource(client=client).fetch("", properties={"llm_fn": _injected_model})

    assert client.prompts == []


def test_every_surface_that_takes_properties_refuses_an_unknown_key() -> None:
    """One key set, wherever the map arrives from."""
    calls: dict[str, Callable[[], object]] = {
        "constructor": lambda: LlmSource(
            properties={"nope": 1}, client=ScriptedClient([ONE_RECORD])
        ).fetch("who?"),
        "fetch": lambda: LlmSource(client=ScriptedClient([ONE_RECORD])).fetch(
            "who?", properties={"nope": 1}
        ),
        "afetch": lambda: asyncio.run(
            LlmSource(client=ScriptedClient([ONE_RECORD])).afetch("who?", properties={"nope": 1})
        ),
        "estimate": lambda: LlmSource(client=ScriptedClient()).estimate(
            "who?", properties={"nope": 1}
        ),
        "execute_query": lambda: LlmSource(client=ScriptedClient([ONE_RECORD])).execute_query(
            "who?", optional_properties={"nope": 1}
        ),
        "execute_query op": lambda: LlmSource(client=ScriptedClient([ONE_RECORD])).execute_query(
            {"op": C.OP_FETCH, "query": "who?"}, optional_properties={"nope": 1}
        ),
        "models op": lambda: LlmSource(client=ScriptedClient()).execute_query(
            {"op": C.OP_MODELS}, optional_properties={"nope": 1}
        ),
        "estimate op": lambda: LlmSource(client=ScriptedClient()).execute_query(
            {"op": C.OP_ESTIMATE, "query": "who?"}, optional_properties={"nope": 1}
        ),
        "execute_query_nl": lambda: LlmSource(client=ScriptedClient([ONE_RECORD])).execute_query_nl(
            "who?", optional_properties={"nope": 1}
        ),
        "execute_nl_tool": lambda: LlmSource(client=ScriptedClient([ONE_RECORD])).execute_nl_tool(
            "execute_query", {"query": "who?"}, optional_properties={"nope": 1}
        ),
    }

    for surface, call in calls.items():
        with pytest.raises(ConfigError, match=UNKNOWN) as caught:
            call()
        assert "'nope'" in str(caught.value), surface


def test_a_request_field_written_into_the_property_map_is_refused() -> None:
    """Each of these is a parameter of `fetch`, so in the map it changes nothing."""
    for key, value in (
        ("attrs", ["name"]),
        ("attr_names", ["name"]),
        ("context", "be brief"),
        ("max_records", 1),
    ):
        client = ScriptedClient([TWO_RECORDS])
        with pytest.raises(ConfigError, match=UNKNOWN):
            LlmSource(client=client).fetch("who?", properties={key: value})
        assert client.prompts == []


def test_the_data_layer_surfaces_still_read_the_request_fields_they_are_handed() -> None:
    """`optional_properties` is where those surfaces receive a request, not only a config.

    Refusing them there would break the one path that has nowhere else to put them.
    """
    by_name = ScriptedClient([TWO_RECORDS])
    assert LlmSource(client=by_name).execute_query(
        "who?", optional_properties={"attr_names": ["name"], "max_records": 1}
    ) == [{"name": "Ada"}]

    by_attrs = ScriptedClient([TWO_RECORDS])
    assert LlmSource(client=by_attrs).execute_query_nl(
        "who?", optional_properties={"attrs": ["name"], "context": "be brief"}
    ) == [{"name": "Ada"}, {"name": "Grace"}]
    assert "be brief" in by_attrs.prompts[0]


def test_a_property_key_that_is_not_a_string_is_refused_by_name() -> None:
    """Every check below it reads a key as a string, and a bare AttributeError names none."""
    # Cast because the annotation already says `str`; the caller under test is the one who
    # is not reading it, which is the only way this key arrives.
    mistyped = cast("dict[str, Any]", {1: 2})
    with pytest.raises(ConfigError, match="property keys must be strings") as caught:
        LlmSource(client=ScriptedClient([ONE_RECORD])).fetch("who?", properties=mistyped)

    message = str(caught.value)
    assert "1 (int)" in message, "the refusal has to name the key and what it is"


def test_the_keys_the_library_reads_without_a_namespace_are_accepted() -> None:
    """`budget` and `usage_sink` are the channels a fetch writes into the map it passes down.

    An injected client constructed with either carries it back through the resolved map, so
    a set that did not hold them would refuse the library's own vocabulary.
    """
    client = ScriptedClient([ONE_RECORD])
    source = LlmSource(
        properties={
            "connection": {"variant": "openai"},
            "cache_store": None,
            C.BUDGET_PROPERTY: Budget(Limits()),
            C.USAGE_SINK_PROPERTY: [],
        },
        client=client,
    )

    assert source.fetch("who?").records == [{"name": "Ada", "age": 36}]


# ------------------------------------------------------------- the tool path --
SERVED_TOOLS = ["list_databases", "list_collections", "get_schema", "execute_query"]

# What a call to each advertised tool looks like. Keyed by name so a tool advertised without
# an entry here fails the tests below rather than going unexercised.
TOOL_INPUTS: dict[str, dict[str, Any]] = {
    "list_databases": {},
    "list_collections": {"database": C.DATABASE},
    "get_schema": {"database": C.DATABASE, "collection": C.COLLECTION},
    "execute_query": {"query": "who?"},
}


def test_the_advertised_tools_are_the_ones_this_source_can_answer() -> None:
    """The base advertises eight of them and this source answers four.

    `get_metadata`, `get_column_stats` and `get_relations` return an empty result with
    nothing said, and `get_sample` raises a bare `TypeError` out of the base dispatcher,
    which hands `row_sample_size` to a `fetch_entity_stats` that takes no such argument.
    Advertising any of those puts a tool in front of an orchestrating model that it cannot
    use and is not told why.
    """
    assert [tool["name"] for tool in LlmSource().get_nl_tools()] == SERVED_TOOLS


def test_an_advertised_schema_is_the_base_class_own() -> None:
    """Filtered, never rewritten.

    The base dispatcher is what reads the input, so a schema written here would be a second
    copy of its contract and the copy is what drifts: a key renamed or a `required` entry
    added upstream would leave this source advertising a call the dispatcher then refuses.
    """
    base_tools = cast(
        "list[dict[str, Any]]",
        DataSource.get_nl_tools(LlmSource()),  # pyright: ignore[reportUnknownMemberType]
    )
    upstream = {tool["name"]: tool for tool in base_tools}

    for tool in LlmSource().get_nl_tools():
        assert tool == upstream[tool["name"]], "an advertised schema has drifted from upstream"

    # Called out because the tool path routes a call by them, and a filter that dropped them
    # would leave an orchestrator no way to say which database it means.
    by_name = {tool["name"]: tool for tool in LlmSource().get_nl_tools()}
    assert set(by_name["get_schema"]["input_schema"]["properties"]) == {"database", "collection"}


def test_every_advertised_tool_returns_something_a_caller_can_use() -> None:
    """Answering with silence is the failure the filter exists to prevent."""
    source = LlmSource(client=ScriptedClient([TWO_RECORDS]))
    answers = {
        str(tool["name"]): source.execute_nl_tool(tool["name"], TOOL_INPUTS[tool["name"]])
        for tool in source.get_nl_tools()
    }

    assert answers["list_databases"] == [C.DATABASE]
    assert answers["list_collections"] == [C.COLLECTION]
    assert answers["get_schema"]["entities"], "a schema with no entities in it is silence"
    assert answers["execute_query"] == [{"name": "Ada"}, {"name": "Grace"}]
    assert all(answers.values()), "every advertised tool has to answer with something"


def test_a_tool_this_source_does_not_advertise_is_refused_by_name() -> None:
    """And the refusal names what a caller can reach instead.

    The caller here is a model that sees only the message text, so a refusal that does not
    say what is available leaves it guessing at its next move.
    """
    source = LlmSource()

    for name in ("get_sample", "get_metadata", "get_column_stats", "get_relations", "invented"):
        with pytest.raises(ConfigError, match="is not a tool this source serves"):
            source.execute_nl_tool(name, {"entity": "x"})

    with pytest.raises(ConfigError, match="list_databases"):
        source.execute_nl_tool("get_sample", {"entity": "x"})


def test_no_tool_call_escapes_the_documented_hierarchy() -> None:
    """`except LlmSourceError` is the promise, and the base dispatcher does not keep it.

    Left to the base, an unknown name arrives as `ValueError`, a missing required key as
    `KeyError`, and a `tool_input` that is not a mapping as `AttributeError`. None of those
    is catchable by a caller written against this library's documented errors.
    """
    source = LlmSource(client=ScriptedClient([TWO_RECORDS]))
    calls: list[Callable[[], Any]] = [
        lambda: source.execute_nl_tool("no_such_tool", {}),
        lambda: source.execute_nl_tool("execute_query", {}),
        lambda: source.execute_nl_tool("execute_query", {"query": 7}),
        lambda: source.execute_nl_tool("execute_query", cast("dict[str, Any]", ["query"])),
        lambda: source.execute_nl_tool(cast("str", {"execute_query": 1}), {}),
        lambda: source.execute_query_nl_tools("who?"),
    ]

    for call in calls:
        with pytest.raises(LlmSourceError):
            call()

    # The missing key is named, rather than reaching the caller as a dictionary lookup.
    with pytest.raises(QueryError, match="execute_query requires query"):
        source.execute_nl_tool("execute_query", {})


def test_the_tool_driven_query_loop_is_still_refused() -> None:
    """The one tool method that stays refused, and for spend rather than shape.

    It drives a loop of up to ten provider calls that this source's ceilings never see, so
    serving it would put an unbounded spend path behind a method nobody here chose to offer.
    """
    with pytest.raises(ConfigError, match="tool-driven query path"):
        LlmSource().execute_query_nl_tools("who?")


def test_a_ceiling_supplied_with_a_tool_call_binds_the_tool_that_spends() -> None:
    """`execute_query` is the one advertised tool that reaches a provider.

    It takes its ceilings where the query surface takes them — `limits.*` in
    `optional_properties` — and the base dispatcher hands them on. A tool that spends with no
    reachable ceiling is a tool an orchestrator can run until the bill arrives.
    """
    refused = ScriptedClient([TWO_RECORDS], cost=0.02)
    stopped = LlmSource(client=refused).execute_nl_tool(
        "execute_query", {"query": "who?"}, optional_properties={"limits.max_cost": 0.000001}
    )
    assert stopped == []
    assert refused.prompts == [], "a spend ceiling has to bind before the provider is contacted"

    allowed = ScriptedClient([TWO_RECORDS], cost=0.02)
    assert LlmSource(client=allowed).execute_nl_tool(
        "execute_query", {"query": "who?"}, optional_properties={"limits.max_cost": 1.0}
    ) == [{"name": "Ada"}, {"name": "Grace"}]

    capped = ScriptedClient([TWO_RECORDS])
    assert LlmSource(client=capped).execute_nl_tool(
        "execute_query", {"query": "who?"}, optional_properties={"limits.max_rows": 1}
    ) == [{"name": "Ada"}]


def test_a_misspelled_ceiling_is_refused_on_every_tool_not_only_the_one_that_spends() -> None:
    """A key accepted on one call and refused on the next is a ceiling a caller believes in."""
    source = LlmSource(client=ScriptedClient([TWO_RECORDS]))

    for name, tool_input in TOOL_INPUTS.items():
        with pytest.raises(ConfigError, match="unknown limit"):
            source.execute_nl_tool(name, tool_input, optional_properties={"limits.max_cst": 1})


def _empty(_prompt: str) -> str:
    return "[]"


class TestSynchronousEntryInsideAnEventLoop:
    """`fetch` refuses inside a running loop, and the refusal is catchable as ours.

    The documented guarantee is that nothing but ``LlmSourceError`` escapes ``fetch``,
    ``afetch`` or ``estimate``. A bare ``RuntimeError`` here broke that for the one case a
    caller is most likely to hit while porting sync code into an async service, and no test
    covered it, which is why it survived.
    """

    def test_the_refusal_is_part_of_the_error_hierarchy(self) -> None:
        async def call_it() -> Exception | None:
            try:
                LlmSource().fetch("anything", llm_fn=_empty)
            except Exception as exc:  # noqa: BLE001 — the type is what is under test
                return exc
            return None

        raised = asyncio.run(call_it())
        assert raised is not None, "fetch must refuse inside a running loop"
        assert isinstance(raised, LlmSourceError)
        assert "afetch" in str(raised), "the message must name the async form to use"

    def test_it_does_not_refuse_outside_a_loop(self) -> None:
        result = LlmSource().fetch("anything", llm_fn=_empty)
        assert result.records == []


def test_a_source_rebuilt_by_the_registry_still_answers() -> None:
    """`register_source` records `_class` beside the properties and `connect_source` reads
    it back, so a rebuilt source arrives carrying a key this package never set. The closed
    property map has to admit it or the registry round trip fails for every source."""
    from blue_data.registry.data_registry import (  # pyright: ignore[reportMissingTypeStubs]
        DataRegistry,
    )

    registry = DataRegistry()  # pyright: ignore[reportUnknownVariableType]
    registry.register_source(LlmSource(properties={"llm.model": "gpt-4o"}))
    rebuilt = cast("LlmSource", registry.connect_source("llm"))

    # The regression is the refusal, not the answer: the rebuilt source carries a key this
    # package never set, and a closed map that does not admit it ends the round trip here.
    def answer(prompt: str, **_kw: Any) -> str:
        return '{"records":[{"name":"CDG"}]}'

    rebuilt.execute_query_nl(
        "two airports",
        llm_fn=answer,
        optional_properties={"attrs": ["name"]},
    )


# ------------------------------------------------- defaults across a rebuild --
def _rebuilt(source: LlmSource) -> LlmSource:
    """*source* recorded by the data registry and rebuilt from that record."""
    from blue_data.registry.data_registry import (  # pyright: ignore[reportMissingTypeStubs]
        DataRegistry,
    )

    registry = DataRegistry()  # pyright: ignore[reportUnknownVariableType]
    registry.register_source(source)
    del registry.sources[source.name]  # pyright: ignore[reportUnknownMemberType]
    return cast("LlmSource", registry.connect_source(source.name))


def _requests(
    stub_install: Callable[[LitellmStub], LitellmStub],
    source: LlmSource,
    properties: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """What the default client sends the provider for one fetch, less the prompt."""
    stub = stub_install(LitellmStub(chat_payload(ONE_RECORD)))
    source.fetch("who?", properties=properties)
    assert len(stub.calls) == 1
    return {k: v for k, v in stub.calls[0].items() if k not in {"messages", "timeout"}}


@pytest.mark.parametrize(
    "configured",
    [
        {},
        {"llm.model": "gpt-4o-mini", "llm.max_tokens": 512},
        {"llm.model": "gpt-4o-mini"},
        {"llm.model": "gpt-4o"},
        {"llm.model": "gpt-6-luna"},
        {"llm.model": "gpt-6-sol"},
        {"llm.model": "gpt-6-sol", "llm.reasoning_effort": "high"},
        {"llm.model": "gpt-5.4", "llm.reasoning_effort": "medium"},
        {"llm.model": "gpt-4o", "llm.max_tokens": 25_000},
        {"llm.model": "house-model"},
    ],
    ids=lambda configured: ",".join(f"{k}={v}" for k, v in configured.items()) or "defaults",
)
def test_a_source_rebuilt_from_its_record_sends_the_same_request(
    litellm_stub: Callable[[LitellmStub], LitellmStub], configured: dict[str, Any]
) -> None:
    """The registry records `properties` and rebuilds from them as caller settings.

    The default model's effort and allowance were stored there, so the rebuilt source sent
    them as deliberate choices: `gpt-4o-mini` was refused for a reasoning effort nobody set,
    and a `gpt-4o` source rebuilt without an allowance asked for the reasoning allowance.
    A setting the caller did make, even one equal to a default, has to survive.
    """
    original = _requests(litellm_stub, LlmSource(properties=dict(configured)))
    rebuilt = _requests(litellm_stub, _rebuilt(LlmSource(properties=dict(configured))))
    assert rebuilt == original


@pytest.mark.parametrize("model", ["gpt-4o", "gpt-4o-mini", "gpt-5.4", "house-model"])
def test_a_default_source_takes_another_models_defaults_when_the_model_changes(
    litellm_stub: Callable[[LitellmStub], LitellmStub], model: str
) -> None:
    fresh = _requests(litellm_stub, LlmSource(properties={"llm.model": model}))

    per_call = _requests(litellm_stub, LlmSource(), {"llm.model": model})
    after_rebuild = _requests(litellm_stub, _rebuilt(LlmSource()), {"llm.model": model})
    reassigned = LlmSource()
    reassigned.properties["llm.model"] = model
    set_directly = _requests(litellm_stub, reassigned)

    assert per_call == fresh
    assert after_rebuild == fresh
    assert set_directly == fresh


def test_the_rebuilt_record_does_not_carry_the_model_dependent_defaults() -> None:
    rebuilt = _rebuilt(LlmSource(properties={"llm.model": "gpt-4o-mini", "llm.max_tokens": 512}))
    assert rebuilt.properties["llm.model"] == "gpt-4o-mini"
    assert rebuilt.properties["llm.max_tokens"] == 512
    assert "llm.reasoning_effort" not in rebuilt.properties
    assert rebuilt.max_tokens_for_model("gpt-6-luna") == 512


def _from_properties(source: LlmSource, client: ScriptedClient) -> LlmSource:
    return LlmSource(properties=dict(source.properties), client=client)


def _through_the_registry(source: LlmSource, client: ScriptedClient) -> LlmSource:
    return LlmSource(properties=dict(_rebuilt(source).properties), client=client)


@pytest.mark.parametrize(
    "rebuild", [_from_properties, _through_the_registry], ids=["from-properties", "registry"]
)
def test_a_rebuilt_source_still_defers_to_its_injected_clients_configuration(
    rebuild: Callable[[LlmSource, ScriptedClient], LlmSource],
) -> None:
    """The default model and stream flag outranked the client's own once read back."""
    configured = ScriptedClient([ONE_RECORD, ONE_RECORD])
    configured.properties = {"llm.model": "gpt-6-sol", "llm.stream": True}  # type: ignore[attr-defined]

    LlmSource(client=configured).fetch("who?")
    rebuild(LlmSource(client=configured), configured).fetch("who?")

    fresh, rebuilt = configured.seen_properties
    assert fresh is not None
    assert rebuilt is not None
    for key in ("llm.model", "llm.stream", "llm.reasoning_effort", "llm.max_tokens"):
        assert rebuilt[key] == fresh[key], key
    assert rebuilt["llm.model"] == "gpt-6-sol"
    assert rebuilt["llm.stream"] is True


# ------------------------------------------------- a repaired answer says so --
def test_grouped_numbers_are_read_and_the_repair_is_reported() -> None:
    from blue_data_llm_source import meta_view

    answer = '[{"parish": "Alpha", "population": 4,470}, {"parish": "Beta", "population": 12}]'
    result = LlmSource(client=ScriptedClient([answer])).fetch(
        "parishes", attrs=["parish", "population"]
    )
    assert [record["population"] for record in result.records] == [4470, 12]
    assert result.meta.repaired_numbers == 1
    assert meta_view(result.meta)["repaired_numbers"] == 1


def test_a_clean_answer_reports_no_repair() -> None:
    from blue_data_llm_source import meta_view

    result = LlmSource(client=ScriptedClient([ONE_RECORD])).fetch("who?")
    assert result.meta.repaired_numbers == 0
    assert "repaired_numbers" not in meta_view(result.meta)
