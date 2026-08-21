"""Budget on five axes, reserved before spend and reconciled after it.

The implementation this client is derived from accounted usage after each call. That
records an overrun rather than preventing one, which is fine for a single call and wrong
for a fan-out: by the time the total is read, the money is gone.

Every axis is claimed before the provider is contacted, under one lock, so concurrent
callers cannot race past a ceiling. Cost is claimed the only way an unknown price can be:
an estimated upper bound is reserved before the attempt and reconciled against the real
cost afterwards, releasing the difference. The sibling operator library already does this
one layer up, estimating a semantic join's tokens and refusing above a cap before it runs.

Reserving rather than settling is what makes the two hard cases work. A cost that cannot be
read leaves its reservation standing, so an accounting failure is not a free call. And ten
concurrent leaves each commit an estimate before spending, so they cannot collectively
overshoot by ten calls' worth while each sees room for one.

A budget may be built per call or handed in by a caller, in which case it accumulates
across every call it is given to. A child from :meth:`derive` never receives a fresh
allowance: it spends what the parent has left, so a planner cannot hand out more than it
holds.
"""

from __future__ import annotations

import math
import threading
import time
from collections.abc import Callable, Generator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TypeVar

from blue_data_llm_source.types import BudgetExceeded, ConfigError

_Number = TypeVar("_Number", int, float)


@dataclass(frozen=True)
class Limits:
    """Configured ceilings. None means unlimited on that axis.

    Every field is validated on construction. A ``NaN`` ceiling compares false against
    everything, so it reads as configured and enforces nothing — a limit that is present,
    accepted, and silently off. That is the failure this package exists to prevent, so it
    is refused at the boundary rather than discovered later.
    """

    max_rows: int | None = None
    max_seconds: float | None = None
    max_cost: float | None = None
    max_calls: int | None = None
    max_retries: int = 2

    def __post_init__(self) -> None:
        for name in ("max_rows", "max_calls"):
            _require_whole(name, getattr(self, name))
        for name in ("max_seconds", "max_cost"):
            _require_real(name, getattr(self, name))
        retries = _require_whole("max_retries", self.max_retries, allow_none=False)
        if retries is None:  # pragma: no cover - _require_whole raises when None is refused
            raise ConfigError("limits.max_retries must be set")


def _require_whole(name: str, value: object, allow_none: bool = True) -> int | None:
    if value is None:
        if allow_none:
            return None
        raise ConfigError(f"limits.{name} must be a whole number, not None")
    # bool is an int in Python, and True as a ceiling of one is never what anyone meant.
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"limits.{name} must be a whole number, got {value!r}")
    if value < 0:
        raise ConfigError(f"limits.{name} must not be negative, got {value!r}")
    return value


def _require_real(name: str, value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"limits.{name} must be a number, got {value!r}")
    try:
        number = float(value)
    except OverflowError as e:
        # An int has no float range, so a large enough one raises here rather than becoming
        # inf. The package guards float() at four other sites, one of them with a comment
        # stating the rule; this was the site that did not, so a ceiling raised a bare
        # OverflowError from outside the documented hierarchy.
        raise ConfigError(f"limits.{name} is too large to be a ceiling, got {value!r}") from e
    if not math.isfinite(number):
        raise ConfigError(
            f"limits.{name} must be finite, got {value!r}. A non-finite ceiling compares "
            f"false against every spend and would be accepted while enforcing nothing"
        )
    if number < 0:
        raise ConfigError(f"limits.{name} must not be negative, got {value!r}")
    return number


