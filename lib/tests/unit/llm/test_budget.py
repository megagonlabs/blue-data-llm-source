"""Budget, one test per axis.

Each configured limit is checked on its own axis.
"""

from __future__ import annotations

import math
import threading
import time

import pytest

from blue_data_llm_source.llm.budget import Budget, Limits, Reservation
from blue_data_llm_source.types import BudgetExceeded, ConfigError


class FakeClock:
    """A monotonic clock a test drives by hand."""

    def __init__(self, now: float = 0.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class SlowClock:
    """Delay clock reads to expose a gap between checking and claiming a call."""

    def __init__(self, delay: float = 0.005) -> None:
        self.delay = delay

    def __call__(self) -> float:
        time.sleep(self.delay)
        return 0.0


def test_an_unlimited_budget_never_stops() -> None:
    budget = Budget()
    for _ in range(10):
        budget.reserve()
    assert budget.calls == 10
    assert budget.stopped_by is None


def test_max_calls_stops_the_run_and_records_the_limit_that_stopped_it() -> None:
    budget = Budget(Limits(max_calls=2))
    budget.reserve()
    budget.reserve()

    with pytest.raises(BudgetExceeded) as caught:
        budget.reserve()

    assert caught.value.limit == "max_calls"
    assert budget.stopped_by == "max_calls"
    assert budget.calls == 2


def test_max_cost_refuses_an_attempt_whose_estimate_would_cross_the_ceiling() -> None:
    """Refused on the estimate, before the money is spent rather than after."""
    budget = Budget(Limits(max_cost=0.50))
    budget.reserve(0.40).settle(0.40)

    with pytest.raises(BudgetExceeded) as caught:
        budget.reserve(0.20)

    assert caught.value.limit == "max_cost"
    assert budget.stopped_by == "max_cost"
    # The refused attempt was never claimed, so it can never be charged.
    assert budget.calls == 1
    assert budget.cost == 0.40


def test_a_costless_attempt_is_admitted_at_an_exactly_met_ceiling() -> None:
    """Reaching a ceiling refuses nothing; only crossing it does.

    An attempt estimated at nothing cannot cross a ceiling, and refusing it would be the
    same error as reporting a stop when the last allowed call is spent.
    """
    budget = Budget(Limits(max_cost=0.50))
    budget.reserve(0.50).settle(0.50)

    budget.reserve(0.0)

    assert budget.calls == 2
    assert budget.stopped_by is None


def test_outstanding_reservations_count_against_the_ceiling() -> None:
    """Two concurrent attempts cannot each see room for one.

    This is the reason to reserve rather than settle: without the outstanding claim, both
    would be admitted and the ceiling crossed by a whole call.
    """
    budget = Budget(Limits(max_cost=1.0))
    first = budget.reserve(0.80)

    with pytest.raises(BudgetExceeded):
        budget.reserve(0.80)

    first.settle(0.10)
    # The overshoot is released on settlement, so the room comes back.
    budget.reserve(0.80)
    assert budget.cost == 0.10


def test_max_cost_allows_a_call_while_spend_is_below_the_limit() -> None:
    budget = Budget(Limits(max_cost=1.0))
    budget.reserve().settle(0.99)
    budget.reserve()
    assert budget.calls == 2
    assert budget.stopped_by is None


def test_max_seconds_stops_the_run_once_the_clock_passes_the_limit() -> None:
    clock = FakeClock()
    budget = Budget(Limits(max_seconds=30.0), clock=clock)
    budget.reserve()

    clock.advance(29.0)
    budget.reserve()

    clock.advance(1.0)
    with pytest.raises(BudgetExceeded) as caught:
        budget.reserve()

    assert caught.value.limit == "max_seconds"
    assert budget.stopped_by == "max_seconds"
    assert budget.calls == 2


def test_elapsed_is_measured_from_construction_with_the_injected_clock() -> None:
    clock = FakeClock(now=1000.0)
    budget = Budget(clock=clock)
    clock.advance(4.5)
    assert budget.elapsed == pytest.approx(4.5)


def test_max_rows_truncates_and_records_the_kept_count() -> None:
    budget = Budget(Limits(max_rows=2))
    assert budget.add_rows(5) == 2
    assert budget.rows == 2
    assert budget.stopped_by == "max_rows"
    assert budget.rows_exhausted() is True


def test_rows_under_the_limit_are_all_kept() -> None:
    budget = Budget(Limits(max_rows=5))
    assert budget.add_rows(3) == 3
    assert budget.stopped_by is None
    assert budget.rows_exhausted() is False


def test_rows_are_unlimited_when_no_row_limit_is_set() -> None:
    budget = Budget()
    assert budget.add_rows(10_000) == 10_000
    assert budget.rows_exhausted() is False


def test_a_second_batch_of_rows_sees_the_room_the_first_left() -> None:
    budget = Budget(Limits(max_rows=4))
    assert budget.add_rows(3) == 3
    assert budget.add_rows(3) == 1
    assert budget.rows == 4


def test_a_cache_hit_consumes_no_cost_budget() -> None:
    budget = Budget(Limits(max_cost=1.0))
    budget.reserve().settle(0.75, cache_hit=True)
    assert budget.cost == 0.0
    # Still spendable, which is the point: a cached answer must not price out a real one.
    budget.reserve()
    assert budget.stopped_by is None


def test_settle_refuses_a_negative_cost() -> None:
    """Silently clamping to zero is how an unreadable amount became a free call."""
    budget = Budget()
    reservation = budget.reserve()

    with pytest.raises(ConfigError, match="settled cost"):
        reservation.settle(-5.0)

    # A refused settlement resolves the contacted attempt at its estimate.
    assert reservation.resolved
    assert budget.cost == 0.0, "this reservation was taken at an estimate of nothing"


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf"), "free", None])
def test_a_settlement_that_is_not_an_amount_is_refused_and_charged_at_the_estimate(
    bad: object,
) -> None:
    """Invalid actual costs are refused while the claim settles at its estimate."""
    budget = Budget(Limits(max_cost=0.10))
    reservation = budget.reserve(0.05)

    with pytest.raises(ConfigError):
        reservation.settle(bad)  # pyright: ignore[reportArgumentType]

    # Refuse the invalid amount and resolve the claim at its estimate. `None` is the
    # value `_cost_of` returns when a cost cannot be read.
    assert reservation.resolved
    assert budget.cost == 0.05, "charged at the estimate, exactly as keep_unpriced would"
    assert budget.reserved == 0.0


def test_reserve_under_concurrent_callers_never_exceeds_max_calls() -> None:
    workers = 32
    allowed = 5
    budget = Budget(Limits(max_calls=allowed))
    start = threading.Barrier(workers)
    granted: list[int] = []
    granted_lock = threading.Lock()

    def worker(index: int) -> None:
        start.wait(timeout=5)
        try:
            budget.reserve()
        except BudgetExceeded:
            return
        with granted_lock:
            granted.append(index)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(workers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert len(granted) == allowed
    assert budget.calls == allowed


def test_the_check_and_the_claim_happen_under_one_lock() -> None:
    workers = 24
    allowed = 4
    budget = Budget(Limits(max_calls=allowed, max_seconds=1000.0), clock=SlowClock())
    start = threading.Barrier(workers)
    granted: list[int] = []
    granted_lock = threading.Lock()

    def worker(index: int) -> None:
        start.wait(timeout=5)
        try:
            budget.reserve()
        except BudgetExceeded:
            return
        with granted_lock:
            granted.append(index)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(workers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert len(granted) == allowed
    assert budget.calls == allowed


def test_concurrent_add_rows_never_exceeds_max_rows() -> None:
    workers = 16
    budget = Budget(Limits(max_rows=10))
    start = threading.Barrier(workers)
    kept: list[int] = []
    kept_lock = threading.Lock()

    def worker() -> None:
        start.wait(timeout=5)
        taken = budget.add_rows(3)
        with kept_lock:
            kept.append(taken)

    threads = [threading.Thread(target=worker) for _ in range(workers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert sum(kept) == 10
    assert budget.rows == 10


def test_budget_exceeded_carries_the_limit_name_in_its_message() -> None:
    budget = Budget(Limits(max_calls=0))
    with pytest.raises(BudgetExceeded, match="call limit reached"):
        budget.reserve()


# ------------------------------------------------------ reservation and derivation --
def test_concurrent_leaves_cannot_collectively_overshoot_a_shared_ceiling() -> None:
    """The reason to reserve rather than settle.

    Settling afterwards, ten leaves each see room for one and the ceiling is crossed by
    nine calls' worth before anything notices.
    """
    parent = Budget(Limits(max_cost=1.0))
    admitted = 0
    for _ in range(10):
        try:
            parent.derive().reserve(0.30)
        except BudgetExceeded:
            continue
        admitted += 1

    assert admitted == 3


def test_a_reservation_resolves_exactly_once() -> None:
    budget = Budget()
    reservation = budget.reserve(0.10)
    reservation.settle(0.05)

    for resolve in (
        lambda: reservation.settle(0.05),
        reservation.release,
        reservation.keep_unpriced,
    ):
        with pytest.raises(RuntimeError, match="already resolved"):
            resolve()


def test_settling_below_the_estimate_gives_the_difference_back() -> None:
    budget = Budget(Limits(max_cost=1.0))
    budget.reserve(0.90).settle(0.10)

    assert budget.cost == 0.10
    assert budget.reserved == 0.0
    assert budget.remaining_cost() == 0.90


def test_a_released_reservation_returns_its_attempt_and_its_estimate() -> None:
    budget = Budget(Limits(max_calls=1, max_cost=1.0))
    budget.reserve(0.90).release()

    assert budget.calls == 0
    assert budget.committed == 0.0
    budget.reserve(0.90)


def test_an_unpriced_attempt_is_charged_at_its_estimate() -> None:
    budget = Budget(Limits(max_cost=1.0))
    budget.reserve(0.60).keep_unpriced()

    assert budget.cost == 0.60
    with pytest.raises(BudgetExceeded):
        budget.reserve(0.60)


def test_a_cache_hit_gives_back_the_attempt_it_claimed() -> None:
    """Nothing reached the provider, so nothing was consumed on any axis."""
    budget = Budget(Limits(max_calls=1))
    budget.reserve(0.10).settle(0.99, cache_hit=True)

    assert budget.calls == 0
    assert budget.cost == 0.0
    budget.reserve(0.10)


def test_a_child_ceiling_narrows_the_parent_and_never_widens_it() -> None:
    parent = Budget(Limits(max_cost=1.0, max_calls=4))
    tight = parent.derive(Limits(max_cost=0.25))
    loose = parent.derive(Limits(max_cost=100.0))

    assert tight.remaining_cost() == 0.25
    assert loose.remaining_cost() == 1.0
    assert loose.remaining_calls() == 4


def test_a_childs_spend_is_visible_to_the_parent() -> None:
    parent = Budget(Limits(max_cost=1.0))
    parent.derive().reserve(0.40).settle(0.40)

    assert parent.cost == 0.40
    assert parent.remaining_cost() == 0.60


def test_concurrent_reservations_never_exceed_a_derived_call_ceiling() -> None:
    parent = Budget(Limits(max_calls=5))
    admitted: list[int] = []
    lock = threading.Lock()

    def worker() -> None:
        try:
            parent.derive().reserve()
        except BudgetExceeded:
            return
        with lock:
            admitted.append(1)

    threads = [threading.Thread(target=worker) for _ in range(32)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(admitted) == 5
    assert parent.calls == 5


# ----------------------------------------------- non-finite amounts at the public edge --
@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf"), -1.0, "free", True])
def test_a_reservation_estimate_that_is_not_an_amount_is_refused(bad: object) -> None:
    """max(0.0, NaN) is 0.0, so this would consume a call and leave the ceiling untouched.

    The provider adapter filters these, but this is the boundary a custom client reaches.
    """
    budget = Budget(Limits(max_cost=0.10))
    with pytest.raises(ConfigError):
        budget.reserve(bad)  # pyright: ignore[reportArgumentType]
    assert budget.calls == 0, "a refused reservation must not consume an attempt"


def test_a_spent_cost_ceiling_is_crossed_for_every_later_call_too() -> None:
    """Cost is a fact about the world, not an event belonging to one call.

    Money an earlier call spent is still spent. A later call under the same budget really is
    over the ceiling, so reporting it is correct rather than contamination. What must not
    spread is an admission refusal, which is about one call being refused; that reaches only
    the call it refused, as the exception reserve raises.
    """
    parent = Budget(Limits(max_cost=0.10))
    first = parent.derive()
    first.reserve(0.05).settle(0.20)
    assert first.note_exhausted() == "max_cost"

    later = parent.derive()
    assert later.note_exhausted() == "max_cost", "the ceiling is still crossed"


def test_an_admission_refusal_does_not_reach_a_call_it_did_not_refuse() -> None:
    """A refused sibling leaves admitted calls unchanged."""
    parent = Budget(Limits(max_calls=2))
    admitted = [parent.derive(), parent.derive()]
    for child in admitted:
        child.reserve().settle(0.0)

    refused = parent.derive()
    with pytest.raises(BudgetExceeded) as caught:
        refused.reserve()
    assert caught.value.limit == "max_calls"

    for child in admitted:
        assert child.note_exhausted() is None, "an admitted call was not refused by anything"
        assert child.stopped_by is None


def test_note_exhausted_reports_a_ceiling_this_call_crossed_on_a_parent() -> None:
    """Read from the child alone, a deadline crossed on the caller's budget was lost."""
    clock = FakeClock()
    parent = Budget(Limits(max_seconds=1.0), clock=clock)
    child = parent.derive()

    clock.advance(2.0)

    assert child.note_exhausted() == "max_seconds"


def test_two_threads_resolving_one_handle_cannot_take_another_handles_claim() -> None:
    """Exactly one resolution wins, and it touches only its own claim.

    The handle's state lived in an unlocked flag and the ledger removed claims by matching
    amount. Two threads resolving one handle both saw the flag unset, both removed, and the
    second removal took a *different* live handle's claim — so a budget with two attempts
    and 1.0 of cost admitted a third attempt and 1.5 of live bound.
    """
    import threading

    budget = Budget(Limits(max_calls=2, max_cost=1.0))
    first = budget.reserve(0.5)
    second = budget.reserve(0.5)  # same amount, different claim

    outcomes: list[str] = []
    barrier = threading.Barrier(2)

    def resolve() -> None:
        barrier.wait()
        try:
            first.release()
            outcomes.append("resolved")
        except RuntimeError:
            outcomes.append("refused")

    threads = [threading.Thread(target=resolve) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sorted(outcomes) == ["refused", "resolved"], outcomes
    assert not second.resolved, "the other handle's claim was taken"
    assert budget.calls == 1 and budget.committed == pytest.approx(0.5)
    with pytest.raises(BudgetExceeded):
        budget.reserve(0.6)  # 0.5 outstanding + 0.6 crosses 1.0


def test_cleanup_does_not_replace_the_exception_it_is_cleaning_up() -> None:
    """Cleanup preserves the caller's failure if another thread settles the handle.

    The hook below settles the handle if cleanup checks its state outside the lock. The
    original ``ValueError`` must still reach the caller.
    """
    budget = Budget(Limits(max_calls=2))
    real_holds = budget.holds
    fired: list[str] = []

    def holds_then_settle(reservation: Reservation) -> bool:
        answer = real_holds(reservation)
        if answer and not fired:
            fired.append("settled")
            reservation.settle(0.05)  # the other thread wins, between observation and act
        return answer

    budget.holds = holds_then_settle  # pyright: ignore[reportAttributeAccessIssue]

    def exercise() -> None:
        with budget.attempt(0.1):
            raise ValueError("the caller's own failure")

    with pytest.raises(ValueError, match="the caller's own failure"):
        exercise()

    assert budget.reserved == 0.0, "a claim was left outstanding either way"


def test_an_unrepresentable_total_is_left_to_the_caller_who_asked_for_it() -> None:
    """Extreme finite claims may sum beyond the float range without losing call counts.

    A custom client can supply amounts near 1e308. The committed total then becomes
    ``inf``, while call and cost ceilings still apply to subsequent work.
    """
    budget = Budget(Limits(max_calls=4))
    first = budget.reserve(1e308)
    first.settle(1e308)
    second = budget.reserve(1e308)

    assert math.isinf(budget.committed), "the absurd total is reported, not refused"
    # The axis that matters is untouched: two attempts were made and two were counted.
    assert budget.calls == 2
    second.release()
    assert budget.calls == 1


def test_a_reentrant_take_cannot_spend_the_headroom_twice() -> None:
    """The family lock is reentrant, and that is what a trace callback needs.

    Entering another `attempt` between an admission and its claim, the inner one was admitted
    against a state the outer had already been promised, and both then claimed it: a budget
    of one call and 1.0 of cost finished with two calls and 1.2.

    Nesting is not refused — the ceiling decides. Whichever of the two is refused, the ledger
    must never hold both.
    """
    budget = Budget(Limits(max_calls=1, max_cost=1.0))
    real_admit = budget._admit  # pyright: ignore[reportPrivateUsage]
    inner: list[str] = []

    def admit_then_reenter(estimate: float) -> None:
        real_admit(estimate)
        if not inner:
            inner.append("tried")
            # Stands in for a trace or signal callback landing between admit and claim.
            try:
                with budget.attempt(0.6) as nested:
                    nested.settle(0.6)
            except BudgetExceeded:
                inner.append("refused")

    budget._admit = admit_then_reenter  # pyright: ignore[reportPrivateUsage,reportAttributeAccessIssue]
    outer_refused = False
    try:
        try:
            with budget.attempt(0.6) as reservation:
                reservation.settle(0.6)
        except BudgetExceeded:
            outer_refused = True
    finally:
        budget._admit = real_admit  # pyright: ignore[reportPrivateUsage,reportAttributeAccessIssue]

    assert inner[0] == "tried", "the reentrant path was never taken, so nothing was tested"
    assert outer_refused or "refused" in inner, "both attempts were admitted"
    assert budget.calls <= 1, f"the call ceiling was crossed: {budget.calls}"
    assert budget.cost <= 1.0, f"the cost ceiling was crossed: {budget.cost}"


def test_an_absurd_total_saturates_instead_of_raising() -> None:
    """`fsum` raises on two 1e308 values, and raising here stranded a parent's claim.

    The child inserted into the parent, `fsum` raised before the child's own insert, and the
    child's finalizer found nothing local to remove — so the parent kept a counted call and
    an unreachable claim. The withdrawal says an absurd total reads as absurd; it now does.
    """
    parent = Budget(Limits(max_calls=4))
    child = parent.derive()

    outer = parent.reserve(1e308)
    # Left unresolved on purpose: the finalizer must give it back from both ledgers. Under
    # the raising version the parent was claimed and the child was not, so the child's
    # finalizer found nothing local and the parent's entry could never be reached again.
    with child.attempt(1e308):
        assert math.isinf(parent.reserved)

    outer.release()
    assert parent.calls == 0, "a claim was stranded on the parent"
    assert parent.reserved == 0.0, "the parent's ledger did not come back"
    assert child.reserved == 0.0


def test_a_forged_handle_cannot_free_a_live_claim() -> None:
    """The ledger trusts its key's equality, and the handle class is exported."""

    class Forged(Reservation):
        def __init__(self, real: Reservation) -> None:
            super().__init__(real._budget, real.estimate)  # pyright: ignore[reportPrivateUsage]
            self._real = real

        def __hash__(self) -> int:
            return hash(self._real)

        def __eq__(self, other: object) -> bool:
            return other is self._real or other is self

    budget = Budget(Limits(max_calls=1, max_cost=1.0))
    real = budget.reserve(0.6)

    with pytest.raises(TypeError, match="subclass cannot be used"):
        Forged(real).release()

    assert budget.calls == 1, "the forged handle freed the real claim"
    with pytest.raises(BudgetExceeded):
        budget.reserve(0.6)


def test_an_interruption_inside_the_transaction_leaves_the_family_usable() -> None:
    """An interrupted admission leaves no held lock or stale transaction state."""
    budget = Budget(Limits(max_calls=3, max_cost=1.0))
    real_admit = budget._admit  # pyright: ignore[reportPrivateUsage]
    fired: list[str] = []

    def admit_then_interrupt(estimate: float) -> None:
        real_admit(estimate)
        if not fired:
            fired.append("interrupted")
            raise KeyboardInterrupt  # inside the transaction, after the check

    budget._admit = admit_then_interrupt  # pyright: ignore[reportPrivateUsage,reportAttributeAccessIssue]
    try:
        with pytest.raises(KeyboardInterrupt), budget.attempt(0.4):
            pass  # pragma: no cover - the interrupt lands before the body
    finally:
        budget._admit = real_admit  # pyright: ignore[reportPrivateUsage,reportAttributeAccessIssue]

    assert fired == ["interrupted"]
    assert budget.calls == 0 and budget.reserved == 0.0, "the interrupted attempt left state"

    # The family is still usable, which the boolean version was not.
    with budget.attempt(0.5) as reservation:
        reservation.settle(0.5)
    assert budget.calls == 1


def test_a_handle_from_another_budget_cannot_be_claimed_here() -> None:
    """Exact type was not enough: it says nothing about which ledger issued the handle."""
    one = Budget(Limits(max_calls=2))
    other = Budget(Limits(max_calls=2))
    foreign = other.reserve(0.1)

    with pytest.raises(ValueError, match="different budget"):
        one._take(foreign)  # pyright: ignore[reportPrivateUsage]

    assert one.calls == 0, "a foreign handle was claimed here"


@pytest.mark.parametrize("amount", [-1.0, float("nan"), float("inf")])
def test_a_hand_built_handle_with_a_bad_amount_is_refused(amount: float) -> None:
    """Nothing had looked at the amount since construction, and it need not be valid.

    A negative claim admitted an ordinary call under a zero ceiling; a NaN corrupted the
    total. Both arrive only through a hand-built handle, which is why the amount is checked
    again at the boundary rather than trusted from construction.
    """
    budget = Budget(Limits(max_cost=0.0))
    handle = Reservation(budget, amount)

    with pytest.raises(ConfigError):
        budget._take(handle)  # pyright: ignore[reportPrivateUsage]

    assert budget.reserved == 0.0
    assert budget.calls == 0


def test_recovering_from_a_refused_settlement_does_not_raise_out_of_hierarchy() -> None:
    """The recovery shape the README instructs: resolve on every path out of the block.

    `settle` charges the claim before letting a refused amount out, which is what stops a
    contacted call being refunded. But the caller's own `except: keep_unpriced(); raise` then
    met "already resolved" — a bare RuntimeError, outside the documented hierarchy, replacing
    the ConfigError that says what was actually wrong. `None` is the live case: it is what
    this package's own cost extraction returns when a provider's accounting cannot be read.
    """
    budget = Budget(Limits(max_cost=0.10))
    escaped: BaseException | None = None
    try:
        with budget.attempt(0.05) as reservation:
            try:
                reservation.settle(None)  # pyright: ignore[reportArgumentType]
            except BaseException:
                reservation.keep_unpriced()
                raise
    except BaseException as caught:  # noqa: BLE001 - the type is the assertion
        escaped = caught

    assert isinstance(escaped, ConfigError), f"got {type(escaped).__name__}"
    assert "settled cost" in str(escaped)
    # And the claim still stands as charged, which is the property the refusal path exists for.
    assert budget.calls == 1
    assert budget.cost == 0.05
    assert budget.reserved == 0.0

    # `release` is tolerated on the same footing, and must not refund the contacted call.
    other = Budget(Limits(max_cost=0.10))
    with other.attempt(0.05) as res2:
        try:
            res2.settle("free")  # pyright: ignore[reportArgumentType]
        except ConfigError:
            res2.release()
    assert other.cost == 0.05, "a contacted attempt is not refunded by a late release"
