"""Merge partition results and report coverage across their leaves.

The coverage account records truncated rows, leaves that did not run, and conflicting
values for a record. It is produced for every run, including a one-leaf run that can
stop at its cap. Records are merged only on a key identified by
:mod:`~blue_data_llm_source.planning.schema`; without one, the records remain separate
and the result carries a note. Verification may still remove exact whole-record
duplicates.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Final

from blue_data_llm_source.planning.plan import ROOT, FetchPlan, Gap, Note
from blue_data_llm_source.planning.schema import Schema, deduplication_runs
from blue_data_llm_source.reduction.aligner import normalized_key
from blue_data_llm_source.reduction.identity import KeyTuple, key_identity
from blue_data_llm_source.types import Outcome

FAILED: Final = "failed"
"""A leaf that raised instead of answering.

:class:`~blue_data_llm_source.types.Outcome` carries no member for this. Every case it
names describes a call that came back with something, down to "the model says there are
none". Reporting a leaf that raised as one of those either invents an answer it never
gave or invents a ceiling that never fired, so the aggregate needs its own name for
nothing at all coming back.
"""


@dataclass
class LeafOutcome:
    """What one leaf did, as execution reports it.

    ``ran`` and ``at_cap`` are separate from ``outcome`` because they answer questions the
    outcome cannot. A leaf execution never reached has no outcome to report, and a leaf
    that stopped at a cap returned a complete-looking list that is not complete.
    """

    leaf_id: str
    outcome: str = Outcome.OK
    rows: int = 0
    calls: int = 0
    answered: bool = False
    """Whether at least one complete response parsed, including an empty answer."""

    failed: bool = False
    """Whether ordinary execution work failed."""

    unserved: bool = False
    """Whether budget admission denied requested execution work."""

    stop_detail: str | None = None
    """What the ceiling that stopped this leaf said, when one did: the figure it refused and
    the setting that lowers it. None when nothing a caller set stopped the leaf."""

    at_cap: bool = False
    """True when the continuation cap ended the leaf after a response that was still adding
    records it had not returned before.

    Not a row quota. A caller reading it as one raises its row target, asks again, and gets
    the same answer, because what fired was the ceiling on how many times the leaf may ask.

    Not a claim that more records exist either. The last permitted response looks identical
    whether the model was nearly done or barely started, so this says completeness was not
    established and stops there."""

    recovered_prefix: bool = False
    """True when a response in this leaf did not parse whole and its complete records were kept.

    Its own field for the same reason ``at_cap`` is one. Both say the answer is short, and
    they say it about different things: a caller past its continuation cap can raise that
    cap, while one whose answers end mid-document needs a larger output allowance or a plan
    that asks for less at a time.

    What it does not say is why the document ended. That is
    :attr:`provider_output_limit`, and only a provider can supply it."""

    provider_output_limit: bool = False
    """True when the provider attested that its allowance ended any response in this leaf.

    The run-level axis. It does not require the response to have failed to parse: a document
    that closed does not negate the provider's word that generation was cut short."""

    recovery_attested: bool = False
    """True when the attestation was carried by a response that was itself recovered.

    The narrower fact, and the only one a sentence about a lost prefix may cite. A reason from
    some other response explains nothing about this one."""

    ran: bool = True
    """Whether execution processed this leaf, not whether a provider was contacted.

    The two come apart: a leaf refused for a blank query is processed, reports
    ``EMPTY_QUERY``, and makes no call. ``calls`` is the field that says whether a provider
    was contacted. An adapter that sets ``ran=False`` because none was files that leaf with
    the ones execution never reached, and the reason it was refused stops being
    recoverable."""


LeafInput = tuple[str, list[dict[str, Any]], LeafOutcome]
"""One leaf's contribution: its id, the records it returned, and what it did."""


