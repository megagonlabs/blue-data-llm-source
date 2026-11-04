"""Whether a partitioned request should split at all, decided from one root answer.

Under ``plan.split_policy="always"`` the root of a partitioned plan is split whatever it
holds. Many requests are answered whole by one call, and
splitting those asks each child a narrower question and merges back less than the root would
have returned. Under ``"evidence"`` the root leaf is asked once first, as a probe, and the
plan splits only when that answer shows the one call did not hold the request: the provider
said its output allowance ended the response, the answer ended mid-document and only its
complete records could be read, the answer holds fewer than half the keys an estimate above
``plan.split_threshold_rows`` expects, or the probe failed outright. Otherwise the probe's
answer is the result.

The probe is one ask of the normal root leaf, with no continuation and no empty-answer
retry, so it is exactly one logical call. The published bounds in
:mod:`~blue_data_llm_source.planning.bounds` charge it in every accessor.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final, Protocol

from blue_data_llm_source.planning.bounds import SPLIT_POLICY_ALWAYS, SPLIT_POLICY_EVIDENCE
from blue_data_llm_source.planning.cardinality import CardinalityEstimate
from blue_data_llm_source.prompts import bounded_held_entries
from blue_data_llm_source.reduction.identity import key_identity
from blue_data_llm_source.types import Attr

SPLIT_ALWAYS: Final = SPLIT_POLICY_ALWAYS
SPLIT_EVIDENCE: Final = SPLIT_POLICY_EVIDENCE
SPLIT_POLICIES: Final = (SPLIT_ALWAYS, SPLIT_EVIDENCE)
"""The split policies, the first being the default."""

DEFAULT_SPLIT_THRESHOLD_ROWS: Final = 120
"""The estimate above which a short probe answer is read as a shortfall rather than the whole.

Below it, a probe that returned few rows is taken at its word: the request was estimated
small, and a split would ask many narrow questions to find rows the estimate never claimed."""

PROBE_ACCEPTED: Final = "probe_accepted"
PROBE_SPLIT_LENGTH: Final = "probe_split_length"
PROBE_SPLIT_INCOMPLETE: Final = "probe_split_incomplete"
PROBE_SPLIT_SHORTFALL: Final = "probe_split_shortfall"
PROBE_SPLIT_FAILED: Final = "probe_split_failed"
SPLIT_DECISIONS: Final = (
    PROBE_ACCEPTED,
    PROBE_SPLIT_LENGTH,
    PROBE_SPLIT_INCOMPLETE,
    PROBE_SPLIT_SHORTFALL,
    PROBE_SPLIT_FAILED,
)
"""What a probe decided, published as ``meta.split_decision``."""

PROBE_ORIGIN: Final = "root"
"""The origin the probe's records carry when the plan split after it."""


class ProbeRun(Protocol):
    """What the gate reads from the probe's leaf run."""

    @property
    def records(self) -> list[dict[str, Any]]: ...

    @property
    def observed_key_count(self) -> int: ...

    @property
    def provider_output_limit(self) -> bool: ...

    @property
    def recovered_prefix(self) -> bool: ...

    @property
    def ordinary_failure(self) -> bool: ...

    @property
    def budget_denied(self) -> bool: ...

    @property
    def exception(self) -> BaseException | None: ...


Prober = Callable[[Any, Any], Awaitable[Any]]
"""Runs the root leaf once, given the leaf and the effective schema, and returns its run."""


def decide(
    run: ProbeRun,
    estimate: CardinalityEstimate | None,
    threshold_rows: int,
    exact_rows: int | None = None,
) -> str:
    """The split decision for one probe answer.

    A probe that failed outright, for a reason other than the budget, gave no evidence, so
    the plan splits as it would have without a probe. A caller's trusted *exact_rows* of zero
    asks nothing and is accepted without a call. Otherwise the probe splits on the provider's
    attestation that the output allowance ended the response, or on a response that ended
    mid-document with only its complete records read. Only then does a nonzero *exact_rows*
    the probe met accept it: a cut answer that happens to hold the target count still lost
    whatever followed the cut. Last, it splits when a positive estimate above
    *threshold_rows* expects at least twice the keys the probe returned.
    """
    if run.ordinary_failure:
        return PROBE_SPLIT_FAILED
    if exact_rows == 0:
        return PROBE_ACCEPTED
    if run.provider_output_limit:
        return PROBE_SPLIT_LENGTH
    if run.recovered_prefix:
        # The parser saw the cut even though the provider gave no reason: the same answer
        # asked again would stop in the same place.
        return PROBE_SPLIT_INCOMPLETE
    if exact_rows is not None and run.observed_key_count >= exact_rows:
        return PROBE_ACCEPTED
    expected = estimate.expected if estimate is not None else None
    if expected is not None and expected > threshold_rows and run.observed_key_count * 2 < expected:
        return PROBE_SPLIT_SHORTFALL
    return PROBE_ACCEPTED


def held_from_records(
    records: Sequence[Mapping[str, Any]],
    primary_keys: Sequence[str],
    attrs: Sequence[Attr],
    *,
    max_excluded_keys: int,
    max_exclusion_bytes: int,
) -> tuple[tuple[Any, ...], int]:
    """The probe's keys as a child's held list, bounded, and how many there were in all.

    One complete named key object per distinct key, bounded by the same two exclusion
    settings, keeping the most recent entries. Only key values cross into another prompt: a
    record with no complete key, or any record when no key is established, is not listed.
    It could not be excluded by key anyway, and sending it whole would copy every field the
    model returned into every child's prompt. Reduction merges whatever overlap follows.
    """
    entries: list[Any] = []
    seen: set[object] = set()
    for record in records:
        identity = key_identity(record, primary_keys, attrs) if primary_keys else None
        if identity is None or identity in seen:
            continue
        seen.add(identity)
        entries.append(identity.exclusion())
    shown = bounded_held_entries(entries, max_excluded_keys, max_exclusion_bytes)
    return tuple(shown), len(entries)


@dataclass(frozen=True)
class ProbeOutcome:
    """The probe's run, what it decided, and the held list it hands the children."""

    run: Any
    decision: str
    held: tuple[Any, ...] = field(default_factory=tuple[Any, ...])
    held_total: int = 0

    @property
    def split(self) -> bool:
        return self.decision != PROBE_ACCEPTED
