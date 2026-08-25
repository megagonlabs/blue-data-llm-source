"""A streamed call, which has to end up in the same place a non-streamed one does.

Streaming buys compatibility and nothing else: a fetch parses one whole JSON document into
records, so every chunk has to arrive before anything can be answered. What these assert is
that taking the slower route changes nothing a caller can observe — the same records, the
same pricing, the same reservation — and that an unpriceable stream is reported as unpriced
rather than as free.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

import pytest

from blue_data_llm_source import LlmSource
from blue_data_llm_source.llm.budget import Budget, Limits
from blue_data_llm_source.llm.client import DefaultLlmClient
from blue_data_llm_source.types import TransportError
from tests.doubles import LitellmStub, chat_payload, stream_chunks

InstallStub = Callable[[LitellmStub], LitellmStub]

ANSWER = '[{"name": "Ada"}, {"name": "Grace"}]'
BILLED = {"prompt_tokens": 11, "completion_tokens": 7}


def test_a_streamed_call_returns_what_the_same_unstreamed_call_would(
    litellm_stub: InstallStub,
) -> None:
    litellm_stub(LitellmStub(chat_payload(ANSWER), chunks=stream_chunks(ANSWER)))

    plain = DefaultLlmClient({"llm.stream": False}).complete("who?")
    streamed = DefaultLlmClient({"llm.stream": True}).complete("who?")

    assert streamed == plain == ANSWER


def test_a_streamed_fetch_produces_the_same_records_as_an_unstreamed_one(
    litellm_stub: InstallStub,
) -> None:
    """The whole pipeline, not just the client: parsing, alignment and row counting."""
    litellm_stub(LitellmStub(chat_payload(ANSWER), chunks=stream_chunks(ANSWER)))

    plain = LlmSource(properties={"llm.stream": False}).fetch("names", attrs=["name"])
    streamed = LlmSource(properties={"llm.stream": True}).fetch("names", attrs=["name"])

    assert streamed.records == plain.records == [{"name": "Ada"}, {"name": "Grace"}]
    assert streamed.meta.outcome == plain.meta.outcome


def test_the_whole_stream_is_read_before_anything_is_assembled(
    litellm_stub: InstallStub,
) -> None:
    """A partial read would silently truncate the document the parser is handed."""
    chunks = stream_chunks(ANSWER, size=3)
    stub = litellm_stub(LitellmStub(chunks=chunks))

    DefaultLlmClient({"llm.stream": True}).complete("who?")

    assert len(chunks) > 1, "the split produced one chunk, so this asserts nothing"
    assert stub.assembled == [chunks]


def test_a_stream_that_carries_usage_is_priced_like_any_other_call(
    litellm_stub: InstallStub,
) -> None:
    litellm_stub(
        LitellmStub(chunks=stream_chunks(ANSWER, usage=BILLED), cost=0.125),
    )
    budget = Budget()
    client = DefaultLlmClient({"llm.stream": True}, budget=budget)

    client.complete("who?")

    assert client.last_usage is not None
    assert client.last_usage.cost_known is True
    assert client.last_usage.cost == pytest.approx(0.125)
    assert client.last_usage.prompt_tokens == 11
    assert client.last_usage.completion_tokens == 7
    assert budget.cost == pytest.approx(0.125)


def test_a_stream_with_no_usage_keeps_its_reservation_and_reports_it_unknown(
    litellm_stub: InstallStub,
) -> None:
    """The one answer this package has for a cost it cannot read.

    A stream that sends no figures still reached the provider and was still billed. The
    assembler fills a usage block in from a local tokenizer, so a price is always available
    to whoever wants one — and it is not the price that was charged. The bound reserved for
    the attempt stands as the charge instead, which is what a ceiling can act on.
    """
    litellm_stub(LitellmStub(chunks=stream_chunks(ANSWER), cost=0.125))
    budget = Budget(Limits(max_cost=1.0))
    client = DefaultLlmClient(
        {"llm.stream": True, "llm.model": "gpt-4o-mini", "llm.max_tokens": 512},
        budget=budget,
    )

    client.complete("who?")

    assert client.last_usage is not None
    assert client.last_usage.cost_known is False
    assert client.last_usage.cost != pytest.approx(0.125), "the stub's price was settled anyway"
    assert client.last_usage.cost > 0.0
    assert budget.cost == client.last_usage.cost
    # No tokens either. The counts on the assembled response came from a tokenizer here, not
    # from the provider, and reporting them as accounting is the same mistake as the price.
    assert client.last_usage.prompt_tokens == 0
    assert client.last_usage.completion_tokens == 0


def test_an_unpriced_stream_never_settles_as_free(litellm_stub: InstallStub) -> None:
    """A ceiling that admits every attempt while the total stays at zero is not a ceiling."""
    litellm_stub(LitellmStub(chunks=stream_chunks(ANSWER)))
    budget = Budget(Limits(max_cost=1.0))
    client = DefaultLlmClient(
        {"llm.stream": True, "llm.model": "gpt-4o-mini", "llm.max_tokens": 512},
        budget=budget,
    )

    client.complete("who?")

    assert budget.cost > 0.0


def test_a_streamed_fetch_reports_its_accounting_as_unknown(litellm_stub: InstallStub) -> None:
    """What a caller reading the result sees, rather than what the client recorded."""
    litellm_stub(LitellmStub(chunks=stream_chunks(ANSWER)))

    result = LlmSource(properties={"llm.stream": True}).fetch("names", attrs=["name"])

    assert result.records == [{"name": "Ada"}, {"name": "Grace"}]
    assert result.meta.accounting_known is False


def test_a_streamed_fetch_that_was_priced_reports_its_accounting_as_known(
    litellm_stub: InstallStub,
) -> None:
    litellm_stub(LitellmStub(chunks=stream_chunks(ANSWER, usage=BILLED), cost=0.125))

    result = LlmSource(properties={"llm.stream": True}).fetch("names", attrs=["name"])

    assert result.meta.accounting_known is True
    assert result.meta.cost == pytest.approx(0.125)


# ------------------------------------------------------------------------ async --
async def test_the_async_path_streams_too(litellm_stub: InstallStub) -> None:
    litellm_stub(LitellmStub(chunks=stream_chunks(ANSWER)))
    assert await DefaultLlmClient({"llm.stream": True}).acomplete("who?") == ANSWER


async def test_the_async_path_prices_a_stream_that_carries_usage(
    litellm_stub: InstallStub,
) -> None:
    litellm_stub(LitellmStub(chunks=stream_chunks(ANSWER, usage=BILLED), cost=0.125))
    budget = Budget()
    client = DefaultLlmClient({"llm.stream": True}, budget=budget)

    await client.acomplete("who?")

    assert client.last_usage is not None
    assert client.last_usage.cost_known is True
    assert budget.cost == pytest.approx(0.125)


async def test_the_async_path_keeps_the_reservation_for_a_stream_with_no_usage(
    litellm_stub: InstallStub,
) -> None:
    """The two entry points are separate code, so each is asked the accounting question."""
    litellm_stub(LitellmStub(chunks=stream_chunks(ANSWER), cost=0.125))
    budget = Budget(Limits(max_cost=1.0))
    client = DefaultLlmClient(
        {"llm.stream": True, "llm.model": "gpt-4o-mini", "llm.max_tokens": 512},
        budget=budget,
    )

    await client.acomplete("who?")

    assert client.last_usage is not None
    assert client.last_usage.cost_known is False
    assert budget.cost == client.last_usage.cost
    assert budget.cost > 0.0


async def test_an_async_stream_is_read_through_its_async_iterator(
    litellm_stub: InstallStub,
) -> None:
    """`acompletion` hands back an async iterator; draining it synchronously would raise."""
    chunks = stream_chunks(ANSWER, size=3)
    stub = litellm_stub(LitellmStub(chunks=chunks))

    await DefaultLlmClient({"llm.stream": True}).acomplete("who?")

    assert stub.assembled == [chunks]


async def test_a_streamed_afetch_produces_the_same_records(litellm_stub: InstallStub) -> None:
    litellm_stub(LitellmStub(chat_payload(ANSWER), chunks=stream_chunks(ANSWER)))

    plain = await LlmSource(properties={"llm.stream": False}).afetch("names", attrs=["name"])
    streamed = await LlmSource(properties={"llm.stream": True}).afetch("names", attrs=["name"])

    assert streamed.records == plain.records == [{"name": "Ada"}, {"name": "Grace"}]


# --------------------------------------------------------------------- malformed --
def test_a_stream_that_carried_nothing_is_a_transport_error(litellm_stub: InstallStub) -> None:
    litellm_stub(LitellmStub(chunks=[]))

    with pytest.raises(TransportError, match="empty stream"):
        DefaultLlmClient({"llm.stream": True}).complete("who?")


def test_a_stream_of_unreadable_chunks_is_a_transport_error(litellm_stub: InstallStub) -> None:
    litellm_stub(LitellmStub(chunks=["not a chunk"]))

    with pytest.raises(TransportError, match="could not be assembled"):
        DefaultLlmClient({"llm.stream": True}).complete("who?")


def test_a_provider_that_ignored_the_flag_is_a_transport_error(
    litellm_stub: InstallStub,
) -> None:
    """Not iterable, so there is no stream to read. Named rather than raised as a TypeError."""
    litellm_stub(LitellmStub(_NotAStream()))

    with pytest.raises(TransportError, match="did not return a stream"):
        DefaultLlmClient({"llm.stream": True}).complete("who?")


def test_an_assembled_response_faces_the_same_shape_checks_as_a_plain_one(
    litellm_stub: InstallStub,
) -> None:
    """Assembling a stream must not become a way past the response-shape guard.

    Those checks run inside the guarded attempt so a transient anomaly is retried rather
    than settled, cached and then raised outside the loop. A stream arriving by a different
    route does not earn a different answer.
    """
    stub = LitellmStub(chunks=stream_chunks(ANSWER))
    stub.stream_chunk_builder = _assembles_without_choices
    litellm_stub(stub)

    with pytest.raises(TransportError, match="no choices list"):
        DefaultLlmClient({"llm.stream": True}, budget=Budget(Limits(max_retries=0))).complete("q")


async def test_a_malformed_async_stream_also_raises_a_transport_error(
    litellm_stub: InstallStub,
) -> None:
    litellm_stub(LitellmStub(chunks=["not a chunk"]))

    with pytest.raises(TransportError, match="could not be assembled"):
        await DefaultLlmClient({"llm.stream": True}).acomplete("who?")


def test_a_failed_streamed_attempt_is_retried_and_charged(litellm_stub: InstallStub) -> None:
    """A stream that fails partway is a transport failure like any other, not a free call."""
    stub = litellm_stub(
        LitellmStub(chunks=stream_chunks(ANSWER, usage=BILLED), cost=0.125, fail_times=1)
    )
    budget = Budget(Limits(max_retries=1))
    client = DefaultLlmClient({"llm.stream": True}, budget=budget)

    client.complete("who?")

    assert len(stub.calls) == 2
    assert budget.calls == 2
    assert [usage.cost_known for usage in client.usages] == [False, True]


def test_the_stream_flag_is_not_smuggled_past_an_injected_client(
    litellm_stub: InstallStub,
) -> None:
    """A client's own properties cross the boundary, so its stream flag reaches the call."""
    stub = litellm_stub(LitellmStub(chunks=stream_chunks(ANSWER)))
    source = LlmSource(client=DefaultLlmClient(properties={"llm.stream": True}))

    source.fetch("names", attrs=["name"])

    assert stub.calls[0]["stream"] is True