@dataclass
class Coverage:
    """What a run covered and what it did not.

    Returned for every run rather than only for runs that went wrong, since "nothing is
    missing" is itself a claim a caller needs stated rather than assumed from an absent
    report.
    """

    plan: FetchPlan
    outcomes: list[LeafOutcome] = field(default_factory=list[LeafOutcome])
    """One entry per leaf that reported, in plan order. A plan leaf that reported nothing
    is accounted for by a ``LEAF_NOT_RUN`` gap instead, so this stays a record of what
    execution observed rather than a mixture of observation and inference."""

    gaps: list[Note] = field(default_factory=list[Note])
    """Planning's own notes followed by the shortfalls of this run."""

    record_origins: list[list[str]] = field(default_factory=list[list[str]])
    """Which leaves produced each returned record, positionally parallel to the records.

    A list per record because overlapping leaves can each return the same record and all
    of them are true. Filled after deduplication: filled before, the positions would index
    rows that no longer exist, and attribution that lines up against the wrong rows is
    worse than none, since it looks right."""

    @property
    def run_outcome(self) -> str:
        """One outcome for the whole run, in precedence order.

        The order is decided by which facts have somewhere else to live. A cap that fired
        has ``stopped_by``; a row trim has ``truncated_to``; a part that never came back has
        nowhere but this field. So a lost part takes it and the caps are read where they
        already are, and both facts survive in the same result.

        That does not invert the rule that a ceiling outranks what a run produced. The rule
        is about the shape of an answer — a caller cannot trust the shape of one that was cut
        short — and ``PARTIAL`` is not a claim about shape.

        ``FAILED`` is held back for the case where every leaf raised, because a run that
        answered part of the ask has an answer and the gaps say what is missing from it.
        Calling that a failure pushes a caller to discard rows it holds.
        """
        if self.plan.refused:
            # Nothing was asked, so nothing was lost. A refused plan keeps the leaves it
            # would have run and deliberately runs none of them.
            return Outcome.STOPPED_BY_LIMIT
        reported = {o.leaf_id for o in self.outcomes}
        covers_the_plan = all(leaf.id in reported for leaf in self.plan.leaves)
        answered = any(o.answered or o.rows > 0 for o in self.outcomes)
        ordinary_failures = sum(1 for o in self.outcomes if o.failed or o.outcome == FAILED)
        unserved = sum(1 for o in self.outcomes if o.unserved)
        if (
            self.outcomes
            and covers_the_plan
            and ordinary_failures == len(self.outcomes)
            and not answered
        ):
            return FAILED
        unreported = sum(1 for leaf in self.plan.leaves if leaf.id not in reported)
        lost = ordinary_failures + unserved + unreported
        # Something lost, and something observed. A run where execution reported nothing at
        # all is not partly answered — it is a run nobody has a fact about, and the
        # ``LEAF_NOT_RUN`` gaps are what carry it. Execution reports every leaf it was
        # given, so that state arrives only from a caller driving reduction directly.
        if lost and (answered or ordinary_failures or (unreported and self.outcomes)):
            return Outcome.PARTIAL
        if any(_hit_a_ceiling(o) for o in self.outcomes):
            return Outcome.STOPPED_BY_LIMIT
        return Outcome.OK


def plan_ids(plan: FetchPlan) -> set[str]:
    """Every id a note on this plan can be bound to.

    Leaf and node parents count. A flat plan holds no :class:`~.plan.Node` because nothing
    was partitioned, yet planning still binds its notes to the root the leaf hangs from;
    without the parents those notes would name an id the plan cannot resolve.
    """
    ids = {node.id for node in plan.nodes}
    ids.update(leaf.id for leaf in plan.leaves)
    ids.update(node.parent_id for node in plan.nodes if node.parent_id is not None)
    ids.update(leaf.parent_id for leaf in plan.leaves if leaf.parent_id is not None)
    return ids


def reduce_leaves(
    plan: FetchPlan,
    schema: Schema,
    results: Sequence[LeafInput],
) -> tuple[list[dict[str, Any]], Coverage]:
    """Merge what the leaves returned and account for what the run missed.

    Total. Whatever the leaves did — none ran, all raised, two disagreed about the same
    record — this returns the rows that exist and a :class:`Coverage` saying what is not
    among them. A caller who asked for a hundred rows and can have sixty needs the sixty
    and the reason, not a traceback.
    """
    gathered = _by_leaf(plan, results)
    known = plan_ids(plan)
    fallback = _plan_target(plan)

    records, origins, dedup_notes = _merge(schema, gathered, fallback)

    gaps = list(plan.notes)
    gaps.extend(_leaf_gaps(plan, gathered, known, fallback))
    gaps.extend(dedup_notes)

    coverage = Coverage(
        plan=plan,
        outcomes=[outcome for _, outcome in gathered.values()],
        gaps=gaps,
        record_origins=origins,
    )
    return records, coverage