class Reservation:
    """One claimed provider attempt, awaiting its real cost.

    A handle rather than a bare number so the claim resolves exactly once. A free-standing
    counter decrement carries no evidence that the attempt it undoes never happened, which
    is how a retry escape gets rebuilt by whoever calls it next.

    The handle *is* the identity of its claim, and whether it is still outstanding is read
    from the ledger under the family lock rather than from a flag on this object. A flag was
    read and written unlocked, so two threads resolving one handle could both see it unset,
    and the ledger removed claims by matching float value — which deleted a *different*
    live handle's claim whenever two attempts happened to be priced the same, and let the
    budget admit past both its call and cost ceilings.
    """

    __slots__ = ("_budget", "_estimate", "_settled_by_refusal")

    def __init__(self, budget: Budget, estimate: float) -> None:
        self._budget = budget
        self._estimate = estimate
        # Set when `settle` charged this claim while refusing the amount it was given, so a
        # caller recovering from that refusal can resolve again without being told the claim
        # is already spent. See `settle`.
        self._settled_by_refusal = False

    @property
    def estimate(self) -> float:
        return self._estimate

    @property
    def issuer(self) -> Budget:
        """The budget that issued this claim. Checked by Budget, not called by callers."""
        return self._budget

    @property
    def resolved(self) -> bool:
        return not self._budget.holds(self)

    def settle(self, actual_cost: float, cache_hit: bool = False) -> None:
        """Replace the estimate with what the attempt really cost.

        A cache hit consumes no provider attempt and no cost, including the attempt it
        claimed: nothing reached the provider, so the claim is released rather than settled
        at zero. Rows it returns are admitted separately and do still count.

        The amount is validated before the ledger is touched, because settling an unchecked
        actual corrupted it: a non-finite value passed ``max(0.0, x)`` as zero and restored
        the whole ceiling. But the claim resolves either way. An amount this refuses is
        charged at the estimate, as :meth:`keep_unpriced` would — the provider was contacted,
        and the earlier behaviour of leaving the handle outstanding let ``attempt``'s cleanup
        refund it, which turned a billed call into a free one. The handle is spent once this
        returns or raises; do not resolve it again.
        """
        if cache_hit:
            self._budget.release_reservation(self, refund_call=True)
            return
        try:
            checked = Budget.checked_amount("the settled cost", actual_cost)
        except ConfigError:
            # Validating before resolving is deliberate — settling an unchecked amount
            # corrupted the ledger — but letting the refusal out with the claim still
            # outstanding was worse. `attempt`'s cleanup then refunded the cost *and* the
            # call, so a contacted provider attempt became a free one: under max_calls=1 the
            # provider could be contacted repeatedly with the ledger still reading zero. An
            # unreadable settlement is charged at its estimate, exactly as an unreadable
            # cost is, and the refusal still reaches the caller.
            self.keep_unpriced()
            # The obvious recovery is `except: reservation.keep_unpriced(); raise`, which the
            # README's own instruction to resolve on every path out of the block encourages.
            # That second call met "already resolved" — a bare RuntimeError, outside the
            # documented hierarchy, replacing the ConfigError that says what was actually
            # wrong. The claim is settled either way, so a follow-up resolve has nothing left
            # to do and is allowed to be a no-op rather than an ambush.
            self._settled_by_refusal = True
            raise
        self._budget.settle_reservation(self, checked)

    def release(self) -> None:
        """Give back a claim for an attempt that never reached the provider.

        Correct only before the provider entry point is invoked. Once it is, the attempt
        counts whatever it returns, or every failed retry is free.

        A no-op after :meth:`settle` refused an amount: the provider had been contacted by
        then, so the claim stands as charged and giving it back would be the very refund
        that made a billed call look free.
        """
        if self._settled_by_refusal:
            return
        self._budget.release_reservation(self, refund_call=True)

    def keep_unpriced(self) -> None:
        """Resolve an attempt whose real cost could not be read.

        The estimate stands as the charge. An unreadable cost is not a free call, and
        treating it as zero is how a ceiling admits every remaining attempt while the
        running total stays where it started.

        A no-op after :meth:`settle` refused an amount, because that path already charged
        this claim at exactly this figure. Recovering from the refusal by calling this is
        the documented shape, and it should not be met with "already resolved".
        """
        if self._settled_by_refusal:
            return
        self._budget.settle_reservation(self, self._estimate)


