"""The reservation bound. Wrong high is released; wrong low crosses the ceiling."""

from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path
from typing import Any

import pytest

from blue_data_llm_source.llm import pricing
from blue_data_llm_source.types import ConfigError


def test_a_known_model_uses_its_own_rates() -> None:
    cheap = pricing.bound_cost("x" * 300, "gpt-4o-mini", 100)
    dear = pricing.bound_cost("x" * 300, "gpt-4", 100)
    assert dear > cheap


def test_a_dated_variant_resolves_to_its_family() -> None:
    assert pricing.rates_for("gpt-4o-mini-2024-07-18") == pricing.PRICES["gpt-4o-mini"]


def test_an_unlisted_model_is_refused_rather_than_guessed() -> None:
    """A figure invented for an unknown price is not a bound, and a ceiling on it is a lie."""
    with pytest.raises(ConfigError, match="no published prices"):
        pricing.bound_cost("x", "some-model-released-next-year", 100)


def test_caller_supplied_rates_bound_a_model_the_table_does_not_carry() -> None:
    rates: dict[str, object] = {
        "llm.price_per_input_token": 1e-6,
        "llm.price_per_output_token": 2e-6,
    }
    assert pricing.bound_cost("x" * 30, "brand-new-model", 100, rates) > 0


@pytest.mark.parametrize(
    "supplied",
    [
        {"llm.price_per_input_token": 1e-6},
        {"llm.price_per_output_token": 2e-6},
        {"llm.price_per_input_token": "cheap", "llm.price_per_output_token": "dear"},
        {"llm.price_per_input_token": 0, "llm.price_per_output_token": 0},
        {"llm.price_per_input_token": float("nan"), "llm.price_per_output_token": 1e-6},
    ],
)
def test_an_override_that_cannot_be_read_is_refused_rather_than_ignored(
    supplied: dict[str, object],
) -> None:
    """Presence decides that the caller meant to override, not whether it parsed.

    Falling back to the table when both values were unusable accepted an explicit override
    and ignored it, which against a stale table makes the claimed bound too low.
    """
    with pytest.raises(ConfigError, match="both be set"):
        pricing.bound_cost("x", "gpt-4o-mini", 100, supplied)


def test_an_oversized_integer_names_its_field_rather_than_overflowing() -> None:
    with pytest.raises(ConfigError, match="llm.max_tokens"):
        pricing.bound_cost("x", "gpt-4o-mini", 10**400)


def test_a_longer_name_is_a_different_model_not_a_variant() -> None:
    """An open prefix match under-priced a dearer model as a cheaper one it extends."""
    assert pricing.rates_for("gpt-4o-miniature-premium") is None
    assert pricing.rates_for("o1-pro") is None
    assert pricing.rates_for("gpt-4o-mini-2024-07-18") == pricing.PRICES["gpt-4o-mini"]


def test_the_bound_counts_bytes_because_tokens_can_outnumber_characters() -> None:
    """The premise the bound rests on, and the one that had to be corrected.

    A tokenizer with no merge for a character emits several tokens for it, so characters do
    not bound tokens. Bytes do: every token consumes at least one byte of the encoding.
    """
    ascii_prompt = "a" * 40
    emoji_prompt = "\U0001f469\u200d\U0001f469\u200d\U0001f467" * 6

    assert len(emoji_prompt) < len(ascii_prompt), "fewer characters"
    assert len(emoji_prompt.encode("utf-8")) > len(ascii_prompt.encode("utf-8")), "more bytes"
    # Fewer characters, far more bytes, and the bound must follow the bytes.
    assert pricing.bound_cost(emoji_prompt, "gpt-4o-mini", 1) > pricing.bound_cost(
        ascii_prompt, "gpt-4o-mini", 1
    )


@pytest.mark.parametrize("cap", [None, 0, -5, float("nan"), float("inf"), True, "4096"])
def test_an_output_cap_that_is_not_a_finite_positive_number_is_refused(cap: object) -> None:
    """Without a cap on the output, no figure bounds what the call can cost."""
    with pytest.raises(ConfigError, match="llm.max_tokens"):
        pricing.bound_cost("x", "gpt-4o-mini", cap)


def test_the_bound_is_never_below_a_real_token_count() -> None:
    """The property everything else rests on: tokens never outnumber UTF-8 bytes.

    A recorded call in this suite used 188 prompt and 89 completion tokens on gpt-4o-mini.
    Any prompt producing 188 tokens encodes to at least 188 bytes, so the bound for the
    shortest such prompt must still exceed what that call cost.
    """
    input_rate, output_rate = pricing.PRICES["gpt-4o-mini"]
    actual = 188 * input_rate + 89 * output_rate
    assert pricing.bound_cost("x" * 188, "gpt-4o-mini", 4096) > actual


@pytest.mark.parametrize("text", ["a b c d e f g h", "!@#$ %^&* ()", "日本語のテキスト", ""])
def test_the_bound_holds_for_text_that_tokenizes_densely(text: str) -> None:
    """Short words and punctuation tokenize close to one token per byte.

    An estimate tuned to the three-characters-per-token average is exceeded by exactly this
    kind of input, which is why the bound counts UTF-8 bytes. Characters do not bound it: a
    tokenizer with no merge for a character emits several tokens for it, which is what the
    non-Latin case here covers.
    """
    input_rate, output_rate = pricing.PRICES["gpt-4o-mini"]
    worst_case_tokens = len(text.encode("utf-8"))
    floor = worst_case_tokens * input_rate + 10 * output_rate
    assert pricing.bound_cost(text, "gpt-4o-mini", 10) >= floor


