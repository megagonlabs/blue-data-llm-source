from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import asdict, fields, replace
from typing import Any, cast, get_type_hints

import pytest

import blue_data_llm_source.source as source_module
from blue_data_llm_source import LlmSource, PlanningReceipt
from blue_data_llm_source.planning.coordinator import (
    PlanningBundle,
    PlanningFacts,
    coordinate_plan,
)
from blue_data_llm_source.planning.partition_safety import IdentityPartitionRule
from blue_data_llm_source.planning.plan import Gap, Note, root_plan
from blue_data_llm_source.types import (
    ConfigError,
    ExecMeta,
    FetchResult,
    PlanningReceiptProtocol,
)
from tests.doubles import ScriptedClient

KEYS = ("Console", "Game")
ATTRS = [
    {"name": "Console", "type": "string"},
    {"name": "Game", "type": "string"},
    {"name": "Sales", "type": "number"},
]


def _rule(
    dimension: str,
    domain: tuple[str, ...],
    provenance: str,
    *,
    scope: dict[str, object] | None = None,
) -> dict[str, object]:
    payload = IdentityPartitionRule(
        dimension=dimension,
        ordered_primary_keys=KEYS,
        allowed_domain=domain,
        provenance=provenance,
    ).to_dict()
    if scope is not None:
        payload["parent_scope"] = scope
    return payload


def _bootstrap() -> str:
    return json.dumps(
        {
            "ask": "games by console",
            "constraints": {},
            "row_estimate": 2,
            "schema": {"attrs": ATTRS, "primary_keys": list(KEYS)},
        }
    )


def _distribution(dimension: str, values: tuple[str, ...]) -> str:
    return json.dumps(
        {
            "key": dimension,
            "partitions": [{"value": value, "estimate": {"expected": 1}} for value in values],
            "ambiguity": 0,
            "reasoning": "finite identity domain",
        }
    )


def _classic_partition() -> str:
    return json.dumps(
        {
            "key": "Console",
            "values": ["Switch", "PS4"],
            "ambiguity": 0,
            "child_estimates": [1, 1],
        }
    )


def _source_properties(*rules: dict[str, object]) -> dict[str, object]:
    return {
        "plan.mode": "partitioned",
        "plan.planning_order": "planning.combined",
        "plan.distribution_search": True,
        "plan.identity_partitions": list(rules),
        "plan.keys": list(KEYS),
        "plan.max_depth": 1,
        "plan.max_continuations": 0,
        "limits.max_retries": 0,
    }


def _fetch_responses() -> list[str]:
    return [
        '[{"Console": "Switch", "Game": "Zelda", "Sales": 1}]',
        '[{"Console": "PS4", "Game": "Journey", "Sales": 2}]',
    ]


def _without_latency(value: ExecMeta) -> dict[str, object]:
    result = asdict(value)
    result["latency_seconds"] = 0.0
    return result


def test_disabled_distribution_settings_leave_the_source_run_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shared = {
        "plan.mode": "partitioned",
        "plan.planning_order": "planning.combined",
        "plan.keys": list(KEYS),
        "plan.max_depth": 1,
        "limits.max_retries": 0,
    }
    responses = [_bootstrap(), _classic_partition(), *_fetch_responses()]
    baseline_client = ScriptedClient(responses)
    configured_client = ScriptedClient(responses)
    plans: list[dict[str, object]] = []

    async def capture(**kwargs: Any) -> PlanningBundle:
        bundle = await coordinate_plan(**kwargs)
        plans.append(bundle.plan.to_dict())
        return bundle

    monkeypatch.setattr(source_module, "coordinate_plan", capture)
    baseline = LlmSource(properties=shared, client=baseline_client).fetch("games", attrs=ATTRS)
    configured = LlmSource(
        properties={
            **shared,
            "plan.distribution_search": False,
            "plan.identity_partitions": [
                _rule("Console", ("Switch", "PS4"), "source_configuration")
            ],
            "plan.max_strategy_candidates": 1,
        },
        client=configured_client,
    ).fetch("games", attrs=ATTRS)

    assert configured.records == baseline.records
    assert _without_latency(configured.meta) == _without_latency(baseline.meta)
    assert configured_client.prompts == baseline_client.prompts
    assert plans[1] == plans[0]
    assert configured.planning_receipt is None
    assert configured.to_dict() == asdict(configured)
    assert "planning_receipt" not in configured.to_dict()
    assert set(vars(configured)) == {"records", "meta"}


