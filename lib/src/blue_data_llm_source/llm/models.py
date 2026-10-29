"""The chat models this package knows by name: how to ask each one, and what it costs.

One table for both, so the request a call sends and the rates its cost ceiling is bounded
against always describe the same model. Rates are USD per token, copied from OpenAI's
published pricing and model pages together with each model's reasoning-effort set, its
sampling rule and its output-token parameter. They are a snapshot and go stale.

A name is matched exactly, or with a dated suffix such as ``-2026-03-05``, after an
``openai/`` prefix is removed. A longer name is a different model; an open prefix match
could apply a cheaper model's price to it.

A few older rows carry prices only. They have no request contract and no provider, so a call
to one of them is sent exactly as the caller configured it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal

# Every value the ``llm.reasoning_effort`` knob accepts, in the order the provider documents
# them. Each model accepts a subset, recorded in its entry.
EFFORT_ORDER: tuple[str, ...] = ("none", "minimal", "low", "medium", "high", "xhigh", "max")

# When a model accepts ``temperature``.
TEMPERATURE_ALWAYS = "always"
TEMPERATURE_EFFORT_NONE_ONLY = "effort_none_only"
TEMPERATURE_NEVER = "never"

# Reasoning models count hidden reasoning against the output cap, and OpenAI's guidance is to
# leave at least this much room for reasoning plus the answer.
REASONING_OUTPUT_ALLOWANCE = 25_000
# The allowance for a non-reasoning model, and for a name this table does not carry.
LEGACY_OUTPUT_ALLOWANCE = 4_096

LONG_CONTEXT_THRESHOLD = 272_000
REGIONAL_UPLIFT = 1.10


@dataclass(frozen=True)
class RequestContract:
    """How a call to one model has to be shaped."""

    reasoning_efforts: frozenset[str]
    default_reasoning_effort: str | None
    temperature: str
    output_token_parameter: str
    default_max_tokens: int
    default_temperature: float | None = None


@dataclass(frozen=True)
class LongContextTier:
    """Above ``threshold`` prompt tokens the whole request is billed at the higher rates."""

    threshold: int
    input_multiplier: float
    output_multiplier: float


@dataclass(frozen=True)
class ModelEntry:
    input: float
    output: float
    cached_input: float | None = None
    cache_write: float | None = None
    long_context: LongContextTier | None = None
    provider: str | None = None
    contract: RequestContract | None = None
    # OpenAI adds 10% for regional processing on models released on or after 2026-03-05.
    # Nothing in a response says whether it applied, so a bound on one of these models
    # assumes it did.
    regional_uplift: float = 1.0


def _per_million(usd: float) -> float:
    """*usd* per million tokens as a per-token rate.

    Divided as the decimal the pricing page prints, so the rate is the float nearest that
    decimal and reads back as, say, ``2e-07`` rather than ``2.0000000000000002e-07``.
    """
    return float(Decimal(str(usd)) / 1_000_000)


def _reasoning(efforts: tuple[str, ...], default: str, temperature: str) -> RequestContract:
    return RequestContract(
        reasoning_efforts=frozenset(efforts),
        default_reasoning_effort=default,
        temperature=temperature,
        output_token_parameter="max_completion_tokens",
        default_max_tokens=REASONING_OUTPUT_ALLOWANCE,
    )


# Temperature 0 is this package's own default for a non-reasoning model, because a fetch asks
# for facts and wants the same answer twice.
_NON_REASONING = RequestContract(
    reasoning_efforts=frozenset(),
    default_reasoning_effort=None,
    temperature=TEMPERATURE_ALWAYS,
    output_token_parameter="max_tokens",
    default_max_tokens=LEGACY_OUTPUT_ALLOWANCE,
    default_temperature=0,
)
_GPT6 = _reasoning(
    ("none", "low", "medium", "high", "xhigh", "max"), "medium", TEMPERATURE_EFFORT_NONE_ONLY
)
# Astra refuses effort ``none`` outright, so it can never be sent a temperature. OpenAI names
# no default; ``medium`` is what its siblings use.
_GPT6_ASTRA = _reasoning(("low", "medium", "high", "xhigh", "max"), "medium", TEMPERATURE_NEVER)
_GPT56 = _GPT6
_GPT55 = _reasoning(
    ("none", "low", "medium", "high", "xhigh"), "medium", TEMPERATURE_EFFORT_NONE_ONLY
)
_GPT54 = _reasoning(
    ("none", "low", "medium", "high", "xhigh"), "none", TEMPERATURE_EFFORT_NONE_ONLY
)
# GPT-5 and the o-series document no default effort and refuse sampling parameters at every
# effort. ``medium`` is the starting point OpenAI recommends.
_GPT5 = _reasoning(("minimal", "low", "medium", "high"), "medium", TEMPERATURE_NEVER)
_O_SERIES = _reasoning(("low", "medium", "high"), "medium", TEMPERATURE_NEVER)

# Doubled input and cache rates and one and a half times the output rate, for the whole request.
_LONG_CONTEXT = LongContextTier(LONG_CONTEXT_THRESHOLD, 2.0, 1.5)


def _openai(
    input_usd: float,
    output_usd: float,
    contract: RequestContract,
    *,
    cached_usd: float | None = None,
    write_usd: float | None = None,
    long_context: bool = False,
    regional: bool = False,
) -> ModelEntry:
    """One OpenAI row, prices given in USD per million tokens as the pricing page lists them."""
    return ModelEntry(
        input=_per_million(input_usd),
        output=_per_million(output_usd),
        cached_input=None if cached_usd is None else _per_million(cached_usd),
        cache_write=None if write_usd is None else _per_million(write_usd),
        long_context=_LONG_CONTEXT if long_context else None,
        provider="openai",
        contract=contract,
        regional_uplift=REGIONAL_UPLIFT if regional else 1.0,
    )


def _price_only(input_usd: float, output_usd: float) -> ModelEntry:
    return ModelEntry(input=_per_million(input_usd), output=_per_million(output_usd))


MODELS: dict[str, ModelEntry] = {
    "gpt-6-astra": _openai(
        10.00,
        50.00,
        _GPT6_ASTRA,
        cached_usd=1.00,
        write_usd=12.50,
        long_context=True,
        regional=True,
    ),
    "gpt-6-sol": _openai(
        2.00, 10.00, _GPT6, cached_usd=0.20, write_usd=2.50, long_context=True, regional=True
    ),
    "gpt-6-luna": _openai(
        0.10, 0.50, _GPT6, cached_usd=0.01, write_usd=0.125, long_context=True, regional=True
    ),
    # An alias that routes to GPT-5.6 Sol, and is billed as Sol. Sol's rates are a promotion
    # OpenAI offers at least through 2026-11-21. A later price rise leaves these rows too low,
    # which is the unsafe direction for a bound, so they need refreshing when it ends.
    "gpt-5.6": _openai(
        4.00, 20.00, _GPT56, cached_usd=0.40, write_usd=5.00, long_context=True, regional=True
    ),
    "gpt-5.6-sol": _openai(
        4.00, 20.00, _GPT56, cached_usd=0.40, write_usd=5.00, long_context=True, regional=True
    ),
    "gpt-5.6-terra": _openai(
        2.00, 12.00, _GPT56, cached_usd=0.20, write_usd=2.50, long_context=True, regional=True
    ),
    "gpt-5.6-luna": _openai(
        0.20, 1.20, _GPT56, cached_usd=0.02, write_usd=0.25, long_context=True, regional=True
    ),
    "gpt-5.5": _openai(5.00, 30.00, _GPT55, cached_usd=0.50, long_context=True, regional=True),
    "gpt-5.4": _openai(2.50, 15.00, _GPT54, cached_usd=0.25, long_context=True, regional=True),
    "gpt-5.4-mini": _openai(0.75, 4.50, _GPT54, cached_usd=0.075, regional=True),
    "gpt-5.4-nano": _openai(0.20, 1.25, _GPT54, cached_usd=0.02, regional=True),
    "gpt-5": _openai(1.25, 10.00, _GPT5, cached_usd=0.125),
    "gpt-5-mini": _openai(0.25, 2.00, _GPT5, cached_usd=0.025),
    "gpt-5-nano": _openai(0.05, 0.40, _GPT5, cached_usd=0.005),
    "gpt-4.1": _openai(2.00, 8.00, _NON_REASONING, cached_usd=0.50),
    "gpt-4.1-mini": _openai(0.40, 1.60, _NON_REASONING, cached_usd=0.10),
    "gpt-4.1-nano": _openai(0.10, 0.40, _NON_REASONING, cached_usd=0.025),
    "gpt-4o": _openai(2.50, 10.00, _NON_REASONING, cached_usd=1.25),
    # An older snapshot billed above the current gpt-4o rates. Without its own row the
    # dated-name match would price it as gpt-4o and bound it too low.
    "gpt-4o-2024-05-13": _openai(5.00, 15.00, _NON_REASONING),
    "gpt-4o-mini": _openai(0.15, 0.60, _NON_REASONING, cached_usd=0.075),
    "o3": _openai(2.00, 8.00, _O_SERIES, cached_usd=0.50),
    "o3-mini": _openai(1.10, 4.40, _O_SERIES, cached_usd=0.55),
    "o4-mini": _openai(1.10, 4.40, _O_SERIES, cached_usd=0.275),
    "o1": _openai(15.00, 60.00, _O_SERIES, cached_usd=7.50),
    "gpt-4-turbo": _price_only(10.00, 30.00),
    "gpt-4": _price_only(30.00, 60.00),
    "gpt-3.5-turbo": _price_only(0.50, 1.50),
}

_OPENAI_PREFIX = "openai/"
# A dated or regional variant of a listed model: the exact name, then a hyphen, then only
# digits and hyphens. Anything with letters after the name is a different model.
_VERSION_SUFFIX = re.compile(r"^-[\d-]+$")
# OpenAI's own naming families. A bare name in one of them is sent to OpenAI even when this
# table has not heard of it yet; any other bare name is left to the provider library.
_OPENAI_NAMING = re.compile(r"^(gpt-|chatgpt-|o\d)")


def lookup_name(model: object) -> str:
    """*model* as the table is keyed: lower case, without an ``openai/`` prefix.

    Only the lookup changes. The name sent to the provider stays as the caller wrote it.
    """
    name = str(model or "").lower()
    return name[len(_OPENAI_PREFIX) :] if name.startswith(_OPENAI_PREFIX) else name


def base_name(model: object) -> str | None:
    """The table key *model* resolves to, or None for a model the table does not carry."""
    name = lookup_name(model)
    if name in MODELS:
        return name
    # The longest listed name wins, so a dated snapshot with its own row is never priced as
    # the shorter family name it also extends.
    matches = [
        known
        for known in MODELS
        if name.startswith(known) and _VERSION_SUFFIX.match(name[len(known) :])
    ]
    return max(matches, key=len) if matches else None


def entry_for(model: object) -> ModelEntry | None:
    base = base_name(model)
    return MODELS[base] if base is not None else None


def contract_for(model: object) -> RequestContract | None:
    entry = entry_for(model)
    return entry.contract if entry is not None else None


def openai_family(model: object) -> bool:
    """Whether *model*, without any ``openai/`` prefix, is named in OpenAI's own families."""
    return _OPENAI_NAMING.match(lookup_name(model)) is not None