def test_asyncio_run_reuse_still_streams(litellm_stub: InstallStub) -> None:
    litellm_stub(LitellmStub(chunks=stream_chunks(ANSWER)))
    client = DefaultLlmClient({"llm.stream": True})

    for _ in range(2):
        assert asyncio.run(client.acomplete("who?")) == ANSWER


def test_a_streamed_answer_is_cached_like_any_other(litellm_stub: InstallStub, cache: Any) -> None:
    stub = litellm_stub(LitellmStub(chunks=stream_chunks(ANSWER, usage=BILLED)))
    client = DefaultLlmClient({"llm.stream": True, "cache_store": cache})

    first = client.complete("who?")
    second = client.complete("who?")

    assert first == second == ANSWER
    assert len(stub.calls) == 1
    assert client.last_usage is not None
    assert client.last_usage.cache_hit is True


class _NotAStream:
    """What a provider hands back when it answers a stream request with one response."""

    def __repr__(self) -> str:
        return "<one response>"


def _assembles_without_choices(chunks: list[Any], **kwargs: Any) -> dict[str, Any]:
    return {"id": "stub-completion", "model": "stub-model"}


@pytest.mark.parametrize(
    ("label", "block"),
    [
        ("empty", {}),
        ("all zeros", {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}),
    ],
)
def test_a_usage_block_with_no_counts_is_not_provider_accounting(
    litellm_stub: InstallStub, label: str, block: dict[str, int]
) -> None:
    """A container is not a report, and the difference is a figure charged to a caller.

    A provider can stream a usage block that carries nothing. The assembler then fills the
    counts in from a local tokenizer, so a check that asks whether usage is *present* accepts
    an invented figure as though the provider had sent it. Only counts that are actually
    there make a stream priceable.
    """
    litellm_stub(LitellmStub(chunks=stream_chunks(ANSWER, usage=block), cost=0.125))
    budget = Budget(Limits(max_cost=1.0))
    client = DefaultLlmClient(
        {"llm.stream": True, "llm.model": "gpt-4o-mini", "llm.max_tokens": 512},
        budget=budget,
    )

    client.complete("who?")

    assert client.last_usage is not None
    assert client.last_usage.cost_known is False, f"{label} usage was read as accounting"
    assert client.last_usage.cost != pytest.approx(0.125), "a fabricated price was settled"
    assert client.last_usage.cost > 0.0, "the attempt settled as free"
    assert client.last_usage.prompt_tokens == 0, "locally counted tokens were published"
    assert client.last_usage.completion_tokens == 0
    assert budget.cost == client.last_usage.cost