def test_source_distribution_search_executes_composite_identity_plan_and_receipt() -> None:
    rule = _rule("Console", ("Switch", "PS4"), "source_configuration")
    client = ScriptedClient(
        [_bootstrap(), _distribution("Console", ("Switch", "PS4")), *_fetch_responses()]
    )
    result = LlmSource(properties=_source_properties(rule), client=client).fetch(
        "games", attrs=ATTRS
    )

    assert result.records == [
        {"Console": "Switch", "Game": "Zelda", "Sales": 1},
        {"Console": "PS4", "Game": "Journey", "Sales": 2},
    ]
    assert result.meta.primary_keys == list(KEYS)
    assert result.meta.calls == 4
    receipt = result.planning_receipt
    assert receipt is not None
    assert isinstance(receipt, PlanningReceipt)
    assert receipt.configured_rule_digests == (IdentityPartitionRule.from_dict(rule).digest,)
    assert receipt.facts.selected_partition_key == "Console"
    restored = PlanningReceipt.from_dict(json.loads(json.dumps(receipt.to_dict())))
    assert restored == receipt
    assert [leaf.id for leaf in restored.plan.leaves] == ["0.0", "0.1"]
    assert [item.name for item in fields(result)] == ["records", "meta"]
    assert set(asdict(result)) == {"records", "meta"}
    assert result.to_dict()["planning_receipt"] == receipt.to_dict()
    wrong_version = receipt.to_dict()
    wrong_version["contract_version"] = "planning-receipt-999"
    with pytest.raises(ConfigError, match="contract version"):
        PlanningReceipt.from_dict(wrong_version)
    extra_field = receipt.to_dict()
    extra_field["extra"] = True
    with pytest.raises(ConfigError, match="fields"):
        PlanningReceipt.from_dict(extra_field)
    for seen in client.seen_properties:
        assert seen is not None
        assert "plan.distribution_search" not in seen
        assert "plan.identity_partitions" not in seen
        assert "plan.max_strategy_candidates" not in seen


def test_receipt_summarizes_the_last_applied_decision_after_a_later_rejection() -> None:
    console = _rule("Console", ("Switch", "PS4"), "source_configuration")
    game = _rule(
        "Game",
        ("Zelda", "Journey"),
        "source_configuration",
        scope={"Console": {"kind": "exact", "value": "Switch"}},
    )
    client = ScriptedClient(
        [
            _bootstrap(),
            json.dumps(
                {
                    "key": "Console",
                    "partitions": [
                        {"value": "Switch", "estimate": {"expected": 300}},
                        {"value": "PS4", "estimate": {"expected": 300}},
                    ],
                    "ambiguity": 0,
                    "reasoning": "finite identity domain",
                }
            ),
            _distribution("Game", ("Zelda", "Journey")),
            '[{"Console": "PS4", "Game": "Journey", "Sales": 2}]',
            '[{"Console": "Switch", "Game": "Zelda", "Sales": 1}]',
            '[{"Console": "Switch", "Game": "Journey", "Sales": 1}]',
        ]
    )
    result = LlmSource(
        properties={**_source_properties(console, game), "plan.max_depth": 2},
        client=client,
    ).fetch("games", attrs=ATTRS)

    receipt = result.planning_receipt
    assert receipt is not None
    assert isinstance(receipt, PlanningReceipt)
    last = receipt.plan.applied_partition_decisions[-1]
    assert last.rule.dimension == "Game"
    assert receipt.facts.selected_partition_key == last.rule.dimension
    assert receipt.facts.partition_rule_digest == last.rule.digest
    assert receipt.facts.partition_safety_status == "accepted"


