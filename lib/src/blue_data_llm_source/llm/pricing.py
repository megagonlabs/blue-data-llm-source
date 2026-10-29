"""Upper-bound a model call's cost before admitting it.

Byte-pair tokens consume at least one UTF-8 byte each, so
``len(prompt.encode("utf-8"))`` bounds input tokens. Character count does not:
one character may require several tokens. This byte bound is loose for English
but cannot underestimate token count.

:func:`bound_cost` requires known prices and a finite output cap. Published
prices are a snapshot in USD per token. For an unlisted model, callers may
provide ``llm.price_per_input_token`` and ``llm.price_per_output_token``;
these rates must cover every billed tier. For a listed model, supplied base
rates still include the table's cache-write, long-context, and regional
charges, and the bound never falls below the table's own calculation.
"""

from __future__ import annotations

import math

from blue_data_llm_source.llm import models
from blue_data_llm_source.types import ConfigError

# Chat framing the provider adds around the prompt: role labels, separators, and the
# per-message overhead. Generous, because it is added to a bound and not to an estimate.
FRAMING_TOKENS = 64

# USD per token, (input, output), for every model the table carries. Derived from the one
# model table so the rates a ceiling is bounded against and the request contract cannot drift.
PRICES: dict[str, tuple[float, float]] = {
    name: (entry.input, entry.output) for name, entry in models.MODELS.items()
}


def rates_for(model: str) -> tuple[float, float] | None:
    """Published input and output price per token, or None for a model not in the table.

    Exact names and dated variants only. Refusing an unrecognized name is what makes the
    caller-supplied rates worth having; guessing from a prefix is how a dearer model gets
    priced as a cheaper one whose name it happens to extend.
    """
    base = models.base_name(model)
    return PRICES[base] if base is not None else None


def priceable_models() -> tuple[str, ...]:
    """The model names this package carries a published price for, sorted.

    A tuple rather than the table itself: ``PRICES`` is a mutable snapshot of
    time-sensitive list prices, and handing it out as a public surface would make a
    contract this package cannot keep across a price change. What a caller needs is the
    names it can bound a ceiling against, which is this.
    """
    return tuple(sorted(PRICES))


def positive_finite(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except OverflowError:
        # An integer too large for a float is not a usable ceiling, and letting the raw
        # OverflowError out would break the promise that every bad value names its field.
        return None
    if not math.isfinite(number) or number <= 0:
        return None
    return number


def resolve_rates(model: str, properties: dict[str, object] | None = None) -> tuple[float, float]:
    """The prices to bound *model* against, from the caller or the table.

    Caller-supplied rates win, so a model this table has never heard of can still be
    bounded by someone who knows what it costs.
    """
    supplied = properties or {}
    raw_in = supplied.get("llm.price_per_input_token")
    raw_out = supplied.get("llm.price_per_output_token")
    # Any supplied rate selects the override path. Invalid rates cannot fall back to the
    # table because that could understate the bound.
    if raw_in is not None or raw_out is not None:
        given_in = positive_finite(raw_in)
        given_out = positive_finite(raw_out)
        if given_in is None or given_out is None:
            raise ConfigError(
                "llm.price_per_input_token and llm.price_per_output_token must both be set "
                "to finite positive numbers, since a bound needs both halves and a rate that "
                "cannot be read is not a rate"
            )
        return given_in, given_out
    published = rates_for(model)
    if published is None:
        raise ConfigError(
            f"no published prices for model {model!r}, so a cost ceiling cannot be bounded "
            f"before the call. Set llm.price_per_input_token and llm.price_per_output_token, "
            f"or drop limits.max_cost and bound the run on calls, time or rows instead"
        )
    return published


def bound_cost(
    prompt: str,
    model: str,
    max_tokens: object,
    properties: dict[str, object] | None = None,
) -> float:
    """The most one call on these arguments can cost. Raises when that cannot be established.

    Raising is the point. A figure invented for a model whose price is unknown, or for a
    request whose output is uncapped, is not a bound, and a ceiling claimed against it is a
    ceiling that gets crossed.
    """
    output_cap = positive_finite(max_tokens)
    if output_cap is None:
        raise ConfigError(
            "limits.max_cost needs a finite positive llm.max_tokens: without a cap on the "
            "output, no figure bounds what the call can cost"
        )
    input_rate, output_rate = resolve_rates(model, properties)
    # Bytes, not characters: a byte-pair tokenizer consumes at least one byte per token, and
    # can emit more tokens than the prompt has characters when it has no merge for them.
    prompt_bytes = len(prompt.encode("utf-8"))
    prompt_tokens = prompt_bytes + FRAMING_TOKENS
    entry = models.entry_for(model)
    if entry is None:
        # Rates the caller supplied for a model the table does not carry are used as given.
        # Nothing here knows that model's cache, tier or regional charges, so the rates have
        # to cover every tier it can be billed at already.
        return prompt_tokens * input_rate + output_cap * output_rate
    # Supplied rates for a known model are taken as its base rates. The table's cache-write
    # ratio, long-context tier and regional uplift still apply to them, and they can raise
    # the bound but never lower it below the table's own: a base rate alone omits charges
    # the model is known to carry.
    return max(
        _entry_bound(entry, entry.input, entry.output, prompt_tokens, output_cap),
        _entry_bound(entry, input_rate, output_rate, prompt_tokens, output_cap),
    )


def _entry_bound(
    entry: models.ModelEntry,
    input_rate: float,
    output_rate: float,
    prompt_tokens: int,
    output_cap: float,
) -> float:
    """One call's bound at these base rates, with *entry*'s billing modifiers applied."""
    # Settlement conservatively prices every uncached token at the cache-write rate and
    # applies the long-context multipliers whenever the model carries that tier. Admission
    # must use the same worst case, including for a short prompt.
    if entry.cache_write is not None:
        input_rate *= max(1.0, entry.cache_write / entry.input)
    tier = entry.long_context
    if tier is not None:
        input_rate *= tier.input_multiplier
        output_rate *= tier.output_multiplier
    return (prompt_tokens * input_rate + output_cap * output_rate) * entry.regional_uplift


def can_bound(model: str, max_tokens: object, properties: dict[str, object] | None = None) -> bool:
    """Whether a cost ceiling could be claimed for these arguments."""
    try:
        bound_cost("", model, max_tokens, properties)
    except ConfigError:
        return False
    return True
