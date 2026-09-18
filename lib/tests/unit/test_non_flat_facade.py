"""Non-flat planning and execution through the public facade."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any, cast

import pytest

import blue_data_llm_source.source as source_module
from blue_data_llm_source.llm.budget import Budget, Limits
from blue_data_llm_source.llm.client import DefaultLlmClient
from blue_data_llm_source.planning.analysis import BootstrapAnalysis
from blue_data_llm_source.planning.coordinator import PlanningBundle, PlanningFacts
from blue_data_llm_source.planning.plan import FetchPlan, Leaf, Node
from blue_data_llm_source.planning.schema import KEY_FROM_INFERRED, Schema
from blue_data_llm_source.planning.transport import LeafTask, LeafTaskResult, LocalLeafExecutor
from blue_data_llm_source.source import LlmSource
from blue_data_llm_source.types import Attr, Capability, ConfigError, Exact, Outcome, Usage
from tests.doubles import LitellmStub, MemoryCache, chat_payload

Action = str | tuple[str, str] | BaseException
Responder = Callable[[str], Action]


def _free_attempt(_prompt: str) -> float:
    return 0.0


class RoutedClient:
    """Record calls and answer from prompt-aware actions."""

    def __init__(
        self,
        responder: Responder,
        bound: Callable[[str], float] | None = None,
    ) -> None:
        self.responder = responder
        self.bound = bound or _free_attempt
        self.prompts: list[str] = []

    def budget_capabilities(self) -> frozenset[str]:
        return Capability.ALL

    def bound_for_attempt(self, _prompt: str, _properties: dict[str, Any]) -> float:
        return self.bound(_prompt)

    def complete(self, prompt: str, properties: dict[str, Any] | None = None) -> str:
        return self._respond(prompt, properties)

    async def acomplete(self, prompt: str, properties: dict[str, Any] | None = None) -> str:
        return self._respond(prompt, properties)

    def _respond(self, prompt: str, properties: dict[str, Any] | None) -> str:
        action = self.responder(prompt)
        if isinstance(action, BaseException):
            raise action
        budget = (properties or {}).get("budget")
        assert isinstance(budget, Budget)
        with budget.attempt() as reservation:
            self.prompts.append(prompt)
            reservation.settle(0.0)
        text, finish_reason = action if isinstance(action, tuple) else (action, None)
        usage = Usage(model="routed", finish_reason=finish_reason)
        sink = (properties or {}).get("usage_sink")
        if isinstance(sink, list):
            cast("list[Usage]", sink).append(usage)
        return text


def _bootstrap(
    *,
    attrs: list[dict[str, object]] | None = None,
    key: str | None = "name",
    constraints: dict[str, object] | None = None,
    estimate: int | None = 120,
) -> str:
    return json.dumps(
        {
            "ask": "retrieval wording",
            "constraints": constraints or {},
            "row_estimate": estimate,
            "schema": {"attrs": attrs or [{"name": "name"}], "key": key},
        }
    )


def _kind(prompt: str) -> str:
    if "Prepare a structured data request" in prompt:
        return "bootstrap"
    if "Propose one useful partition" in prompt:
        return "proposal"
    if "Enumerate every entity" in prompt:
        return "entities"
    return "fetch"


def test_a_real_two_level_tree_keeps_every_parent_slice() -> None:
    def answer(prompt: str) -> Action:
        kind = _kind(prompt)
        if kind == "bootstrap":
            return _bootstrap()
        filters = (
            prompt.split("Current filters:", 1)[1].splitlines()[0] if kind == "proposal" else ""
        )
        if kind == "proposal" and '"continent"' not in filters:
            return json.dumps(
                {
                    "key": "continent",
                    "values": ["Asia", "Europe"],
                    "ambiguity": 0,
                    "child_estimates": [100, 1],
                }
            )
        if kind == "proposal":
            return json.dumps(
                {
                    "key": "country",
                    "values": ["Japan", "Korea"],
                    "ambiguity": 0,
                    "child_estimates": [1, 1],
                }
            )
        if '"Japan"' in prompt:
            return '[{"name": "Japan row"}]'
        if '"Korea"' in prompt:
            return '[{"name": "Korea row"}]'
        return '[{"name": "Europe row"}]'

    client = RoutedClient(answer)
    result = LlmSource(
        properties={"plan.planning_order": "planning.combined"}, client=client
    ).fetch(
        "airports by region",
        attrs=["name"],
        properties={"plan.mode": "partitioned", "plan.partition_key": "continent"},
    )

    assert [row["name"] for row in result.records] == [
        "Japan row",
        "Korea row",
        "Europe row",
    ]
    assert result.meta.record_origins == [["0.0.0"], ["0.0.1"], ["0.1"]]
    assert result.meta.asked == "retrieval wording"
    assert sum(_kind(prompt) == "proposal" for prompt in client.prompts) == 2


@pytest.mark.parametrize(
    ("attrs", "schema_mode", "expected", "requested"),
    [
        (None, "preserve", {"name": "Orly", "country": "France"}, []),
        (["name"], "preserve", {"name": "Orly"}, ["name"]),
        (
            ["name"],
            "enrich",
            {"name": "Orly", "country": "France"},
            ["name"],
        ),
    ],
)
def test_effective_schema_reaches_prompt_and_final_shape(
    attrs: list[str] | None,
    schema_mode: str,
    expected: dict[str, str],
    requested: list[str],
) -> None:
    def answer(prompt: str) -> Action:
        if _kind(prompt) == "bootstrap":
            return _bootstrap(attrs=[{"name": "name"}, {"name": "country"}])
        return '[{"name": "Orly", "country": "France"}]'

    client = RoutedClient(answer)
    result = LlmSource(
        properties={"plan.planning_order": "planning.combined"}, client=client
    ).fetch(
        "airport",
        attrs=attrs,
        properties={
            "plan.mode": "partitioned",
            "plan.max_depth": 0,
            "plan.schema_mode": schema_mode,
        },
    )

    assert result.records == [expected]
    assert result.meta.requested_attrs == requested
    fetch_prompt = next(prompt for prompt in client.prompts if _kind(prompt) == "fetch")
    assert all(name in fetch_prompt for name in expected)


@pytest.mark.parametrize(("estimate", "expected"), [(200, 200), (0, 0), (None, None)])
def test_root_row_estimate_is_reported_when_bootstrap_is_accepted(
    estimate: int | None, expected: int | None
) -> None:
    def answer(prompt: str) -> Action:
        if _kind(prompt) == "bootstrap":
            return _bootstrap(estimate=estimate)
        return '[{"name": "Orly"}]'

    result = LlmSource(
        properties={"plan.planning_order": "planning.combined"}, client=RoutedClient(answer)
    ).fetch(
        "airport",
        attrs=["name"],
        properties={"plan.mode": "partitioned", "plan.max_depth": 0},
    )

    assert result.meta.row_estimate == expected


def test_exact_rows_stops_a_single_leaf_without_a_continuation() -> None:
    def answer(prompt: str) -> Action:
        if _kind(prompt) == "bootstrap":
            return _bootstrap()
        if "Already returned:" in prompt:
            return '[{"name": "Lyon"}]'
        return '[{"name": "Paris"}]'

    client = RoutedClient(answer)
    result = LlmSource(
        properties={"plan.planning_order": "planning.combined"}, client=client
    ).fetch(
        "French cities",
        attrs=["name"],
        properties={
            "plan.mode": "partitioned",
            "plan.max_depth": 0,
            "plan.max_continuations": 2,
            "plan.exact_rows": 1,
        },
    )

    assert result.records == [{"name": "Paris"}]
    assert sum(_kind(prompt) == "fetch" for prompt in client.prompts) == 1
    assert not any("exact_rows_not_leaf_bound" in gap for gap in result.meta.gaps)


def test_exact_rows_is_not_applied_as_a_global_multi_leaf_target() -> None:
    def answer(prompt: str) -> Action:
        kind = _kind(prompt)
        if kind == "bootstrap":
            return _bootstrap()
        if kind == "proposal":
            return json.dumps(
                {
                    "key": "region",
                    "values": ["North", "South"],
                    "ambiguity": 0,
                    "child_estimates": [1, 1],
                }
            )
        if '"North"' in prompt:
            return '[{"name": "Lille"}]'
        return '[{"name": "Nice"}]'

    result = LlmSource(
        properties={"plan.planning_order": "planning.combined"},
        client=RoutedClient(answer),
    ).fetch(
        "French cities by region",
        attrs=["name"],
        properties={
            "plan.mode": "partitioned",
            "plan.partition_key": "region",
            "plan.max_continuations": 0,
            "plan.exact_rows": 1,
        },
    )

    assert result.records == [{"name": "Lille"}, {"name": "Nice"}]
    assert any("exact_rows_not_leaf_bound" in gap for gap in result.meta.gaps)


def test_flat_and_discarded_bootstrap_report_unknown_row_estimate() -> None:
    flat = LlmSource(
        properties={"plan.planning_order": "planning.combined"},
        client=RoutedClient(lambda _prompt: '[{"name": "Orly"}]'),
    ).fetch("airport", attrs=["name"])
    assert flat.meta.row_estimate is None

    client = RoutedClient(
        lambda prompt: "not json" if _kind(prompt) == "bootstrap" else '[{"name": "Orly"}]'
    )
    fallback = LlmSource(
        properties={"plan.planning_order": "planning.combined"}, client=client
    ).fetch("airport", attrs=["name"], properties={"plan.mode": "partitioned"})
    assert fallback.meta.row_estimate is None


@pytest.mark.parametrize(
    ("root_values", "root_estimates", "constraints"),
    [
        (["Asia", "Asia", "Europe"], [1, 1, 100], None),
        (["Other", "Asia", "Europe"], [999, 1, 100], {"region": ["Asia", "Europe"]}),
    ],
)
def test_normalized_root_estimates_make_the_right_descendant_eligible(
    root_values: list[str],
    root_estimates: list[int],
    constraints: dict[str, object] | None,
) -> None:
    def answer(prompt: str) -> Action:
        kind = _kind(prompt)
        if kind == "bootstrap":
            return _bootstrap(constraints=constraints)
        if kind == "proposal" and '"value": "Europe"' not in prompt:
            return json.dumps(
                {
                    "key": "region",
                    "values": root_values,
                    "ambiguity": 0,
                    "child_estimates": root_estimates,
                }
            )
        if kind == "proposal":
            return json.dumps(
                {
                    "key": "country",
                    "values": ["France", "Germany"],
                    "ambiguity": 0,
                    "child_estimates": [1, 1],
                }
            )
        return '[{"name": "row"}]'

    client = RoutedClient(answer)
    result = LlmSource(
        properties={"plan.planning_order": "planning.combined"}, client=client
    ).fetch(
        "airports",
        attrs=["name"],
        properties={"plan.mode": "partitioned", "plan.max_depth": 2},
    )

    assert result.records == [{"name": "row"}]
    proposal_prompts = [prompt for prompt in client.prompts if _kind(prompt) == "proposal"]
    assert len(proposal_prompts) == 2
    assert '"value": "Europe"' in proposal_prompts[1]
    assert result.meta.record_origins == [["0.0", "0.1.0", "0.1.1"]]


def test_planning_output_limit_is_diagnostic_only() -> None:
    def answer(prompt: str) -> Action:
        if _kind(prompt) == "bootstrap":
            return (_bootstrap(), "length")
        return '[{"name": "Orly"}]'

    result = LlmSource(
        properties={"plan.planning_order": "planning.combined"}, client=RoutedClient(answer)
    ).fetch(
        "airport",
        attrs=["name"],
        properties={"plan.mode": "partitioned"},
    )

    assert result.records == [{"name": "Orly"}]
    assert result.meta.outcome == Outcome.OK
    assert result.meta.stop_reasons == []
    assert any("planning_output_limit" in gap for gap in result.meta.gaps)


def test_no_key_keeps_partitioned_leaves_without_continuation() -> None:
    def answer(prompt: str) -> Action:
        if _kind(prompt) == "bootstrap":
            return _bootstrap(key=None)
        if _kind(prompt) == "proposal":
            return json.dumps(
                {
                    "key": "region",
                    "values": ["North", "South"],
                    "ambiguity": 0,
                    "child_estimates": [1, 1],
                }
            )
        return '[{"name": "Orly"}]'

    client = RoutedClient(answer)
    result = LlmSource(
        properties={"plan.planning_order": "planning.combined"}, client=client
    ).fetch(
        "airport",
        attrs=["name"],
        properties={"plan.mode": "partitioned", "plan.max_continuations": 2},
    )

    assert result.records == [{"name": "Orly"}]
    assert sum(_kind(prompt) == "fetch" for prompt in client.prompts) == 2
    assert result.meta.record_origins == [["0.0", "0.1"]]
    assert any("key_unavailable" in gap for gap in result.meta.gaps)


def test_partial_known_value_split_keeps_the_complete_parent() -> None:
    countries = ["France", "Germany", "Italy"]

    def answer(prompt: str) -> Action:
        if _kind(prompt) == "bootstrap":
            return _bootstrap(constraints={"country": countries})
        if _kind(prompt) == "proposal":
            return json.dumps(
                {
                    "key": "country",
                    "values": ["France", "Italy"],
                    "ambiguity": 0,
                    "child_estimates": [1, 1],
                }
            )
        return '[{"name": "parent row"}]'

    client = RoutedClient(answer)
    result = LlmSource(
        properties={"plan.planning_order": "planning.combined"}, client=client
    ).fetch(
        "airports in three countries",
        attrs=["name"],
        properties={"plan.mode": "partitioned", "plan.partition_key": "country"},
    )

    assert result.records == [{"name": "parent row"}]
    fetches = [prompt for prompt in client.prompts if _kind(prompt) == "fetch"]
    assert len(fetches) == 1
    assert all(country in fetches[0] for country in countries)
    assert any("does not cover every known value" in gap for gap in result.meta.gaps)


def test_explicit_empty_constraint_skips_extraction() -> None:
    client = RoutedClient(
        lambda prompt: (
            _bootstrap(constraints={"country": []})
            if _kind(prompt) == "bootstrap"
            else pytest.fail("an unsatisfiable request reached extraction")
        )
    )
    result = LlmSource(
        properties={"plan.planning_order": "planning.combined"}, client=client
    ).fetch(
        "airports nowhere",
        attrs=["name"],
        properties={"plan.mode": "partitioned"},
    )

    assert result.records == []
    assert result.meta.outcome == Outcome.MODEL_RETURNED_NONE
    assert [_kind(prompt) for prompt in client.prompts] == ["bootstrap"]


def test_property_caps_change_the_public_plan() -> None:
    def answer(prompt: str) -> Action:
        if _kind(prompt) == "bootstrap":
            return _bootstrap()
        if _kind(prompt) == "proposal":
            return json.dumps(
                {
                    "key": "region",
                    "values": ["North", "South"],
                    "ambiguity": 0,
                    "child_estimates": [1, 1],
                }
            )
        return '[{"name": "row"}]'

    shallow = RoutedClient(answer)
    LlmSource(properties={"plan.planning_order": "planning.combined"}, client=shallow).fetch(
        "airport",
        attrs=["name"],
        properties={"plan.mode": "partitioned", "plan.max_depth": 0},
    )
    assert not any(_kind(prompt) == "proposal" for prompt in shallow.prompts)

    capped = RoutedClient(answer)
    LlmSource(properties={"plan.planning_order": "planning.combined"}, client=capped).fetch(
        "airport",
        attrs=["name"],
        properties={"plan.mode": "partitioned", "plan.max_partitions": 1},
    )
    assert not any(_kind(prompt) == "proposal" for prompt in capped.prompts)

    one_leaf = RoutedClient(answer)
    LlmSource(properties={"plan.planning_order": "planning.combined"}, client=one_leaf).fetch(
        "airport",
        attrs=["name"],
        properties={"plan.mode": "partitioned", "plan.max_leaves": 1},
    )
    assert not any(_kind(prompt) == "proposal" for prompt in one_leaf.prompts)

    with pytest.raises(ConfigError, match="target_rows_per_leaf"):
        LlmSource(
            properties={"plan.planning_order": "planning.combined"}, client=RoutedClient(answer)
        ).fetch(
            "airport",
            properties={"plan.target_rows_per_leaf": 0},
        )


@pytest.mark.parametrize(("mode", "expected_estimate"), [("partitioned", None), ("row_by_row", 1)])
def test_generated_prompt_growth_keeps_the_affordable_parent(
    mode: str, expected_estimate: int | None
) -> None:
    huge = "Huge" * 200

    def answer(prompt: str) -> Action:
        if _kind(prompt) == "bootstrap":
            return _bootstrap(
                constraints={} if mode == "row_by_row" else {"scope": huge},
                estimate=1,
            )
        if _kind(prompt) == "entities":
            return json.dumps({"key": "name", "values": [huge]})
        return '[{"name": "parent"}]'

    client = RoutedClient(answer, bound=lambda prompt: 2.0 if huge in prompt else 0.1)
    result = LlmSource(
        properties={"plan.planning_order": "planning.combined"}, client=client
    ).fetch(
        "affordable parent",
        attrs=["name"],
        budget=Budget(Limits(max_cost=1.0, max_retries=0)),
        properties={"plan.mode": mode, "plan.max_depth": 0},
    )

    assert result.records == [{"name": "parent"}]
    assert result.meta.row_estimate == expected_estimate
    fetch_prompt = next(prompt for prompt in client.prompts if _kind(prompt) == "fetch")
    assert huge not in fetch_prompt
    assert any("unaffordable" in gap for gap in result.meta.gaps)


def test_task_round_trip_and_injected_executor_reject_wrong_identity() -> None:
    task = LeafTask.from_leaf(
        Leaf(
            id="0.0",
            parent_id="0",
            query="retrieval wording",
            constraints={"country": Exact("France")},
            original_query="airport",
        ),
        caller_context="civil airports",
        schema=Schema(attrs=[Attr("name")], key="name", key_from=KEY_FROM_INFERRED),
        settings={"model": "test"},
    )
    copied = LeafTask.from_dict(json.loads(json.dumps(task.to_dict())))
    assert copied == task

    async def wrong(_: LeafTask) -> LeafTaskResult:
        return LeafTaskResult(task_id="other")

    with pytest.raises(ConfigError, match="does not match"):
        import asyncio

        asyncio.run(LocalLeafExecutor(wrong).execute(copied))


def test_identical_leaf_reuses_provider_cache_across_distinct_trees(
    monkeypatch: pytest.MonkeyPatch,
    litellm_stub: Callable[[LitellmStub], LitellmStub],
    cache: MemoryCache,
) -> None:
    async def fixed(**kwargs: Any) -> PlanningBundle:
        hint = cast("str", kwargs["partition_key"])
        declared = cast("Schema", kwargs["declared_schema"])
        schema = Schema(attrs=declared.attrs, key="name", key_from=KEY_FROM_INFERRED)
        leaf = Leaf(
            id="0.0",
            parent_id="0",
            query="shared retrieval",
            constraints={"scope": Exact("shared")},
            original_query="shared request",
        )
        plan = FetchPlan(
            mode="partitioned",
            leaves=[leaf],
            nodes=[
                Node(
                    id="0",
                    parent_id=None,
                    query="shared retrieval",
                    constraints={},
                    key=hint,
                    key_in_schema=False,
                    values=["shared", "other"],
                    values_from="enumeration",
                )
            ],
        )
        return PlanningBundle(
            plan=plan,
            analysis=BootstrapAnalysis(ask="shared retrieval"),
            schema=schema,
            facts=PlanningFacts(bootstrap_attempted=True),
        )

    monkeypatch.setattr(source_module, "coordinate_plan", fixed)
    stub = litellm_stub(LitellmStub(chat_payload('[{"name": "same"}]')))
    client = DefaultLlmClient(
        {"cache_store": cache, "llm.model": "gpt-4o-mini", "llm.max_tokens": 64}
    )
    source = LlmSource(properties={"plan.planning_order": "planning.combined"}, client=client)

    first = source.fetch(
        "shared request",
        attrs=["name"],
        properties={"plan.mode": "partitioned", "plan.partition_key": "country"},
    )
    second = source.fetch(
        "shared request",
        attrs=["name"],
        properties={"plan.mode": "partitioned", "plan.partition_key": "year"},
    )

    assert first.records == second.records == [{"name": "same"}]
    assert second.meta.cache_hits == 1

    source.fetch(
        "shared request",
        attrs=["name", "country"],
        properties={"plan.mode": "partitioned", "plan.partition_key": "year"},
    )
    source.fetch(
        "shared request",
        attrs=["name"],
        properties={
            "plan.mode": "partitioned",
            "plan.partition_key": "year",
            "llm.model": "gpt-4o",
        },
    )
    assert len(stub.calls) == 3
