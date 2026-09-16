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
from tests.doubles import ScriptedClient

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
    LlmSource(client=client).execute_query("who?", optional_properties={"llm.temperature": 0.0})
    assert client.seen_properties[0] is not None
    assert client.seen_properties[0]["llm.temperature"] == 0.0


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
    """A raw AttributeError says what broke; this says what to implement."""
    with pytest.raises(ConfigError, match=r"complete\(prompt, properties\)"):
        LlmSource(client=object()).fetch("who?")


def test_a_client_whose_signature_cannot_take_properties_is_refused() -> None:
    """properties carries the run's budget and usage sink, so omitting it cannot be bounded."""

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
    """Counting parameters accepted these; the source cannot call either of them.

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
    """A permissive signature satisfies the contract; the check must not over-reach."""

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
    """The closed set lived in the built-in client, so every other path skipped it."""

    def stub(prompt: str) -> str:
        return "[]"

    with pytest.raises(ConfigError, match="unknown adapter override"):
        LlmSource(properties={"llm.temprature": 0.1}).fetch("who?", llm_fn=stub)

    with pytest.raises(ConfigError, match="unknown adapter override"):
        LlmSource(properties={"llm.temprature": 0.1}).estimate("who?")


def test_a_callable_reports_its_accounting_as_unknown() -> None:
    """The library says it cannot know a callable's provider work, then must not invent it.

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
    """The flag must distinguish, not simply always disclaim."""
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
        ({"llm.rate_limit": 10}, "deferred to v2.0"),
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