def pins_openai(model: object) -> bool:
    """Whether a call to *model* should name OpenAI as the provider.

    The provider library resolves a bare name from its own price map, which lags new models,
    so a current OpenAI name can fail to route before the request is built. Naming the
    provider fixes that for OpenAI's own families. A ``provider/model`` name is the caller's
    routing and is never overridden, and a bare name from another family is left alone.
    """
    name = str(model or "").lower()
    if "/" in name:
        return False
    entry = entry_for(name)
    if entry is not None:
        return entry.provider == "openai"
    return _OPENAI_NAMING.match(name) is not None


def published_defaults(model: str) -> dict[str, object] | None:
    """What the table records for *model*, or None for a model it does not carry.

    These are the model's published contract and rates. A caller's own settings, and a
    deployment's, override them for a call.
    """
    entry = entry_for(model)
    if entry is None:
        return None
    contract = entry.contract
    return {
        "reasoning_efforts": (
            None
            if contract is None
            else [e for e in EFFORT_ORDER if e in contract.reasoning_efforts]
        ),
        "default_reasoning_effort": None if contract is None else contract.default_reasoning_effort,
        "temperature": None if contract is None else contract.temperature,
        "output_token_parameter": None if contract is None else contract.output_token_parameter,
        "cached_input_price_per_token": entry.cached_input,
        "cache_write_price_per_token": entry.cache_write,
        "long_context_threshold_tokens": (
            None if entry.long_context is None else entry.long_context.threshold
        ),
    }