class Budget:
    """Tracks spend against Limits and refuses the attempt that would cross one."""

    def __init__(
        self,
        limits: Limits | None = None,
        clock: Callable[[], float] = time.monotonic,
        parent: Budget | None = None,
    ) -> None:
        self.limits = limits or Limits()
        self._clock = clock
        # One lock for a whole family. A child with its own lock would make admitting and
        # claiming atomic for itself and not for its parent, so two siblings could each be
        # admitted against the same headroom and both take it. That the GIL makes the
        # window small is not a guarantee, and a fan-out is exactly where it widens.
        self._lock = parent._lock if parent is not None else threading.RLock()
        self._started = clock()
        self._parent = parent
        self.calls = 0
        self.cost = 0.0
        self.rows = 0
        # The outstanding claims themselves, not a running total. Adding each estimate and
        # subtracting it again left a rounding remnant once handles resolved out of order:
        # a residue of 1.1e-16 made an exact remaining-cost claim cross a ceiling it fitted
        # inside. The aggregate is re-derived from what is actually outstanding instead, so
        # it returns to exactly zero when nothing is.
        # Keyed by the handle, not by its amount. Removing the first equal float deleted a
        # different live handle's claim whenever two attempts were priced the same.
        # Keyed by the handle itself, so its identity is the claim's identity and there is
        # nothing to expose or to collide. Keying by amount deleted a different live handle's
        # claim whenever two attempts were priced the same; keying by ``id()`` collided
        # outright, because CPython reuses an address the moment the object at it is
        # collected. An outstanding claim keeps its handle alive, which is what it means.
        self._outstanding: dict[Reservation, float] = {}
        self.reserved = 0.0
        self.stopped_by: str | None = None
        # Advances on every claim and every resolution, so an admission can tell whether the
        # ledger moved while it was being decided. See _take.
        self._version = 0

    # ------------------------------------------------------------------ derivation --
    def derive(self, limits: Limits | None = None) -> Budget:
        """A child that spends from what this budget has left.

        Its own ceilings apply on top, never instead: the effective ceiling on every axis
        is the smaller of the child's and whatever the parent still has. A child with no
        limits of its own is bounded entirely by its parent, which is what a fan-out leaf
        should be. Without this, a planner could hand ten leaves a full budget each.
        """
        child = Budget(limits, clock=self._clock, parent=self)
        child._started = self._started
        return child

    # ----------------------------------------------------------------- observation --
    @property
    def elapsed(self) -> float:
        return self._clock() - self._started

    @property
    def committed(self) -> float:
        """Settled spend plus outstanding reservations."""
        with self._lock:
            return self.cost + self.reserved

    def remaining_cost(self) -> float | None:
        """Headroom against the cost ceiling, this budget's and every parent's.

        The figure is one admission will actually accept. ``max_cost - committed`` is the
        arithmetic answer, but ``committed + that`` can land one unit in the last place
        above ``max_cost``, so ``reserve(budget.remaining_cost())`` — the obvious way to
        spend exactly what is left — was refused for about one in a hundred small ceilings.
        Reported headroom a caller cannot claim is not headroom.
        """
        return self._narrow(
            None if self.limits.max_cost is None else self._claimable_cost(),
            lambda p: p.remaining_cost(),
        )

    def _claimable_cost(self) -> float:
        """The largest amount this budget's own cost ceiling would admit right now."""
        ceiling = self.limits.max_cost
        assert ceiling is not None  # guarded by the caller
        committed = self.committed
        room = max(0.0, ceiling - committed)
        # At most a couple of steps in practice; bounded so a pathological value cannot spin.
        for _ in range(4):
            if room <= 0.0 or committed + room <= ceiling:
                break
            room = math.nextafter(room, 0.0)
        return room

    def remaining_calls(self) -> int | None:
        return self._narrow(
            None if self.limits.max_calls is None else max(0, self.limits.max_calls - self.calls),
            lambda p: p.remaining_calls(),
        )

    def configured_axes(self) -> frozenset[str]:
        """Every axis carrying a ceiling anywhere in this chain.

        A ceiling set only on a caller's parent is still a ceiling this run has to honour,
        and reading the child's own limits alone let a parent-only ``max_cost`` reach a
        client that cannot enforce it.
        """
        mine = {
            axis
            for axis, value in (
                ("calls", self.limits.max_calls),
                ("cost", self.limits.max_cost),
                ("seconds", self.limits.max_seconds),
                ("rows", self.limits.max_rows),
            )
            if value is not None
        }
        if self._parent is not None:
            mine |= self._parent.configured_axes()
        return frozenset(mine)

    def effective_retries(self) -> int:
        """Retry depth, narrowed by every parent.

        Read from the child alone, a derived budget could spend more attempts than its
        parent authorised — the one axis that did not consult the chain.
        """
        mine = int(self.limits.max_retries)
        if self._parent is None:
            return mine
        return min(mine, self._parent.effective_retries())

    def remaining_seconds(self) -> float | None:
        """Time left, for passing to a provider as a per-call timeout."""
        return self._narrow(
            None
            if self.limits.max_seconds is None
            else max(0.0, self.limits.max_seconds - self.elapsed),
            lambda p: p.remaining_seconds(),
        )

    def _narrow(
        self, mine: _Number | None, of_parent: Callable[[Budget], _Number | None]
    ) -> _Number | None:
        """The tighter of this budget's headroom and its parents'. None means unlimited."""
        theirs = of_parent(self._parent) if self._parent is not None else None
        if mine is None:
            return theirs
        if theirs is None:
            return mine
        return min(mine, theirs)

    # ----------------------------------------------------------------- reservation --
    @staticmethod
    def checked_amount(name: str, value: object) -> float:
        """A usable amount, or a refusal naming the field. Public for Reservation's use.

        ``max(0.0, NaN)`` is ``0.0``, so a non-finite figure arriving here would consume a
        call and no money and leave a ceiling fully available. The provider adapter filters
        these, but this is the public boundary a custom client reaches.
        """
        # object, not float: this is the public boundary a custom client reaches, and an
        # annotation is not a guarantee about what actually arrives.
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ConfigError(f"{name} must be a number, got {value!r}")
        try:
            number = float(value)
        except OverflowError as e:
            raise ConfigError(f"{name} is too large to be a cost: {value!r}") from e
        if not math.isfinite(number):
            raise ConfigError(
                f"{name} must be finite, got {value!r}. A non-finite amount compares false "
                f"against every ceiling and would be recorded as costing nothing"
            )
        if number < 0:
            raise ConfigError(f"{name} must not be negative, got {value!r}")
        return number

    def reserve(self, estimated_cost: float = 0.0) -> Reservation:
        """Claim one provider attempt and an upper bound on what it will cost.

        Raises BudgetExceeded naming the axis that refused, before anything is spent. Call
        once per attempt that will reach the provider, including every retry: counting
        logical operations instead of attempts lets a run with three retries make three
        times the calls its limit allows.

        Admitting and claiming happen under one lock shared by the whole family, so no
        two budgets in a chain can be admitted against the same headroom.
        """
        return self._take(self._handle(estimated_cost))

    def _handle(self, estimated_cost: float) -> Reservation:
        """A handle for an attempt, before anything is claimed against it.

        Separate from taking the claim so :meth:`attempt` can hold the handle first. Built
        inside the claim, an interrupt landing after ``_claim`` mutated the family and before
        the return unwound with the claim taken and no handle anywhere that could give it
        back. The amount is validated here, so a bad one raises before any claim exists.
        """
        return Reservation(self, self.checked_amount("the reservation estimate", estimated_cost))

    @staticmethod
    def _own(reservation: Reservation) -> Reservation:
        """Refuse a handle this budget did not issue.

        The ledger is keyed by the handle, so it trusts that key's equality. ``Reservation``
        is exported and subclassable, and a subclass that returns a live handle's hash and
        compares equal to it could pop the real claim and free the ceiling for a replacement.
        Exact type, and the budget it names must be one this family issued it from.
        """
        if type(reservation) is not Reservation:
            raise TypeError(
                "a Reservation subclass cannot be used as a claim; obtain one from "
                "Budget.attempt or Budget.reserve"
            )
        return reservation

    def _own_here(self, reservation: Reservation) -> Reservation:
        """As :meth:`_own`, and the handle must be one this budget issued.

        Exact type alone was not enough. A handle issued by another family, passed to this
        one's private take, was claimed here and released *there*, leaving this ledger's
        entry unreachable; and a hand-built handle could carry a negative or ``NaN`` amount,
        which admitted a call under a zero ceiling and corrupted the total respectively. The
        amount is revalidated because nothing else has looked at it since construction.
        """
        self._own(reservation)
        if reservation.issuer is not self:
            raise ValueError("this reservation belongs to a different budget")
        self.checked_amount("the reservation estimate", reservation.estimate)
        return reservation

    def _take(self, reservation: Reservation) -> Reservation:
        """Admit and claim for a handle that already exists. Shared by both entry points.

        The family lock is reentrant, because ``_admit`` and ``_claim`` walk the parent chain
        and every node shares one lock. Reentrancy is what a same-thread trace, profile or
        signal callback needs: entering another ``attempt`` between the admission and the
        claim, it was admitted against a state the outer call had already been promised, and
        both then claimed it — ``max_calls=1`` finished with two calls and 1.2 of cost.

        The admission is therefore re-taken if the ledger moved under it. A version counter
        on the family root advances on every claim and every resolution; if it changed while
        this admission was being decided, the decision is stale and is made again against
        what is there now. Nothing is refused for being nested — the real ceiling decides,
        which is the only thing that should.

        A flag marking the transaction was tried first and is the wrong shape: clearing it is
        an assignment, an interrupt can land before it, and the whole family is then refused
        for good. Nothing here needs clearing.
        """
        self._own_here(reservation)
        root = self._root()
        with self._lock:
            while True:
                seen = root._version
                self._admit(reservation.estimate)
                if root._version == seen:
                    self._claim(reservation, reservation.estimate)
                    return reservation

    def _root(self) -> Budget:
        """The budget that owns the family's shared state. One lock, one guard."""
        node = self
        while node._parent is not None:
            node = node._parent
        return node

    def check_admissible(self, estimated_cost: float = 0.0) -> None:
        """Raise if this attempt would be refused, without claiming anything.

        So a run a ceiling will refuse can be refused before the provider library is
        imported. Importing it is not contacting a provider, but it is not free: the shipped
        adapter fetches a remote price map on first use, so a request the cost ceiling was
        always going to reject still reached the network and took about a second to say no.

        Deliberately *not* a claim. Holding one across the import made a sibling on a shared
        budget see an exhausted ceiling and be terminally refused, on an allowance the final
        ledger then said was free. ``reserve`` stays the authoritative check and is the only
        thing that takes anything; asking the same question twice costs nothing, and the
        window between them can admit a call that ``reserve`` then refuses — which is the
        accepted cost of not blocking a sibling. See the note in :meth:`reserve`.
        """
        estimate = self.checked_amount("the reservation estimate", estimated_cost)
        with self._lock:
            self._admit(estimate)

    def _admit(self, estimate: float) -> None:
        """Refuse if this budget or any parent has no room. Nothing is taken here."""
        if self._parent is not None:
            self._parent._admit(estimate)
        with self._lock:
            limits = self.limits
            if limits.max_calls is not None and self.calls >= limits.max_calls:
                self._stop("max_calls", f"call limit reached ({limits.max_calls})")
            if limits.max_seconds is not None and self.elapsed >= limits.max_seconds:
                self._stop(
                    "max_seconds",
                    f"time limit reached ({limits.max_seconds}s, elapsed {self.elapsed:.1f}s)",
                )
            if (
                limits.max_cost is not None
                and self.cost + self.reserved + estimate > limits.max_cost
            ):
                self._stop(
                    "max_cost",
                    f"cost limit reached ({limits.max_cost}, "
                    f"committed {self.cost + self.reserved:.6f}, "
                    f"this attempt estimated at {estimate:.6f})",
                )

    def _claim(self, claim: Reservation, estimate: float) -> None:
        """Take the room admitted above, here and in every parent."""
        if self._parent is not None:
            self._parent._claim(claim, estimate)
        with self._lock:
            self.calls += 1
            self._outstanding[claim] = estimate
            self._resum()
            self._root()._version += 1

    @contextmanager
    def attempt(self, estimated_cost: float = 0.0) -> Generator[Reservation]:
        """One claimed attempt whose cleanup is installed before the claim is taken.

        ``reserve`` hands back a handle, and between it returning and the caller's ``try``
        beginning there is a window an interrupt or a profiling hook can land in: the claim
        is taken, nobody holds the handle, and the headroom never comes back. Here the
        ``finally`` is established first, so there is no instant at which a claim exists
        without something that will resolve it.

        Releasing on exit is right because a reservation that reached the provider is
        resolved inside the block — settled, or kept unpriced. Reaching here unresolved
        means the attempt never happened.
        """
        # The handle exists before anything is claimed against it, and the finalizer holds
        # it before the claim is taken. Assigning it from `reserve`'s return left a window:
        # an interrupt after the claim mutated the family and before the assignment unwound
        # with the claim taken and nothing anywhere that could give it back.
        reservation = self._handle(estimated_cost)
        try:
            yield self._take(reservation)
        finally:
            # One locked operation, not "read resolved, drop the lock, then release".
            # Between those two another thread could settle the same handle, and this
            # cleanup became the losing resolver and raised — replacing whatever the block
            # was already failing with. A caller lost its own exception to an internal
            # lifecycle error. No-ops when nothing was ever claimed.
            self._release_if_outstanding(reservation)

    def settle_reservation(self, reservation: Reservation, actual: float) -> None:
        """Called by Reservation. Not the caller's API; use :meth:`reserve`."""
        self._resolve(reservation, actual=max(0.0, actual), refund_call=False)

    def release_reservation(self, reservation: Reservation, refund_call: bool) -> None:
        """Called by Reservation. Not the caller's API; use :meth:`reserve`."""
        self._resolve(reservation, actual=0.0, refund_call=refund_call)

    def _release_if_outstanding(self, reservation: Reservation) -> None:
        """Give back this claim if it is still held, and do nothing if it is not.

        Only for :meth:`attempt`'s cleanup. A caller resolving the same handle twice is still
        an error and still raises; this exists so a finalizer cannot turn someone else's
        winning resolution into an exception of its own.
        """
        self._own(reservation)
        with self._lock:
            if reservation not in self._outstanding:
                return
            self._resolve_here(reservation, 0.0, refund_call=True)
            parent = self._parent
            while parent is not None:
                parent._resolve_here(reservation, 0.0, refund_call=True)
                parent = parent._parent

    def _resolve(self, reservation: Reservation, actual: float, refund_call: bool) -> None:
        """Claim the right to resolve this handle, then resolve it, under one lock.

        The whole family shares one lock, so deciding that this handle is still outstanding
        and removing it are a single atomic step. Split apart — a flag on the handle read
        without the lock, then a removal by matching amount — two threads resolving one
        handle both won and both removed, taking a *different* live handle's claim with
        them and letting the budget admit past its ceilings.
        """
        self._own(reservation)
        with self._lock:
            if reservation not in self._outstanding:
                raise RuntimeError("this reservation is already resolved")
            self._resolve_here(reservation, actual, refund_call)
            parent = self._parent
            while parent is not None:
                parent._resolve_here(reservation, actual, refund_call)
                parent = parent._parent

    def _resolve_here(self, claim: Reservation, actual: float, refund_call: bool) -> None:
        """Caller holds the family lock."""
        self._drop(claim)
        # Added one charge at a time, which is what `estimate` publishes the total of. An
        # `fsum` here would be marginally steadier and would no longer be the figure a
        # caller sized their ceiling against, which is the property that matters.
        self.cost += actual
        if refund_call:
            self.calls = max(0, self.calls - 1)
        self._root()._version += 1

    def holds(self, reservation: Reservation) -> bool:
        """Whether this handle's claim is still outstanding. The handle's resolved state."""
        with self._lock:
            return reservation in self._outstanding

    def _drop(self, claim: Reservation) -> None:
        """Remove this handle's claim and re-derive the total. Caller holds the lock."""
        self._outstanding.pop(claim, None)
        self._resum()

    def _resum(self) -> None:
        """Caller holds the lock. ``fsum`` so the total does not drift with resolution order.

        Amounts are validated finite where they enter, and a finite sum of finite amounts
        can still exceed a float. Reaching that needs a total around 1e308, which no real
        pricing produces — it takes a caller passing deliberate nonsense through a custom
        client's ``bound_for_attempt``, and what they get is ``inf`` in their own metadata.
        Guarding it was tried and withdrawn: the guard could not refuse a settlement without
        leaving the attempt unresolved, and the cleanup then refunded a call that had reached
        the provider. That broke the hard call ceiling for every caller, which is a real loss
        traded against an impossible one.
        """
        try:
            self.reserved = math.fsum(self._outstanding.values())
        except OverflowError:
            # Saturate rather than raise. Raising here left the parent claimed and the child
            # not, and the child's finalizer could not reach the parent's entry — so an
            # absurd figure took shared headroom permanently instead of merely reading as
            # absurd. A later removal recomputes a finite total.
            self.reserved = math.inf

    # ------------------------------------------------------------------------ rows --
    def add_rows(self, n: int) -> int:
        """Record rows and return how many were kept under the row limit.

        Rows are admitted after parsing rather than reserved, because the count is not
        known until the records are in hand.
        """
        with self._lock:
            kept = min(n, self._row_room(n))
            self._take_rows(kept)
            if kept < n:
                self.stopped_by = "max_rows"
            return kept

    def _row_room(self, n: int) -> int:
        with self._lock:
            limit = self.limits.max_rows
            mine = n if limit is None else max(0, limit - self.rows)
        return mine if self._parent is None else min(mine, self._parent._row_room(n))

    def _take_rows(self, kept: int) -> None:
        with self._lock:
            self.rows += kept
        if self._parent is not None:
            self._parent._take_rows(kept)

    def rows_exhausted(self) -> bool:
        limit = self.limits.max_rows
        if limit is not None and self.rows >= limit:
            return True
        return self._parent.rows_exhausted() if self._parent is not None else False

    # ------------------------------------------------------------------- reporting --
    def note_exhausted(self) -> str | None:
        """Record a ceiling this call has crossed, and report it. Never raises.

        Evaluated against the chain's ceilings rather than read from an ancestor's
        ``stopped_by``. Time and spend are facts about the world at this moment, so a
        deadline crossed while several calls were in flight belongs to all of them, and a
        ceiling a *different* call was refused by belongs to none of them.

        Both errors have been made here. Asking whether the parent was already marked
        dropped a shared deadline from every sibling but the first to fold. Treating any
        newer ancestor mark as this call's own then reported two admitted, completed calls
        as stopped because a third sibling was refused.

        An admission refusal is not reported here at all. It reaches the call it refused,
        as the exception ``reserve`` raises, and reaches no other.
        """
        crossed = self._crossed_ceiling()
        if crossed is None:
            return None
        with self._lock:
            if not self.stopped_by:
                self.stopped_by = crossed
        # The ancestors carry it too, for a caller reading the budget it owns.
        parent = self._parent
        while parent is not None:
            with parent._lock:
                if not parent.stopped_by:
                    parent.stopped_by = crossed
            parent = parent._parent
        return crossed

    def _crossed_ceiling(self) -> str | None:
        """The tightest ceiling in this chain that completed work has actually crossed.

        Strictly crossed for cost: spending exactly the ceiling refuses nothing, which is
        the same reasoning that keeps an exact call ceiling from reading as a stop.
        """
        budget: Budget | None = self
        while budget is not None:
            limits = budget.limits
            if limits.max_seconds is not None and budget.elapsed >= limits.max_seconds:
                return "max_seconds"
            if limits.max_cost is not None and budget.cost > limits.max_cost:
                return "max_cost"
            budget = budget._parent
        return None

    def binding_deadline(self) -> tuple[float | None, bool]:
        """The ceiling in this chain with the least time left, and whether it is this one.

        Reporting a limit means finding which one actually bound. Reading this budget's own
        ``max_seconds`` named a local one-second ceiling for a call the caller's twenty
        millisecond ceiling had stopped, and named nothing at all when the child had no
        ceiling of its own.

        Compared as absolute deadlines rather than as time remaining. Remaining time takes a
        clock reading per node, so a walk that straddles a tick could rank a later ceiling as
        the tighter one and name the wrong owner. A deadline is a fixed point and two of them
        order the same way whenever they are read.
        """
        best: float | None = None
        mine = False
        budget: Budget | None = self
        first = True
        earliest: float | None = None
        while budget is not None:
            limit = budget.limits.max_seconds
            if limit is not None:
                deadline = budget._started + limit
                if earliest is None or deadline < earliest:
                    earliest, best, mine = deadline, limit, first
            budget = budget._parent
            first = False
        return best, mine

    def check_deadline(self) -> None:
        """Raise if the time limit has passed. Call between attempts, not only before."""
        if self._parent is not None:
            self._parent.check_deadline()
        with self._lock:
            limit = self.limits.max_seconds
            if limit is not None and self.elapsed >= limit:
                self._stop(
                    "max_seconds", f"time limit reached ({limit}s, elapsed {self.elapsed:.1f}s)"
                )

    def _stop(self, limit: str, message: str) -> None:
        self.stopped_by = limit
        raise BudgetExceeded(message, limit=limit)