@pytest.mark.parametrize("mode", ["flat", "row_by_row"])
def test_distribution_search_rejects_unsupported_modes_before_sync_provider_contact(
    mode: str,
) -> None:
    rule = _rule("Console", ("Switch", "PS4"), "source_configuration")
    client = ScriptedClient([])

    with pytest.raises(ConfigError, match="requires plan.mode='partitioned'"):
        LlmSource(properties={**_source_properties(rule), "plan.mode": mode}, client=client).fetch(
            "games", attrs=ATTRS
        )

    assert client.prompts == []


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["flat", "row_by_row"])
async def test_distribution_search_rejects_unsupported_modes_before_async_provider_contact(
    mode: str,
) -> None:
    rule = _rule("Console", ("Switch", "PS4"), "source_configuration")
    client = ScriptedClient([])

    with pytest.raises(ConfigError, match="requires plan.mode='partitioned'"):
        await LlmSource(
            properties={**_source_properties(rule), "plan.mode": mode}, client=client
        ).afetch("games", attrs=ATTRS)

    assert client.prompts == []


def _receipt_with_two_configured_rules() -> tuple[PlanningReceipt, str]:
    console = _rule("Console", ("Switch", "PS4"), "source_configuration")
    game = _rule("Game", ("Zelda", "Journey"), "source_configuration")
    client = ScriptedClient(
        [_bootstrap(), _distribution("Console", ("Switch", "PS4")), *_fetch_responses()]
    )
    result = LlmSource(
        properties={
            **_source_properties(console, game),
            "plan.max_strategy_candidates": 1,
        },
        client=client,
    ).fetch("games", attrs=ATTRS)
    receipt = result.planning_receipt
    assert receipt is not None
    assert isinstance(receipt, PlanningReceipt)
    return receipt, IdentityPartitionRule.from_dict(game).digest


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("proposal_attempts", 0),
        ("accepted_internal_nodes", 0),
        ("strategy_candidates_considered", 0),
        ("distribution_attempts", 0),
        ("distribution_nodes_attempted", 0),
        ("usable_strategy_candidates", 0),
        ("selected_partition_key", "Game"),
        ("partition_safety_status", "rejected"),
        ("entity_enumeration_attempted", True),
        ("proposal_attempt_limit", 0),
        ("distribution_parse_failures", 1),
        ("distribution_nodes_attempted", 2),
    ],
)
def test_receipt_rejects_each_fact_that_contradicts_an_accepted_plan(
    field: str, replacement: object
) -> None:
    receipt, _ = _receipt_with_two_configured_rules()
    payload = receipt.to_dict()
    facts = cast("dict[str, object]", payload["planning_facts"])
    facts[field] = replacement

    with pytest.raises(ConfigError, match="planning receipt"):
        PlanningReceipt.from_dict(payload)


def test_receipt_rejects_a_configured_digest_that_is_not_the_last_applied_rule() -> None:
    receipt, unused_digest = _receipt_with_two_configured_rules()
    payload = receipt.to_dict()
    facts = cast("dict[str, object]", payload["planning_facts"])
    facts["partition_rule_digest"] = unused_digest

    with pytest.raises(ConfigError, match="last decision"):
        PlanningReceipt.from_dict(payload)


@pytest.mark.parametrize("mode", ["flat", "row_by_row"])
def test_receipt_rejects_a_nonpartitioned_embedded_plan(mode: str) -> None:
    receipt, _ = _receipt_with_two_configured_rules()
    payload = receipt.to_dict()
    plan = cast("dict[str, object]", payload["fetch_plan"])
    plan["mode"] = mode

    with pytest.raises(ConfigError, match="partitioned fetch plan"):
        PlanningReceipt.from_dict(payload)