def _by_leaf(
    plan: FetchPlan, results: Sequence[LeafInput]
) -> dict[str, tuple[list[dict[str, Any]], LeafOutcome]]:
    """Results keyed by leaf and put in plan order.

    Plan order, not the order the ids sort in. Leaf ids are dotted, so ``0.10`` precedes
    ``0.2`` as text and any plan past ten leaves would hand a key collision to the wrong
    leaf. A leaf the plan does not contain keeps its records and follows the ones it does:
    dropping rows to tidy the bookkeeping is the wrong trade.
    """
    gathered: dict[str, tuple[list[dict[str, Any]], LeafOutcome]] = {}
    for leaf_id, rows, outcome in results:
        found = gathered.get(leaf_id)
        if found is None:
            gathered[leaf_id] = (list(rows), outcome)
        else:
            found[0].extend(rows)
    rank = {leaf.id: position for position, leaf in enumerate(plan.leaves)}
    return dict(sorted(gathered.items(), key=lambda entry: rank.get(entry[0], len(rank))))


def _plan_target(plan: FetchPlan) -> str | None:
    """The id a note about the whole run binds to."""
    if ROOT in plan_ids(plan):
        return ROOT
    if plan.nodes:
        return plan.nodes[0].id
    if plan.leaves:
        return plan.leaves[0].id
    return None


def _target(leaf_id: str, known: set[str], fallback: str | None) -> str | None:
    """*leaf_id* when the plan holds it, otherwise the whole-run id.

    A note naming an id the plan does not contain cannot be resolved by anything reading
    the account, which makes it decoration rather than a report.
    """
    return leaf_id if leaf_id in known else fallback


def _hit_a_ceiling(outcome: LeafOutcome) -> bool:
    """Whether a cap bounded what this leaf returned.

    ``at_cap``, ``recovered_prefix``, ``provider_output_limit`` and ``TRUNCATED`` all describe
    an answer that stopped before the data ran out. They are separate signals and execution may
    set any of them, so a run reading only one reports a cut answer as a whole one.

    ``provider_output_limit`` is here because it is a fact about the run and not only about a
    recovered prefix. A response that closed and carried ``length`` lost nothing this library
    can see, and the provider still said generation stopped because its allowance ran out —
    reporting that run as one nothing cut short contradicts an attestation the result holds.
    """
    return (
        outcome.at_cap
        or outcome.recovered_prefix
        or outcome.provider_output_limit
        or outcome.outcome == Outcome.TRUNCATED
        or outcome.outcome == Outcome.STOPPED_BY_LIMIT
    )


def _conflicts(
    primary_keys: tuple[str, ...],
    key_value: dict[str, Any],
    kept: dict[str, Any],
    kept_sources: dict[str, str],
    dropped: dict[str, Any],
    dropped_leaf: str,
    target: str | None,
) -> list[Note]:
    """One note per field the two copies of a record disagree about.

    Only fields both copies answered. Alignment fills an unanswered attribute with None
    and a JSON null is also None, so None against a value is not evidence of disagreement;
    counting it as one buries the real conflicts under every field a leaf happened not to
    answer.

    *key_value* is the value as the record carried it, not what the two copies were filed
    under. The filing form is a tagged encoding, and a note naming a row as
    ``id=('number', 4)`` sends a reader looking for a row that is not in the result.
    """
    out: list[Note] = []
    identity_label = (
        f"{primary_keys[0]}={key_value[primary_keys[0]]!r}"
        if len(primary_keys) == 1
        else f"key={key_value!r}"
    )
    normalized_primary_keys = {normalized_key(name) for name in primary_keys}
    for name, value in kept.items():
        if normalized_key(name) in normalized_primary_keys or value is None:
            continue
        other = dropped.get(name)
        if other is None or other == value:
            continue
        out.append(
            Note(
                code=Gap.DEDUP_CONFLICT,
                target_id=target,
                detail=(
                    f"{name!r} disagrees for {identity_label}: leaf "
                    f"{kept_sources.get(name, dropped_leaf)} says {value!r} and leaf "
                    f"{dropped_leaf} says {other!r}; kept "
                    f"{kept_sources.get(name, dropped_leaf)}"
                ),
            )
        )
    return out


