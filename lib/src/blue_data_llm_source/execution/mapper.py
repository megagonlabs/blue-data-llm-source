"""Run one leaf of a plan: ask, then ask again for what did not come back.

A model asked for two hundred records returns forty, stops at a plausible place, and
reports nothing wrong. Nothing in the response says it is short, so the only way to find
the rest is to ask again — and the only thing that ends an ask-again loop is a cap.

That cap is what makes the loop safe and what makes its answer ambiguous. Forty records
from a model that had nothing more and forty from a loop that ran out of continuations are
the same list. :attr:`LeafRun.at_cap` says which of the two ended this one, and it is why
this returns a result object rather than records.

It says which loop ended, not whether rows are missing. A model holding exactly forty and a
model holding four hundred both answer the last permitted call the same way, so the cap
firing after a productive response means completeness was never established — not that
another record exists.

Nothing here contacts a provider. The model arrives as a callable, so the loop — which is
the part that decides how many calls a leaf makes — is exercised without one.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Generator, Sequence
from dataclasses import dataclass, field
from typing import Any, Final, cast

from blue_data_llm_source.planning.bounds import PlanLimits
from blue_data_llm_source.planning.cardinality import CardinalityEstimate
from blue_data_llm_source.planning.plan import Leaf
from blue_data_llm_source.prompts import (
    LEAF_PROMPT_VERSIONS,
    bounded_held_entries,
    build_fetch_prompt,
    build_leaf_fetch_prompt,
    with_held,
)
from blue_data_llm_source.reduction.identity import (
    KeyTuple,
    clean_key_names,
    key_identity,
    record_identity,
)
from blue_data_llm_source.reduction.parsing import read_records, salvage_read
from blue_data_llm_source.types import (
    Attr,
    BudgetExceeded,
    ConfigError,
    Outcome,
    ParseError,
)


@dataclass(frozen=True)
class Answer:
    """One model reply, with whatever the provider said about why it stopped.

    This internal driver type carries a finish reason while
    :class:`~blue_data_llm_source.types.LlmClientProtocol` still returns ``str``. A bare
    string has no reported finish reason.
    """

    text: str | None
    finish_reason: str | None = None


def _answer(sent: Any) -> Answer:
    """Whatever the driver replied, as an :class:`Answer`."""
    return sent if isinstance(sent, Answer) else Answer(cast("str | None", sent))


@dataclass(frozen=True)
class ResponseRecord:
    """What was observed about one response, kept beside the response it describes.

    Recovery and finish reason belong to the same response. :class:`LeafRun` derives its
    summary flags from these records so facts from different calls are not combined.
    """

    recovered: bool = False
    """Whether this response failed to parse whole and its complete records were kept."""

    parsed: bool = False
    """Whether the complete response parsed as a record list, including an empty list."""

    finish_reason: str | None = None
    """What the provider said about this response, or None when it said nothing usable."""


@dataclass(frozen=True)
class LeafRun:
    """What one leaf produced, and what stopped it producing more.

    ``at_cap`` is the field a caller cannot reconstruct from the rest. A short answer looks
    the same whether the model was finished or the loop was, and a caller deciding whether to
    split this leaf further, ask elsewhere, or trust the count has to be told which it was.
    """

    leaf_id: str
    records: list[dict[str, Any]] = field(default_factory=list[dict[str, Any]])
    attempts: int = 0
    """Calls made to the model, including one that raised. A failed call may still be
    billed, so a count that skipped it would not describe what the leaf spent."""

    at_cap: bool = False
    """True when the continuation cap ended the run after a response that was still adding
    records. False when the model ran out first, or the row target was met.

    This reports the loop's stopping condition; it does not establish whether the model
    had more records."""

    responses: tuple[ResponseRecord, ...] = ()
    """One entry per response this leaf received, in the order they arrived.

    The two flags below are computed from this and carry no state of their own, which is what
    keeps a fact about one response from being read as a fact about another."""

    outcome: str = Outcome.OK
    stopped_by: str | None = None
    """The ceiling this leaf crossed, when one did, as the axis a caller could raise.

    Its own field because a leaf refused by the budget crossed something real, and flattening
    that into a generic failure loses the only part of it a caller can act on. None for a
    provider call that simply raised: that is a failure, not a ceiling."""

    error: str | None = None
    """What went wrong, when something did — the model call raising, or a response that
    could not be read. None on every clean path, including one that returned no records."""

    exception: BaseException | None = None
    """The failure as it was raised, kept beside its message.

    A leaf failing beside siblings is a result, because their rows are already paid for. A
    leaf failing with no siblings is just the request failing, and the caller is owed the
    exception the contract promises rather than an empty list with a note attached. Only a
    caller holding the whole plan can tell those apart, so the exception travels with the
    leaf and the decision is made where the plan is known."""

    observed_key_count: int = 0
    marginal_key_yields: tuple[int, ...] = ()
    incomplete_key_records: int = 0
    normalization_collisions: tuple[dict[str, Any], ...] = ()
    repaired_numbers: int = 0
    """Numbers written with thousands separators that were read after removing them."""

    @property
    def recovered_prefix(self) -> bool:
        """True when complete records were kept from an unparseable response.

        The text cannot show whether the model stopped mid-record or wrote invalid JSON.
        This differs from ``at_cap``: another continuation cannot establish whether
        records were lost within that response.
        """
        return any(response.recovered for response in self.responses)

    @property
    def provider_output_limit(self) -> bool:
        """True when the provider attested that its allowance ended any response.

        A missing or unrecognized finish reason is not an attestation. A plain
        caller-provided function usually gives no reason.

        A response may parse completely and still have a provider-attested output
        limit. Its ``length`` finish reason must count even when the JSON closes.

        Lack of an attestation does not prove the response was complete."""
        return any(response.finish_reason == "length" for response in self.responses)

    @property
    def recovery_attested(self) -> bool:
        """True when the provider attested the allowance on a response that *was* recovered.

        The narrower of the two, and the one a cause sentence about a lost prefix may use. A
        reason carried by some other response explains nothing about this one, which is the
        pairing the per-response ledger exists to keep straight."""
        return any(
            response.recovered and response.finish_reason == "length" for response in self.responses
        )

    @property
    def failed(self) -> bool:
        return self.error is not None

    @property
    def answered(self) -> bool:
        """Whether at least one complete response parsed, including an empty answer."""
        return any(response.parsed for response in self.responses)

    @property
    def ordinary_failure(self) -> bool:
        """Whether execution failed for a reason other than budget admission."""
        return self.failed and not isinstance(self.exception, BudgetExceeded)

    @property
    def budget_denied(self) -> bool:
        """Whether an attempt was refused by a configured budget."""
        return isinstance(self.exception, BudgetExceeded)


_ANSWERED: Final = "answered"
_CALL_FAILED: Final = "call_failed"

LeafLoop = Generator[str, "tuple[str, Any]", LeafRun]
"""The leaf loop as a state machine: it yields a prompt and is sent what came back.