def test_receipt_allows_a_consistent_rejected_no_split_source_result() -> None:
    rule_payload = _rule("Console", ("Switch",), "source_configuration")
    client = ScriptedClient([_bootstrap(), '[{"Console": "Switch", "Game": "Zelda", "Sales": 1}]'])
    result = LlmSource(properties=_source_properties(rule_payload), client=client).fetch(
        "games", attrs=ATTRS
    )

    receipt = result.planning_receipt
    assert receipt is not None
    assert isinstance(receipt, PlanningReceipt)
    assert receipt.plan.applied_partition_decisions == ()
    assert receipt.facts.partition_safety_status == "rejected"
    assert PlanningReceipt.from_dict(receipt.to_dict()) == receipt


def test_receipt_allows_a_consistent_unknown_no_split_plan() -> None:
    rule = IdentityPartitionRule.from_dict(
        _rule("Console", ("Switch", "PS4"), "source_configuration")
    )
    plan = replace(
        root_plan(
            mode="partitioned",
            original_query="games",
            retrieval_query="games",
            constraints={},
            estimate=None,
        ),
        attempted_proposals=1,
        notes=[Note(code=Gap.GUARD_COLLAPSED, target_id="0", detail="no matching rule")],
    )
    receipt = PlanningReceipt(
        configured_rule_digests=(rule.digest,),
        facts=PlanningFacts(
            distribution_search_enabled=True,
            proposal_attempts=1,
            proposal_attempt_limit=1,
            partition_safety_status="unknown",
        ),
        plan=plan,
    )

    assert PlanningReceipt.from_dict(receipt.to_dict()) == receipt


def test_fetch_result_runtime_types_and_default_serialization_keep_the_old_shape() -> None:
    result = FetchResult()

    assert set(get_type_hints(FetchResult)) == {"records", "meta"}
    getter = FetchResult.planning_receipt.fget
    assert getter is not None
    assert get_type_hints(getter)["return"] == PlanningReceiptProtocol | None
    attacher_hints = get_type_hints(FetchResult.with_planning_receipt)
    assert attacher_hints["receipt"] == PlanningReceiptProtocol | None
    assert attacher_hints["return"] is FetchResult
    assert [item.name for item in fields(result)] == ["records", "meta"]
    assert result.to_dict() == asdict(result)
    assert "planning_receipt" not in result.to_dict()


def _malformed_cases() -> list[tuple[str, dict[str, object]]]:
    valid = _rule("Console", ("Switch", "PS4"), "source_configuration")
    cases: list[tuple[str, dict[str, object]]] = []
    for label, field, value in (
        ("unknown field", "extra", True),
        ("version", "contract_version", "identity-partition-rule-999"),
        ("kind", "kind", "heuristic"),
        ("key order", "ordered_primary_keys", ["Game", "Console"]),
        ("dimension", "dimension", "Sales"),
        ("provenance", "provenance", "caller_declared"),
        ("domain", "allowed_domain", ["Switch", "Switch"]),
        (
            "parent scope",
            "parent_scope",
            {"Console": {"kind": "one_of", "values": "Switch"}},
        ),
    ):
        changed = deepcopy(valid)
        changed[field] = value
        cases.append((label, _source_properties(changed)))
    missing = deepcopy(valid)
    missing.pop("kind")
    cases.append(("missing field", _source_properties(missing)))
    cases.extend(
        [
            (
                "flag type",
                {**_source_properties(valid), "plan.distribution_search": 1},
            ),
            (
                "candidate minimum",
                {**_source_properties(valid), "plan.max_strategy_candidates": 0},
            ),
            (
                "keys required",
                {
                    key: value
                    for key, value in _source_properties(valid).items()
                    if key != "plan.keys"
                },
            ),
            (
                "root rule required",
                _source_properties(
                    _rule(
                        "Game",
                        ("Zelda", "Journey"),
                        "source_configuration",
                        scope={"Console": {"kind": "exact", "value": "Switch"}},
                    )
                ),
            ),
        ]
    )
    return cases