def _concatenated(
    gathered: dict[str, tuple[list[dict[str, Any]], LeafOutcome]],
    fallback: str | None,
) -> tuple[list[dict[str, Any]], list[list[str]], list[Note]]:
    """Every record from every leaf, in plan order, with nothing merged."""
    records: list[dict[str, Any]] = []
    origins: list[list[str]] = []
    for leaf_id, (rows, _outcome) in gathered.items():
        records.extend(rows)
        origins.extend([leaf_id] for _ in rows)
    note = Note(
        code=Gap.DEDUP_KEY_ABSENT,
        target_id=fallback,
        detail=(
            "no identifying column was established, so records from overlapping leaves "
            "are returned as they arrived"
        ),
    )
    return records, origins, [note]


def _merge(
    schema: Schema,
    gathered: dict[str, tuple[list[dict[str, Any]], LeafOutcome]],
    fallback: str | None,
) -> tuple[list[dict[str, Any]], list[list[str]], list[Note]]:
    """The records, their origins, and what merging them cost in fidelity."""
    primary_keys = schema.primary_keys
    if not deduplication_runs(schema) or not primary_keys:
        return _concatenated(gathered, fallback)

    positions: dict[KeyTuple, int] = {}
    records: list[dict[str, Any]] = []
    origins: list[list[str]] = []
    sources: list[dict[str, str]] = []
    notes: list[Note] = []
    incomplete = 0

    for leaf_id, (rows, _) in gathered.items():
        for record in rows:
            identity = key_identity(record, primary_keys, schema.attrs)
            if identity is None:
                incomplete += 1
                records.append(record)
                origins.append([leaf_id])
                sources.append(
                    {name: leaf_id for name, value in record.items() if value is not None}
                )
                continue
            at = positions.get(identity)
            if at is None:
                positions[identity] = len(records)
                records.append(record)
                origins.append([leaf_id])
                sources.append(
                    {name: leaf_id for name, value in record.items() if value is not None}
                )
                continue
            kept_identity = key_identity(records[at], primary_keys, schema.attrs)
            if kept_identity is not None and kept_identity.display != identity.display:
                notes.append(
                    Note(
                        code=Gap.DEDUP_NORMALIZATION_COLLISION,
                        target_id=fallback,
                        detail=(
                            f"normalized key collision: kept {kept_identity.exclusion()!r} "
                            f"and merged {identity.exclusion()!r}"
                        ),
                    )
                )
            if leaf_id not in origins[at]:
                origins[at].append(leaf_id)
            notes.extend(
                _conflicts(
                    primary_keys,
                    identity.exclusion(),
                    records[at],
                    sources[at],
                    record,
                    leaf_id,
                    fallback,
                )
            )
            for name, value in record.items():
                if value is not None and records[at].get(name) is None:
                    records[at][name] = value
                    sources[at][name] = leaf_id

    if incomplete:
        notes.append(
            Note(
                code=Gap.DEDUP_KEY_ABSENT,
                target_id=fallback,
                detail=(
                    f"{incomplete} record(s) lacked at least one of {list(primary_keys)!r} "
                    "and were kept unmerged"
                ),
            )
        )
    return records, origins, notes


