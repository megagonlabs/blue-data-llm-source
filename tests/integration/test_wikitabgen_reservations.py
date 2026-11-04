"""Exercise the WikiTabGen orchestration with the real reservation ledger."""

from __future__ import annotations

from pathlib import Path
from runpy import run_path
from typing import Any

import pytest
from blue_data_llm_source.llm.budget import Budget, Limits
from blue_data_llm_source.types import BudgetExceeded

DEMO_PATH = (
    Path(__file__).resolve().parents[2]
    / "examples/scenario-demos/partitioned_wikitabgen_demo.py"
)


@pytest.mark.parametrize("attempt_cost", [0.004, 0.0001], ids=["cost-cap", "call-cap"])
@pytest.mark.parametrize(
    ("selected", "expected_calls", "expected_cost"),
    [
        (None, [54, 53, 53], 0.10),
        (("row_by_row", "flat", "partitioned"), [54, 53, 53], 0.10),
        (("partitioned", "row_by_row", "flat"), [54, 53, 53], 0.10),
        (("row_by_row", "partitioned"), [80, 80], 0.15),
        (("partitioned", "flat"), [80, 80], 0.15),
        (("flat",), [160], 0.30),
        (("row_by_row",), [160], 0.30),
        (("partitioned",), [160], 0.30),
    ],
)
def test_selected_modes_keep_their_allowances(
    monkeypatch: pytest.MonkeyPatch,
    attempt_cost: float,
    selected: tuple[str, ...] | None,
    expected_calls: list[int],
    expected_cost: float,
) -> None:
    demo = run_path(str(DEMO_PATH))
    modes = demo["MODES"] if selected is None else selected
    now = 1000.0
    parent = Budget(demo["TOTAL_LIMITS"], clock=lambda: now)
    children: dict[str, Budget] = {}
    admitted: dict[str, int] = {}
    stopped: dict[str, str] = {}
    requested = {"flat": 200, "row_by_row": 200, "partitioned": 9}

    def fake_run_mode(mode: str, budget: Budget) -> dict[str, Any]:
        nonlocal now
        children[mode] = budget
        assert budget.remaining_seconds() == pytest.approx(
            min(900 / len(modes), 900 - parent.elapsed)
        )
        count = 0
        for _ in range(requested[mode]):
            try:
                reservation = budget.reserve(attempt_cost)
            except BudgetExceeded as error:
                stopped[mode] = error.limit
                break
            reservation.keep_unpriced()
            count += 1
        admitted[mode] = count
        now = 1400.0
        return {"mode": mode, "calls": count}

    run_modes = demo["run_modes"]
    monkeypatch.setitem(run_modes.__globals__, "run_mode", fake_run_mode)
    summaries = run_modes(modes, parent)

    assert list(children) == list(modes)
    assert [summary["mode"] for summary in summaries] == list(modes)
    assert [child.limits.max_calls for child in children.values()] == expected_calls
    for mode, child in children.items():
        assert child is not parent
        assert child.limits.max_cost == pytest.approx(expected_cost)
        assert child.limits.max_seconds == 900 / len(modes) + (
            0 if mode == modes[0] else 400
        )
        assert child.calls == admitted[mode] > 0
        assert child.calls <= child.limits.max_calls
        assert child.cost <= expected_cost
        assert child.reserved == 0
        if mode == "partitioned":
            assert admitted[mode] == requested[mode]
        else:
            assert admitted[mode] < requested[mode]
            assert stopped[mode] == (
                "max_cost" if attempt_cost == 0.004 else "max_calls"
            )

    assert parent.limits == Limits(
        max_cost=0.30, max_calls=160, max_seconds=900, max_retries=0
    )
    assert parent.calls == sum(admitted.values()) <= 160
    assert parent.cost == pytest.approx(sum(child.cost for child in children.values()))
    assert parent.cost <= 0.30
    assert parent.reserved == 0

    # A child deadline cannot extend the original shared deadline.
    now = 1899.0
    assert parent.remaining_seconds() == 1
    assert all(child.remaining_seconds() <= 1 for child in children.values())
    now = 1900.0
    for child in children.values():
        with pytest.raises(BudgetExceeded):
            child.reserve(0)
    assert parent.calls == sum(admitted.values())


@pytest.mark.parametrize(
    ("args", "allowances"),
    [
        (
            [],
            [
                ("flat", "0.100000", 54),
                ("partitioned", "0.100000", 53),
                ("row_by_row", "0.100000", 53),
            ],
        ),
        (
            ["--mode", "row_by_row", "--mode", "partitioned"],
            [("row_by_row", "0.150000", 80), ("partitioned", "0.150000", 80)],
        ),
        (["--mode", "partitioned"], [("partitioned", "0.300000", 160)]),
    ],
)
def test_preview_prints_selected_allowances(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    args: list[str],
    allowances: list[tuple[str, str, int]],
) -> None:
    demo = run_path(str(DEMO_PATH))
    monkeypatch.setattr("sys.argv", [str(DEMO_PATH), *args])
    demo["main"]()
    lines = capsys.readouterr().out.splitlines()
    assert "modes: " + ", ".join(mode for mode, _, _ in allowances) in lines
    assert [line for line in lines if " allowance:" in line] == [
        f"{mode} allowance: ${cost}, {calls} calls, {900 / len(allowances):g} seconds; shared 900-second deadline"
        for mode, cost, calls in allowances
    ]
    assert "Add --live to call GPT-6 Luna and print every model response." in lines


@pytest.mark.parametrize(
    "modes",
    [
        ("flat", "partitioned", "row_by_row"),
        ("row_by_row", "flat", "partitioned"),
    ],
)
def test_slow_modes_leave_time_for_later_modes(
    monkeypatch: pytest.MonkeyPatch,
    modes: tuple[str, ...],
) -> None:
    demo = run_path(str(DEMO_PATH))
    now = 1000.0
    parent = Budget(demo["TOTAL_LIMITS"], clock=lambda: now)
    calls: dict[str, int] = {}

    def fake_run_mode(mode: str, budget: Budget) -> dict[str, Any]:
        nonlocal now
        count = 0
        for _ in range(9 if mode == "partitioned" else 1000):
            try:
                reservation = budget.reserve(0.0001)
            except BudgetExceeded as error:
                assert error.limit == "max_seconds"
                break
            reservation.keep_unpriced()
            count += 1
            now += 1 if mode == "partitioned" else 100
        calls[mode] = count
        return {"mode": mode, "calls": count}

    run_modes = demo["run_modes"]
    monkeypatch.setitem(run_modes.__globals__, "run_mode", fake_run_mode)
    run_modes(modes, parent)
    assert calls == {"flat": 3, "row_by_row": 3, "partitioned": 9}
    assert parent.calls == 15
    assert parent.elapsed == 609
    assert parent.remaining_seconds() == 291