def test_framing_is_counted_because_the_provider_adds_it() -> None:
    input_rate, _ = pricing.PRICES["gpt-4o-mini"]
    bare = pricing.bound_cost("", "gpt-4o-mini", 1)
    assert bare > pricing.FRAMING_TOKENS * input_rate * 0.9


def test_can_bound_reports_what_bound_cost_would_do() -> None:
    assert pricing.can_bound("gpt-4o-mini", 100)
    assert not pricing.can_bound("unknown-model", 100)
    assert not pricing.can_bound("gpt-4o-mini", None)


def test_caller_supplied_rates_reach_the_estimator_through_the_client() -> None:
    """The escape hatch has to be reachable, not just implemented.

    Both keys are adapter overrides, so the client's closed key set has to accept them, and
    they are control keys, so they must never be forwarded to the provider. Without both
    halves an unlisted model with a cost ceiling had no working route at all.
    """
    from blue_data_llm_source.llm.client import DefaultLlmClient

    properties: dict[str, object] = {
        "llm.model": "brand-new-model",
        "llm.max_tokens": 64,
        "llm.price_per_input_token": 1e-6,
        "llm.price_per_output_token": 2e-6,
    }
    # Accepted rather than refused as an unknown override...
    kwargs = DefaultLlmClient()._call_kwargs("who?", properties)  # pyright: ignore[reportPrivateUsage]

    # ...and consumed here rather than forwarded to the provider, which would reject them.
    assert "price_per_input_token" not in kwargs
    assert "price_per_output_token" not in kwargs
    assert kwargs["model"] == "brand-new-model"


_O200K_BLOBPATH = "https://openaipublic.blob.core.windows.net/encodings/o200k_base.tiktoken"
"""Where the encoding these tests cross-check against comes from. Never fetched here."""


def _tokenizer_data_is_cached() -> bool:
    """Whether the encoding table is already on this machine, without going to look for it.

    `tiktoken` downloads its table on first use and caches it under the temp directory. The
    check has to be made without calling `get_encoding`, because that call *is* the network
    request: the suite's offline guard both blocks it and counts it, so catching the failure
    afterwards still fails the run at teardown. This mirrors how tiktoken resolves its own
    cache directory.
    """
    # This table specifically, not any table. A machine holding a different encoding is not
    # a machine that can run this cross-check offline, and treating it as one turns the call
    # below into the network request this guard exists to avoid. tiktoken files each cached
    # blob under the SHA-1 of its URL, so the question is answerable without asking for it.
    key = hashlib.sha1(_O200K_BLOBPATH.encode()).hexdigest()
    for var in ("TIKTOKEN_CACHE_DIR", "DATA_GYM_CACHE_DIR"):
        configured = os.environ.get(var)
        if configured is not None:
            return bool(configured) and (Path(configured) / key).is_file()
    return (Path(tempfile.gettempdir(), "data-gym-cache") / key).is_file()


def _encoder() -> Any:
    """The real tokenizer, or a skip when its data is not already on this machine.

    `importorskip` covers the package being absent. It does not cover the case that actually
    happens: the package is installed and its encoding table is not, which turns these tests
    into a network call in a suite that forbids one. The cross-check is genuinely optional —
    it verifies a premise against an external artifact rather than against this code — so an
    absent table is a skip rather than a failure. Warm the cache once with network access and
    these run everywhere after.
    """
    # Not bound to a name ending in "token": the repository's credential scanner reads that
    # shape as an assigned secret, and it is right to.
    encoders = pytest.importorskip("tiktoken")
    if not _tokenizer_data_is_cached():
        pytest.skip("tokenizer encoding table is not cached locally; run once with network")
    return encoders.get_encoding("o200k_base")


@pytest.mark.parametrize(
    ("label", "text"),
    [
        ("ascii", "The member states of the European Union " * 40),
        ("emoji", "\U0001f642" * 600),
        ("zwj", "\U0001f468‍\U0001f469‍\U0001f467‍\U0001f466" * 300),
        ("cjk", "日本語のテキスト" * 300),
        ("devanagari", "नमस्ते दुनिया " * 200),
        ("json", '{"k":"v"},' * 400),
    ],
)
def test_the_byte_bound_is_never_below_a_real_tokenizer(label: str, text: str) -> None:
    """The claim the whole cost ceiling rests on, checked against an actual tokenizer.

    Skipped where the tokenizer is not installed, because it is not a dependency of this
    package. Where it is available this is the only test that can falsify the premise, and
    the premise has been wrong once already: characters do not bound tokens, bytes do.
    """
    encoder = _encoder()

    real_tokens = len(encoder.encode(text))
    bounded_tokens = len(text.encode("utf-8")) + pricing.FRAMING_TOKENS

    assert bounded_tokens >= real_tokens, (
        f"{label}: the bound must not be below what the tokenizer actually produces"
    )


def test_characters_would_not_have_been_a_bound() -> None:
    """Kept as the counter-example, so the corrected premise cannot quietly regress."""
    encoder = _encoder()

    text = "\U0001f468‍\U0001f469‍\U0001f467‍\U0001f466" * 300
    assert len(encoder.encode(text)) > len(text), "characters under-count for this input"
    assert len(encoder.encode(text)) <= len(text.encode("utf-8")), "bytes do not"