def _gaps_for(
    leaf_id: str, rows: list[dict[str, Any]], outcome: LeafOutcome, target: str | None
) -> list[Note]:
    """Every shortfall one leaf's report shows."""
    if not outcome.ran:
        return [
            Note(
                code=Gap.LEAF_NOT_RUN,
                target_id=target,
                detail=f"leaf {leaf_id} was not processed",
            )
        ]

    if outcome.outcome == Outcome.EMPTY_QUERY:
        # Its own code because neither neighbor is true of it. This leaf was processed,
        # so it is not a `LEAF_NOT_RUN`; no model was asked, so `LEAF_EMPTY` would credit
        # one with an answer it never gave. Both readings cost the caller the same thing:
        # the leaf's range is uncovered and the fix is a query, not a retry.
        return [
            Note(
                code=Gap.LEAF_EMPTY_QUERY,
                target_id=target,
                detail=f"leaf {leaf_id} carried no query and was refused without a call",
            )
        ]

    out: list[Note] = []
    if outcome.outcome == FAILED:
        out.append(Note(code=Gap.LEAF_FAILED, target_id=target, detail=f"leaf {leaf_id} raised"))
    elif outcome.outcome == Outcome.STOPPED_BY_LIMIT:
        out.append(
            Note(
                code=Gap.LEAF_STOPPED_BY_LIMIT,
                target_id=target,
                # Not "a ceiling stopped it". The continuation cap and the model's own
                # output limit reach here too, and neither is a ceiling the caller set, so
                # naming one sent them looking through their budget for a key they never
                # crossed. The codes reported next say which of the three it was.
                detail=(
                    f"leaf {leaf_id} stopped before it finished: {outcome.stop_detail}"
                    if outcome.stop_detail
                    else f"leaf {leaf_id} stopped before it finished"
                ),
            )
        )

    # Reported before the cap codes and independently of them, because it is a fact about
    # a single answer rather than about how the loop ended. A leaf can be truncated on its
    # first response and then also run out of continuations, and a reader told only the
    # second would raise the continuation cap and watch every answer cut off in the same
    # place.
    if outcome.recovered_prefix:
        cause = (
            "the provider reported that its output allowance ended the response"
            if outcome.recovery_attested
            else "what ended it was not reported, so completeness was not established"
        )
        out.append(
            Note(
                code=Gap.LEAF_RESPONSE_TRUNCATED,
                target_id=target,
                detail=(
                    f"a response to leaf {leaf_id} ended before the document was complete; "
                    f"its {len(rows)} whole record(s) were kept, any record still being "
                    f"written was lost, and {cause}"
                ),
            )
        )

    # Which ceiling fired decides what a caller can do about it, so the detail names it.
    # One wording for both points at the row target, and a caller who raises that target
    # to get past a continuation cap asks again and gets the same rows back.
    if outcome.at_cap:
        out.append(
            Note(
                code=Gap.LEAF_AT_CAP,
                target_id=target,
                detail=(
                    f"leaf {leaf_id} returned {len(rows)} record(s) and reached its "
                    "continuation cap after an answer that was still adding records, so "
                    "completeness was not established"
                ),
            )
        )
    elif outcome.outcome == Outcome.TRUNCATED:
        out.append(
            Note(
                code=Gap.LEAF_AT_CAP,
                target_id=target,
                detail=f"leaf {leaf_id} returned {len(rows)} record(s), trimmed to the row cap",
            )
        )

    # A parsed empty answer is valid and does not by itself establish a gap. LEAF_EMPTY is
    # reserved for a reducer input that has no evidence an answer parsed.
    if (
        not rows
        and not outcome.answered
        and outcome.outcome != FAILED
        and not _hit_a_ceiling(outcome)
    ):
        detail = f"leaf {leaf_id} ran and returned no records"
        if outcome.rows:
            detail = f"leaf {leaf_id} reported {outcome.rows} row(s) and delivered no records"
        out.append(Note(code=Gap.LEAF_EMPTY, target_id=target, detail=detail))
    return out


def _leaf_gaps(
    plan: FetchPlan,
    gathered: dict[str, tuple[list[dict[str, Any]], LeafOutcome]],
    known: set[str],
    fallback: str | None,
) -> list[Note]:
    """Shortfalls for every leaf the plan holds, in plan order, then for any it does not.

    A plan leaf with no report at all is the same gap as one reported with ``ran=False``.
    Reading it only off the reports would let a leaf that execution never mentioned
    disappear from the account entirely, which is the one gap a caller cannot notice by
    looking at the rows.
    """
    out: list[Note] = []
    for leaf in plan.leaves:
        found = gathered.get(leaf.id)
        if found is None:
            out.append(
                Note(
                    code=Gap.LEAF_NOT_RUN,
                    target_id=leaf.id,
                    detail=f"no result was reported for leaf {leaf.id}",
                )
            )
            continue
        out.extend(_gaps_for(leaf.id, found[0], found[1], leaf.id))

    for leaf_id, (rows, outcome) in gathered.items():
        if leaf_id not in known:
            out.extend(_gaps_for(leaf_id, rows, outcome, _target(leaf_id, known, fallback)))
    return out