``(_ANSWERED, text)`` or ``(_CALL_FAILED, exception)``. The pair exists so a driver reports
a raising call without throwing into the generator, which would unwind the loop past the
bookkeeping that records what the attempt cost.
"""


def _leaf_loop(
    leaf: Leaf,
    *,
    attrs: list[Attr],
    limits: PlanLimits,
    primary_keys: Sequence[str],
    estimate: CardinalityEstimate | None,
    exact_rows: int | None,
    legacy_row_target: int | None,
    legacy_scalar_exclusions: bool,
    max_excluded_keys: int | None,
    max_exclusion_bytes: int | None,
    context: str = "",
    prompt_version: str | None = None,
    expose_estimate: bool = True,
    partition_keys: Sequence[str] = (),
    held: Sequence[Any] = (),
    held_total: int = 0,
) -> LeafLoop:
    """Every decision a leaf makes, with the waiting taken out.

    Shared by the synchronous and asynchronous drivers so both use the same continuation
    and stopping rules.

    Stops on the first of: a response with no record this leaf does not already hold, the
    row target being reached, or the continuation cap. Only the last of those sets
    ``at_cap``; the other two are the model or the caller saying the leaf is done.

    ``limits.empty_leaf_retry`` buys one exception to the first of those: an opening
    response holding nothing is asked once more before the leaf is closed. It is off by
    default because it doubles the call count on a leaf the model has already declined.

    A call that raised is sent back as ``_CALL_FAILED`` and becomes a failed result rather
    than an exception, because a leaf runs beside siblings that have already been paid for:
    letting one propagate discards their rows to report a failure that belongs to one of
    them.
    """
    # Default from the configuration seam rather than from a local constant, so the cap
    # that bounds a continuation prompt is the same one the cost bound is computed from.
    if max_excluded_keys is None:
        max_excluded_keys = limits.max_excluded_keys
    _check_count("max_excluded_keys", max_excluded_keys)
    if max_exclusion_bytes is None:
        max_exclusion_bytes = limits.max_exclusion_bytes
    _check_count("max_exclusion_bytes", max_exclusion_bytes)
    if exact_rows is not None:
        _check_count("exact_rows", exact_rows)
    if legacy_row_target is not None:
        _check_count("row_target", legacy_row_target)
    _check_count("held_total", held_total)
    if held_total < len(held):
        raise ConfigError("held_total must count at least the held entries passed")

    if not leaf.query.strip():
        # Refused without a call. An empty ask has no answer, and the attempt would be
        # billed for finding that out.
        return LeafRun(leaf_id=leaf.id, outcome=Outcome.EMPTY_QUERY)
    if exact_rows == 0:
        return LeafRun(leaf_id=leaf.id, outcome=Outcome.MODEL_RETURNED_NONE)

    wanted = [a.name for a in attrs]
    records: list[dict[str, Any]] = []
    # Seeded with what an earlier call already returned, when there was one: a child split
    # after a root probe is told which keys the probe holds, so it asks for the rest. Only
    # the prompt's exclusion list is seeded. A child that returns a probe key anyway keeps
    # the record, and reduction merges the two.
    seen: list[Any] = list(held)
    # Held entries the caller counted but did not pass, because the list was bounded before
    # it crossed the task boundary. The prompt's count includes them.
    unshown = held_total - len(held)
    held_keys: dict[KeyTuple, KeyTuple] = {}
    held_records: set[object] = set()
    attempts = 0
    at_cap = False
    responses: list[ResponseRecord] = []
    error: str | None = None
    failure: BaseException | None = None
    stopped_by: str | None = None
    marginal_yields: list[int] = []
    incomplete_key_records = 0
    normalization_collisions: list[dict[str, Any]] = []
    repaired_numbers = 0

    # The first ask plus its continuations, which is the per-leaf figure the worst case is
    # computed from in `bounds.execution_calls`. A loop that allowed one more would put
    # every plan built against that bound over it, by exactly the leaf count.
    allowed = 1 + limits.max_continuations
    # Held apart from the continuation allowance because it is spendable on one path only.
    # Folded into `allowed` it would become an extra continuation for every leaf, which is
    # a different setting from the one the caller enabled.
    empty_retries = 1 if limits.empty_leaf_retry else 0

    while True:
        shown = bounded_held_entries(seen, max_excluded_keys, max_exclusion_bytes)
        if prompt_version in LEAF_PROMPT_VERSIONS:
            prompt = build_leaf_fetch_prompt(
                original_query=leaf.original_query,
                retrieval_query=leaf.query,
                attrs=attrs,
                caller_context=context,
                constraints=leaf.constraints,
                primary_keys=primary_keys,
                estimate=(estimate.to_dict() if expose_estimate and estimate is not None else None),
                prompt_version=prompt_version,
                partition_keys=partition_keys,
            )
            prompt = with_held(prompt, shown, len(seen) + unshown)
        else:
            prompt = build_fetch_prompt(
                leaf.query,
                attrs,
                caller_context=context,
                constraints=leaf.constraints,
                held=shown,
                held_total=len(seen) + unshown,
                primary_keys=primary_keys,
                estimate=(estimate.to_dict() if expose_estimate and estimate is not None else None),
            )
        attempts += 1
        kind, sent = yield prompt
        if kind == _CALL_FAILED:
            error = f"the model call failed: {type(sent).__name__}: {sent}"
            failure = sent
            # A refusal names the ceiling it crossed. Every other failure names none, because
            # there was none: `stopped_by` is the axis a caller can raise, and a provider that
            # simply raised leaves nothing to raise.
            if isinstance(sent, BudgetExceeded):
                stopped_by = sent.limit
            break
        reply = _answer(sent)
        answer = reply.text
        recovered = False
        try:
            returned, repaired = read_records(answer, wanted)
        except ParseError as e:
            try:
                # A response that ends mid-document is not a failure of the whole attempt:
                # the records above the cut are complete, and the call that produced them
                # was billed whether they are read or thrown away.
                returned, repaired = salvage_read(answer, wanted)
            except ParseError:
                failure = e
                # Reported, not read as "the model has nothing". A response that cannot be
                # parsed is a leaf whose row count means nothing, and silently ending the
                # loop there would hand back a short answer with `ok` on it.
                #
                # The first error, not the salvage attempt's. Salvage always fails on a
                # response that was merely malformed, and its message would replace the one
                # saying what was actually wrong with the document.
                error = f"the model response could not be read: {e.message}"
                # Recorded before the break. The ledger says one entry per response and this
                # is one: it arrived, it was billed, and whatever the provider said about it
                # is the only evidence anyone has for why it could not be read.
                responses.append(
                    ResponseRecord(
                        recovered=False,
                        parsed=False,
                        finish_reason=reply.finish_reason,
                    )
                )
                break
            recovered = True
        repaired_numbers += repaired

        # Filed against the response it describes, not accumulated into a flag about the
        # leaf. Read for evidence and never for reassurance: "length" is the provider saying
        # its allowance fired, while "stop", an unrecognized word, and silence are the same
        # non-answer, because the provider layer defaults a reason it does not recognize to
        # "stop" and believing that turns an unrecognized truncation into a claim of
        # completeness. Which response carried it matters as much as what it said — a reason
        # on an answer that parsed whole explains nothing about one that did not.
        responses.append(
            ResponseRecord(
                recovered=recovered,
                parsed=not recovered,
                finish_reason=reply.finish_reason,
            )
        )

        fresh = 0
        progress = 0
        for record in returned:
            identity = key_identity(record, primary_keys, attrs)
            if identity is None:
                marker = record_identity(record)
                if marker in held_records:
                    continue
                held_records.add(marker)
                incomplete_key_records += 1 if primary_keys else 0
                seen.append(record)
                records.append(record)
                progress += 1
                if not primary_keys:
                    fresh += 1
                continue
            prior = held_keys.get(identity)
            if prior is not None:
                if prior.display != identity.display:
                    normalization_collisions.append(
                        {
                            "primary_keys": list(primary_keys),
                            "kept": prior.exclusion(),
                            "collided": identity.exclusion(),
                        }
                    )
                if legacy_scalar_exclusions:
                    continue
                # Keep complete duplicates until reduction, where field conflicts and every
                # contributing leaf are accounted for. They do not expand the exclusion set
                # or count as a new key for continuation.
                records.append(record)
                continue
            held_keys[identity] = identity
            seen.append(identity.display[0] if legacy_scalar_exclusions else identity.exclusion())
            records.append(record)
            fresh += 1
            progress += 1

        marginal_yields.append(fresh)
        if legacy_row_target is not None and len(records) >= legacy_row_target:
            break
        observed = len(held_keys) if primary_keys else len(held_records)
        if exact_rows is not None and observed >= exact_rows:
            break
        new_key_contract = bool(primary_keys) and not legacy_scalar_exclusions
        if (new_key_contract and fresh == 0) or (not new_key_contract and progress == 0):
            if empty_retries and not records:
                # One second ask, and only where the leaf holds nothing at all, which can
                # only be the opening response: any later one follows an attempt that
                # produced something. `bounds.execution_calls` reserves this call as soon
                # as the caller enables it, so a leaf that never makes it leaves a knob
                # that is paid for and changes nothing anyone can observe.
                empty_retries -= 1
                continue
            break
        if attempts >= allowed:
            # The first call is the whole run when continuations are disabled. Only an
            # enabled continuation cap can mark the result as stopped by a limit.
            at_cap = limits.max_continuations > 0
            break

    return LeafRun(
        leaf_id=leaf.id,
        records=records,
        attempts=attempts,
        at_cap=at_cap,
        responses=tuple(responses),
        outcome=_outcome(
            records,
            at_cap=at_cap,
            recovered_prefix=any(response.recovered for response in responses),
            error=error,
        ),
        stopped_by=stopped_by,
        error=error,
        exception=failure,
        observed_key_count=len(held_keys) if primary_keys else len(held_records),
        marginal_key_yields=tuple(marginal_yields),
        incomplete_key_records=incomplete_key_records,
        normalization_collisions=tuple(normalization_collisions),
        repaired_numbers=repaired_numbers,
    )


def run_leaf(
    leaf: Leaf,
    ask: Callable[[str], str | None | Answer],
    *,
    attrs: list[Attr],
    limits: PlanLimits,
    key: str | None = None,
    row_target: int | None = None,
    max_excluded_keys: int | None = None,
    primary_keys: Sequence[str] = (),
    estimate: CardinalityEstimate | None = None,
    exact_rows: int | None = None,
    max_exclusion_bytes: int | None = None,
    context: str = "",
    prompt_version: str | None = None,
    expose_estimate: bool = True,
    partition_keys: Sequence[str] = (),
    held: Sequence[Any] = (),
    held_total: int = 0,
) -> LeafRun:
    """Ask for this leaf and keep asking for what is missing, until something stops it.

    *ask* is the whole model boundary. See :func:`_leaf_loop` for what stops the loop and
    what each stopping condition means for the result.
    """
    keys = _resolve_keys(primary_keys, key)
    loop = _leaf_loop(
        leaf,
        attrs=attrs,
        limits=limits,
        primary_keys=keys,
        estimate=estimate or leaf.cardinality,
        exact_rows=exact_rows,
        legacy_row_target=row_target,
        legacy_scalar_exclusions=bool(key is not None and not primary_keys),
        max_excluded_keys=max_excluded_keys,
        max_exclusion_bytes=max_exclusion_bytes,
        context=context,
        prompt_version=prompt_version,
        expose_estimate=expose_estimate,
        partition_keys=partition_keys,
        held=held,
        held_total=held_total,
    )
    reply: tuple[str, Any] | None = None
    while True:
        try:
            prompt = next(loop) if reply is None else loop.send(reply)
        except StopIteration as stop:
            return cast("LeafRun", stop.value)
        try:
            reply = (_ANSWERED, ask(prompt))
        except Exception as e:
            # BaseException is deliberately not caught. A cancellation or an interrupt is
            # not a leaf failing, and converting one into a result the caller reads as data
            # would keep the run going after it was told to stop.
            reply = (_CALL_FAILED, e)


async def arun_leaf(
    leaf: Leaf,
    ask: Callable[[str], Awaitable[str | None | Answer]],
    *,
    attrs: list[Attr],
    limits: PlanLimits,
    key: str | None = None,
    row_target: int | None = None,
    max_excluded_keys: int | None = None,
    primary_keys: Sequence[str] = (),
    estimate: CardinalityEstimate | None = None,
    exact_rows: int | None = None,
    max_exclusion_bytes: int | None = None,
    context: str = "",
    prompt_version: str | None = None,
    expose_estimate: bool = True,
    partition_keys: Sequence[str] = (),
    held: Sequence[Any] = (),
    held_total: int = 0,
) -> LeafRun:
    """:func:`run_leaf` against an awaitable model boundary.

    The async client can cancel a wait when ``max_seconds`` expires; the synchronous
    entry point cannot.
    """
    keys = _resolve_keys(primary_keys, key)
    loop = _leaf_loop(
        leaf,
        attrs=attrs,
        limits=limits,
        primary_keys=keys,
        estimate=estimate or leaf.cardinality,
        exact_rows=exact_rows,
        legacy_row_target=row_target,
        legacy_scalar_exclusions=bool(key is not None and not primary_keys),
        max_excluded_keys=max_excluded_keys,
        max_exclusion_bytes=max_exclusion_bytes,
        context=context,
        prompt_version=prompt_version,
        expose_estimate=expose_estimate,
        partition_keys=partition_keys,
        held=held,
        held_total=held_total,
    )
    reply: tuple[str, Any] | None = None
    while True:
        try:
            prompt = next(loop) if reply is None else loop.send(reply)
        except StopIteration as stop:
            return cast("LeafRun", stop.value)
        try:
            reply = (_ANSWERED, await ask(prompt))
        except Exception as e:
            reply = (_CALL_FAILED, e)


def _outcome(
    records: list[dict[str, Any]], *, at_cap: bool, recovered_prefix: bool, error: str | None
) -> str:
    """Which member describes this run, in precedence order.

    A run that was cut short outranks whatever it produced: a caller cannot trust the shape
    of an answer whose loop ended before the model did, and ``ok`` on forty rows out of an
    unknown number is the reading that hides it.

    A recovered prefix counts as cut short even if later continuations finish. They cannot
    establish whether rows were lost inside the unparseable response; the gap records that
    uncertainty.

    ``Outcome`` has no member for a failed call, so a failure reports the same cut-short
    member and ``error`` says which of the two happened. What must not happen is a failure
    reading as ``model_returned_none``, which is a claim about the world rather than about
    this run.
    """
    if error is not None or at_cap or recovered_prefix:
        return Outcome.STOPPED_BY_LIMIT
    if not records:
        return Outcome.MODEL_RETURNED_NONE
    return Outcome.OK


def _resolve_keys(primary_keys: Sequence[str], key: str | None) -> tuple[str, ...]:
    keys = clean_key_names(primary_keys)
    if key is not None:
        alias = clean_key_names((key,))[0]
        if keys and keys != (alias,):
            raise ConfigError("key and primary_keys disagree")
        keys = (alias,)
    if len(set(keys)) != len(keys):
        raise ConfigError("primary_keys must be distinct non-empty strings")
    return keys


def _check_count(name: str, value: object) -> None:
    """Refuse a count that cannot bound anything, when it is set rather than when it is read.

    ``bool`` is rejected by name because it is a subtype of ``int``: ``max_excluded_keys=True``
    type-checks and would quietly list exactly one record.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"{name} must be an integer, got {value!r}")
    if value < 0:
        raise ConfigError(f"{name} must not be negative, got {value!r}")