@pytest.mark.parametrize(("label", "properties"), _malformed_cases())
def test_malformed_distribution_configuration_fails_before_provider_contact(
    label: str, properties: dict[str, object]
) -> None:
    client = ScriptedClient([])
    with pytest.raises(ConfigError):
        LlmSource(properties=properties, client=client).fetch("games", attrs=ATTRS)
    assert client.prompts == [], label


def test_per_fetch_rules_replace_source_rules_and_require_caller_provenance() -> None:
    source_rule = _rule("Console", ("Switch", "PS4"), "source_configuration")
    override_rule = _rule("Game", ("Zelda", "Journey"), "caller_declared")
    client = ScriptedClient(
        [_bootstrap(), _distribution("Game", ("Zelda", "Journey")), *_fetch_responses()]
    )
    result = LlmSource(properties=_source_properties(source_rule), client=client).fetch(
        "games",
        attrs=ATTRS,
        properties={"plan.identity_partitions": [override_rule]},
    )

    receipt = result.planning_receipt
    assert receipt is not None
    assert isinstance(receipt, PlanningReceipt)
    assert receipt.configured_rule_digests == (
        IdentityPartitionRule.from_dict(override_rule).digest,
    )
    distribution_prompt = next(
        prompt for prompt in client.prompts if prompt.startswith("Estimate the distribution")
    )
    assert 'Declared partition key: "Game"' in distribution_prompt
    assert 'Declared partition key: "Console"' not in distribution_prompt

    wrong_origin = deepcopy(override_rule)
    wrong_origin["provenance"] = "source_configuration"
    untouched = ScriptedClient([])
    with pytest.raises(ConfigError, match="caller_declared"):
        LlmSource(properties=_source_properties(source_rule), client=untouched).fetch(
            "games",
            attrs=ATTRS,
            properties={"plan.identity_partitions": [wrong_origin]},
        )
    assert untouched.prompts == []


class _ConfiguredClient(ScriptedClient):
    def __init__(self, properties: dict[str, object]) -> None:
        super().__init__([])
        self.properties = properties


def test_provider_client_cannot_inject_identity_partition_rules() -> None:
    injected = _rule("Console", ("Switch", "PS4"), "source_configuration")
    client = _ConfiguredClient({"plan.identity_partitions": [injected]})
    with pytest.raises(ConfigError, match="provider client"):
        LlmSource(client=client).fetch("games", attrs=ATTRS)
    assert client.prompts == []


def test_candidate_cap_and_budget_bound_the_public_distribution_path() -> None:
    rules = (
        _rule("Console", ("Switch", "PS4"), "source_configuration"),
        _rule("Game", ("Zelda", "Journey"), "source_configuration"),
    )
    client = ScriptedClient(
        [_bootstrap(), _distribution("Console", ("Switch", "PS4")), *_fetch_responses()]
    )
    properties = {
        **_source_properties(*rules),
        "plan.max_strategy_candidates": 1,
        "limits.max_calls": 4,
    }
    result = LlmSource(properties=properties, client=client).fetch("games", attrs=ATTRS)

    assert result.meta.calls == 4
    assert (
        len([prompt for prompt in client.prompts if prompt.startswith("Estimate the distribution")])
        == 1
    )
    assert result.meta.stopped_by is None
    assert result.planning_receipt is not None


def test_estimate_remains_the_flat_bound_when_distribution_is_configured() -> None:
    rule = _rule("Console", ("Switch", "PS4"), "source_configuration")
    baseline = LlmSource(
        properties={
            "llm.model": "gpt-4o-mini",
            "llm.max_tokens": 64,
            "limits.max_retries": 0,
        }
    ).estimate("games", attrs=ATTRS)
    configured = LlmSource(
        properties={
            "llm.model": "gpt-4o-mini",
            "llm.max_tokens": 64,
            **_source_properties(rule),
        }
    ).estimate("games", attrs=ATTRS)

    assert configured == baseline
