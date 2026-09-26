"""Scripted planner and leaf answers shared by the prompt-contract switch tests.

Each scenario answers from the prompt it is sent, so the same script serves every
combination of plan settings, and a test can compare the prompts one setting sends with
the prompts another sends.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from typing import Any, cast

from blue_data_llm_source.llm.budget import Budget
from blue_data_llm_source.source import LlmSource
from blue_data_llm_source.types import Capability, FetchResult, Usage


class ScriptedClient:
    """Answer from the prompt text and record every prompt that was paid for."""

    def __init__(self, responder: Callable[[str], str]) -> None:
        self.responder = responder
        self.prompts: list[str] = []

    def budget_capabilities(self) -> frozenset[str]:
        return Capability.ALL

    def bound_for_attempt(self, _prompt: str, _properties: dict[str, Any]) -> float:
        return 0.0

    def complete(self, prompt: str, properties: dict[str, Any] | None = None) -> str:
        return self._respond(prompt, properties)

    async def acomplete(self, prompt: str, properties: dict[str, Any] | None = None) -> str:
        return self._respond(prompt, properties)

    def _respond(self, prompt: str, properties: dict[str, Any] | None) -> str:
        text = self.responder(prompt)
        budget = (properties or {}).get("budget")
        assert isinstance(budget, Budget)
        with budget.attempt() as reservation:
            self.prompts.append(prompt)
            reservation.settle(0.0)
        sink = (properties or {}).get("usage_sink")
        if isinstance(sink, list):
            cast("list[Usage]", sink).append(Usage(model="scripted"))
        return text


def kind(prompt: str) -> str:
    if "Prepare a structured data request" in prompt:
        return "bootstrap"
    if "Propose one useful partition" in prompt:
        return "proposal"
    if "Enumerate every entity" in prompt:
        return "entities"
    return "fetch"


def _filters_line(prompt: str) -> str:
    for line in prompt.splitlines():
        if line.startswith(("Filters:", "Column filters:", "Current filters:")):
            return line
    return ""


def partitioned_answer(prompt: str) -> str:
    """A year split with range-shaped values, then a region split under the first year."""
    step = kind(prompt)
    if step == "bootstrap":
        return json.dumps(
            {
                "ask": "retrieval wording",
                "constraints": {},
                "row_estimate": 120,
                "schema": {
                    "attrs": [
                        {"name": "name", "type": "string"},
                        {"name": "year", "type": "integer"},
                        {"name": "region", "type": "string"},
                    ],
                    "key": "name",
                },
            }
        )
    filters = _filters_line(prompt)
    if step == "proposal" and '"year"' not in filters:
        return json.dumps(
            {
                "key": "year",
                "values": ["1946–1952", "1953-1960"],
                "ambiguity": 0,
                "child_estimates": [60, 40],
            }
        )
    if step == "proposal":
        return json.dumps(
            {
                "key": "region",
                "values": ["North", "South"],
                "ambiguity": 0,
                "child_estimates": [30, 30],
            }
        )
    if '"North"' in filters:
        return json.dumps(
            [
                {"name": "Alpha", "year": 1947, "region": "North"},
                {"name": "Bravo", "year": 1950, "region": "south"},
            ]
        )
    if '"South"' in filters:
        return json.dumps(
            [
                {"name": "Charlie", "year": 1949, "region": "South"},
                {"name": "Delta", "year": 1970, "region": "South"},
            ]
        )
    return json.dumps(
        [
            {"name": "Echo", "year": 1955, "region": "North"},
            {"name": "Foxtrot", "year": 1940, "region": "South"},
        ]
    )


def row_by_row_answer(prompt: str) -> str:
    step = kind(prompt)
    if step == "bootstrap":
        return json.dumps(
            {
                "ask": "retrieval wording",
                "constraints": {},
                "row_estimate": 2,
                "schema": {
                    "attrs": [
                        {"name": "name", "type": "string"},
                        {"name": "country", "type": "string"},
                    ],
                    "key": "name",
                },
            }
        )
    if step == "entities":
        return json.dumps({"keys": ["name"], "values": [{"name": "Orly"}, {"name": "Narita"}]})
    if '"Orly"' in _filters_line(prompt):
        return json.dumps([{"name": "Orly", "country": "France"}])
    return json.dumps(
        [{"name": "Narita", "country": "Japan"}, {"name": "Haneda", "country": "Japan"}]
    )


def flat_answer(_prompt: str) -> str:
    return json.dumps([{"name": "Orly", "country": "France"}])


_TYPED = [
    {"name": "name", "type": "string"},
    {"name": "year", "type": "integer"},
    {"name": "region", "type": "string"},
]
_PLACES = [{"name": "name", "type": "string"}, {"name": "country", "type": "string"}]

SCENARIOS: dict[str, tuple[Callable[[str], str], dict[str, Any], list[dict[str, str]]]] = {
    "partitioned": (partitioned_answer, {"plan.mode": "partitioned"}, _TYPED),
    "row_by_row": (row_by_row_answer, {"plan.mode": "row_by_row"}, _PLACES),
    "flat": (flat_answer, {"plan.mode": "flat"}, _PLACES),
}


def run_scenario(
    name: str, settings: dict[str, Any] | None = None
) -> tuple[ScriptedClient, FetchResult]:
    responder, base, attrs = SCENARIOS[name]
    client = ScriptedClient(responder)
    result = LlmSource(
        properties={"plan.planning_order": "planning.combined"}, client=client
    ).fetch("records by year", attrs=attrs, properties={**base, **(settings or {})})
    return client, result


def digest(client: ScriptedClient, result: FetchResult) -> str:
    """One hash over every prompt sent and every published fact the switches could move."""
    payload = {
        "prompts": client.prompts,
        "records": result.records,
        "outcome": result.meta.outcome,
        "gaps": result.meta.gaps,
        "calls": result.meta.calls,
        "record_origins": result.meta.record_origins,
        "stop_reasons": result.meta.stop_reasons,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()
