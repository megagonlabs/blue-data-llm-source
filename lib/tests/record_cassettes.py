"""Record model-boundary cassettes from real provider calls.

Not part of the test run. Run it by hand when a prompt changes and the recordings need
refreshing:

    python tests/record_cassettes.py            # dry run, prints what it would spend
    python tests/record_cassettes.py --record   # makes real calls

Every call is capped. The script refuses to start without a budget, stops the moment the
ceiling is reached, and writes a receipt next to the cassettes recording what was spent.
Responses are stored, not requests, and no key is ever written to disk.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Protocol, cast

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from blue_data_llm_source import Attr  # noqa: E402
from blue_data_llm_source.prompts import build_fetch_prompt  # noqa: E402

CASSETTES = Path(__file__).resolve().parent / "cassettes"
RECEIPT = CASSETTES / "RECEIPT.json"

# A hard ceiling for the whole run, well under the authorised limit. The script stops
# before a call that would cross it rather than after.
MAX_SPEND_USD = 1.00
# The worst a single call here can cost, derived rather than guessed: this model's
# published rates times the largest request this script can send. Reserved before each
# call so the ceiling bounds spend instead of reporting it afterwards.
MAX_PROMPT_TOKENS = 2_000
MAX_OUTPUT_TOKENS = 4_096
PRICE_PER_PROMPT_TOKEN = 0.15 / 1_000_000
PRICE_PER_OUTPUT_TOKEN = 0.60 / 1_000_000
MAX_CALL_COST_USD = (
    MAX_PROMPT_TOKENS * PRICE_PER_PROMPT_TOKEN + MAX_OUTPUT_TOKENS * PRICE_PER_OUTPUT_TOKEN
)
MODEL = "gpt-4o-mini"

# The four cases the data-source specification gives, which are the v1.0 fixtures.
CASES: list[dict[str, Any]] = [
    {
        "name": "president_no_attrs",
        "query": "Who is the current US president?",
        "attrs": [],
        "context": "",
    },
    {
        "name": "president_with_attrs",
        "query": "Who is the current US president?",
        "attrs": [Attr("first name"), Attr("last name"), Attr("age"), Attr("party")],
        "context": "",
    },
    {
        "name": "president_with_context",
        "query": "Who is the current US president?",
        "attrs": [Attr("name"), Attr("age"), Attr("party")],
        "context": "The name in the output should be the last name.",
    },
    {
        "name": "three_presidents",
        "query": "Show me 3 presidents of the United States",
        "attrs": [Attr("name"), Attr("year"), Attr("party")],
        "context": "Year in output refers to the year that the person became president.",
    },
]


class _Provider(Protocol):
    """The two litellm entry points this script calls.

    litellm annotates much of its surface as bare ``List`` and ``dict``, which a strict
    checker reads as unknown. This mirrors the narrower declaration the library client
    makes for the same reason.
    """

    def completion(self, **kwargs: Any) -> Any: ...

    def completion_cost(self, **kwargs: Any) -> Any: ...


def _provider() -> _Provider:
    import litellm  # lazy: the provider extra is optional and this script is not run in CI

    return cast("_Provider", litellm)


def _cost(provider: _Provider, response: Any) -> float | None:
    """The call's cost, or None when it cannot be read.

    None is not zero. Treating an unreadable cost as free is what lets a ceiling admit
    every remaining call while the running total stays where it started.
    """
    try:
        return float(provider.completion_cost(completion_response=response))
    except Exception:
        return None


def _usage(payload: dict[str, Any], field: str) -> Any:
    """Token counts, whichever shape the provider returned them in."""
    usage: Any = payload.get("usage")
    if isinstance(usage, dict):
        return cast("dict[str, Any]", usage).get(field)
    return getattr(usage, field, None)


def fits(spent: float, ceiling: float) -> bool:
    """Whether another call can be afforded at its worst plausible cost.

    Checking only what has been spent lets the next unreserved call cross the ceiling by
    its full cost, which makes the ceiling a report rather than a limit. Reserving the
    worst case before the call is what turns it back into one.
    """
    return spent + MAX_CALL_COST_USD <= ceiling


def record(
    ceiling: float, provider: _Provider, write: bool = True
) -> tuple[list[dict[str, Any]], float]:
    """Record every case that fits under *ceiling*, and report what was spent.

    Separated from ``main`` so the ceiling can be driven offline with a scripted
    provider. A guard that is only exercised by spending real money is not a guard.
    """
    spent = 0.0
    written: list[dict[str, Any]] = []

    for case in CASES:
        if not fits(spent, ceiling):
            print(
                f"stopping: ${spent:.4f} spent, ceiling ${ceiling:.2f}, "
                f"no room for another call at ${MAX_CALL_COST_USD:.4f}"
            )
            break

        prompt = build_fetch_prompt(case["query"], case["attrs"], case["context"])
        started = time.monotonic()
        response: Any = provider.completion(
            model=MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            max_tokens=MAX_OUTPUT_TOKENS,
        )
        elapsed = time.monotonic() - started
        # completion() is typed as also returning a streaming wrapper. Nothing here
        # streams, so the response is narrowed once rather than at every use.
        dump = getattr(response, "model_dump", None)
        payload: dict[str, Any] = cast("dict[str, Any]", dump() if callable(dump) else response)

        cost = _cost(provider, response)
        if cost is None:
            # The reservation is what was actually committed, so it stays. Recording
            # cannot continue without a trustworthy total: the next ceiling check would
            # be comparing against a number that no longer means anything.
            spent += MAX_CALL_COST_USD
            print(
                f"stopping: cost could not be read for {case['name']}. "
                f"Charged the reserved ${MAX_CALL_COST_USD:.4f} and stopped.",
                file=sys.stderr,
            )
            break
        spent += cost

        if write:
            path = CASSETTES / f"{case['name']}.json"
            path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        written.append(
            {
                "name": case["name"],
                "model": MODEL,
                "cost_usd": round(cost, 6),
                "latency_seconds": round(elapsed, 3),
                "prompt_tokens": _usage(payload, "prompt_tokens"),
                "completion_tokens": _usage(payload, "completion_tokens"),
            }
        )
        print(f"recorded {case['name']}: ${cost:.6f}, {elapsed:.2f}s")

    return written, spent


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--record", action="store_true", help="make real provider calls")
    parser.add_argument("--budget", type=float, default=MAX_SPEND_USD)
    args = parser.parse_args()

    if args.budget <= 0:
        print("refusing to run without a positive budget", file=sys.stderr)
        return 2

    CASSETTES.mkdir(parents=True, exist_ok=True)

    if not args.record:
        print(f"dry run. {len(CASES)} cases, model {MODEL}, ceiling ${args.budget:.2f}")
        for case in CASES:
            print(f"  would record {case['name']}")
        print("pass --record to make real calls")
        return 0

    if not os.environ.get("OPENAI_API_KEY"):
        print("OPENAI_API_KEY is not set", file=sys.stderr)
        return 2

    written, spent = record(args.budget, _provider(), write=True)

    RECEIPT.write_text(
        json.dumps(
            {
                "model": MODEL,
                "ceiling_usd": args.budget,
                "total_cost_usd": round(spent, 6),
                "cassettes": written,
                "note": (
                    "Recorded by tests/record_cassettes.py. Responses only; no request "
                    "bodies and no credentials are stored."
                ),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\ntotal ${spent:.6f} of ${args.budget:.2f} ceiling. Receipt: {RECEIPT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
