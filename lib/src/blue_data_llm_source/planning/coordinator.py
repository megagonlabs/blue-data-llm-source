"""Async planning for the fetch task tree."""

from __future__ import annotations

import math
from collections.abc import Hashable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any, cast

from blue_data_llm_source import _constants as C
from blue_data_llm_source import prompts
from blue_data_llm_source.llm.budget import Budget
from blue_data_llm_source.planning.analysis import (
    BootstrapAnalysis,
    QueryAnalysisResult,
)
from blue_data_llm_source.planning.analysis import (
    intersect_constraints as _intersect_constraints,
)
from blue_data_llm_source.planning.bounds import (
    MODE_FLAT,
    MODE_PARTITIONED,
    MODE_ROW_BY_ROW,
    MODES,
    PlanLimits,
    distribution_node_attempt_limit,
    proposal_attempt_limit,
)
from blue_data_llm_source.planning.cardinality import CardinalityEstimate, estimate_from_reported
from blue_data_llm_source.planning.context import (
    PLANNING_COMBINED,
    PlanningContext,
    validate_planning_order,
)
from blue_data_llm_source.planning.distribution import (
    DistributionEstimator,
    StrategyCandidate,
    StrategyEvaluator,
)
from blue_data_llm_source.planning.partition_safety import (
    SAFETY_REJECTED,
    SAFETY_UNKNOWN,
    AppliedPartitionDecision,
    IdentityPartitionRule,
    PartitionSafety,
    evaluate_partition_safety,
)
from blue_data_llm_source.planning.plan import (
    VALUES_FROM_ESTIMATOR,
    FetchPlan,
    Gap,
    Leaf,
    Note,
    PartitionPolicy,
    PartitionProposal,
    PlanSchemaContract,
    apply_partition_proposal,
    build_entity_plan,
    build_key_tuple_plan,
    partition_keys,
    root_plan,
)
from blue_data_llm_source.planning.schedules import (
    build_planning_context,
    call_planning_model,
    constraint_payload,
    extraction_fits,
    fallback_analysis,
    parse_json_object,
    planning_lookahead,
    planning_usages,
    schema_payload,
)
from blue_data_llm_source.planning.schema import (
    SCHEMA_ENRICH,
    SCHEMA_MODES,
    Schema,
    SchemaDefinition,
)
from blue_data_llm_source.planning.split_gate import (
    DEFAULT_SPLIT_THRESHOLD_ROWS,
    PROBE_ACCEPTED,
    SPLIT_ALWAYS,
    SPLIT_EVIDENCE,
    SPLIT_POLICIES,
    ProbeOutcome,
    Prober,
    decide,
    held_from_records,
)
from blue_data_llm_source.types import (
    Attr,
    ConfigError,
    Constraint,
    Exact,
    OneOf,
    Usage,
    value_identity,
)

_MAX_MODEL_STRING = 4_096

intersect_constraints = _intersect_constraints


def _require_distribution_search(value: object) -> bool:
    if not isinstance(value, bool):
        raise ConfigError("distribution_search must be a boolean")
    return value


def _carry_distribution_estimates(
    plan: FetchPlan,
    *,
    parent_id: str,
    key: str,
    estimates: Mapping[Hashable, CardinalityEstimate],
) -> FetchPlan:
    leaves: list[Leaf] = []
    for leaf in plan.leaves:
        constraint = leaf.constraints.get(key)
        estimate = (
            estimates.get(value_identity(constraint.value))
            if leaf.parent_id == parent_id and isinstance(constraint, Exact)
            else None
        )
        leaves.append(replace(leaf, cardinality=estimate) if estimate is not None else leaf)
    return replace(plan, leaves=leaves)


@dataclass(frozen=True)
class PlanningFacts:
    bootstrap_attempted: bool = False
    proposal_attempts: int = 0
    accepted_internal_nodes: int = 0
    entity_enumeration_attempted: bool = False
    proposal_attempt_limit: int = 0
    planning_order: str = PLANNING_COMBINED
    analysis_calls: int = 0
    schema_calls: int = 0
    cardinality_calls: int = 0
    combined_calls: int = 0
    context_digest: str | None = None
    distribution_search_enabled: bool = False
    strategy_candidates_considered: int = 0
    distribution_attempts: int = 0
    distribution_nodes_attempted: int = 0
    distribution_parse_failures: int = 0
    usable_strategy_candidates: int = 0
    selected_partition_key: str | None = None
    partition_safety_status: str | None = None
    partition_rule_digest: str | None = None
    parent_child_estimate_disagreement: bool | None = None


@dataclass(frozen=True)
class PlanningBundle:
    plan: FetchPlan
    analysis: BootstrapAnalysis | QueryAnalysisResult
    schema: Schema
    facts: PlanningFacts
    context: PlanningContext | None = None
    usages: list[Usage] = field(default_factory=list[Usage])
    diagnostics: list[str] = field(default_factory=list[str])
    probe: ProbeOutcome | None = None
    """The root probe under ``split_policy="evidence"``, or None when none ran."""


def partition_proposal_from_response(
    text: str, limits: PlanLimits
) -> tuple[PartitionProposal | None, str | None]:
    payload = parse_json_object(text)
    if payload is None:
        return None, "partition response was not one JSON object"
    if (
        not {"key", "values", "ambiguity"}
        <= set(payload)
        <= {
            "key",
            "values",
            "ambiguity",
            "child_estimates",
        }
    ):
        return None, "partition response fields did not match the contract"
    key = payload.get("key")
    values = payload.get("values")
    ambiguity = payload.get("ambiguity")
    estimates = payload.get("child_estimates")
    if not isinstance(key, str) or not key.strip() or len(key) > _MAX_MODEL_STRING:
        return None, "partition key was not a bounded non-empty string"
    if not isinstance(values, list):
        return None, "partition values were not a list"
    raw_values = cast("list[Any]", values)
    if len(raw_values) > limits.max_partitions:
        return None, "partition response exceeded max_partitions"
    cleaned: list[str] = []
    for raw in raw_values:
        if not isinstance(raw, str) or not raw.strip() or len(raw) > _MAX_MODEL_STRING:
            return None, "partition values contained an invalid string"
        cleaned.append(raw.strip())
    if (
        isinstance(ambiguity, bool)
        or not isinstance(ambiguity, (int, float))
        or not 0 <= float(ambiguity) <= 1
    ):
        return None, "partition ambiguity was not within 0..1"
    if "child_estimates" in payload and not isinstance(estimates, list):
        return None, "partition child_estimates was not a list"
    parsed_estimates: list[int | None] = []
    reported_estimates = cast("list[Any]", estimates) if isinstance(estimates, list) else []
    for index in range(len(cleaned)):
        estimate = reported_estimates[index] if index < len(reported_estimates) else None
        parsed_estimates.append(
            estimate
            if estimate is None
            or (not isinstance(estimate, bool) and isinstance(estimate, int) and estimate >= 0)
            else None
        )
    return (
        PartitionProposal(
            key=key.strip(),
            values=cleaned,
            ambiguity=float(ambiguity),
            child_estimates=tuple(parsed_estimates),
        ),
        None,
    )


def _entities_from_response(
    text: str, keys: tuple[str, ...], attrs: list[Attr], limit: int
) -> list[dict[str, Any]] | None:
    payload = parse_json_object(text)
    if payload is None:
        return None
    legacy = len(keys) == 1 and set(payload) == {"key", "values"} and payload.get("key") == keys[0]
    reported_keys: object = payload.get("keys")
    current = (
        set(payload) == {"keys", "values"}
        and isinstance(reported_keys, list)
        and reported_keys == list(keys)
    )
    if not legacy and not current:
        return None
    reported_values: object = payload.get("values")
    if not isinstance(reported_values, list):
        return None
    raw_values = cast("list[Any]", reported_values)
    if len(raw_values) > limit:
        return None
    out: list[dict[str, Any]] = []
    for raw in raw_values:
        if legacy and isinstance(raw, str):
            raw = {keys[0]: raw}
        if not isinstance(raw, dict):
            return None
        item = cast("dict[str, Any]", raw)
        if set(item) != set(keys):
            return None
        clean_values: list[Any] = []
        for key in keys:
            value = item.get(key)
            if value is None or isinstance(value, (list, dict)):
                return None
            if isinstance(value, str):
                if not value.strip() or len(value) > _MAX_MODEL_STRING:
                    return None
                value = value.strip()
            elif isinstance(value, float) and not math.isfinite(value):
                return None
            elif not isinstance(value, (bool, int, float)):
                return None
            clean_values.append(value)
        out.append(dict(zip(keys, clean_values, strict=True)))
    return out or None


def _ancestor_keys(plan: FetchPlan, leaf: Leaf) -> list[str]:
    nodes = {node.id: node for node in plan.nodes}
    out: list[str] = []
    parent_id = leaf.parent_id
    while parent_id is not None and parent_id in nodes:
        parent = nodes[parent_id]
        out.append(parent.key)
        parent_id = parent.parent_id
    out.reverse()
    return out


def _eligible(leaf: Leaf, limits: PlanLimits) -> bool:
    if leaf.depth >= limits.max_depth:
        return False
    if limits.max_partitions < 2 or limits.max_leaves == 1:
        return False
    return leaf.depth == 0 or leaf.estimate is None or leaf.estimate > limits.target_rows_per_leaf


def _fetch_prompt(
    leaf: Leaf,
    schema: Schema,
    caller_context: str,
    leaf_prompt: str,
    plan: FetchPlan | None = None,
    probed: ProbeOutcome | None = None,
) -> str:
    """The leaf prompt execution will send, priced before anything is committed to it.

    Built under the contract execution will use. Pricing one contract and running another
    would let a longer prompt through an affordability check made for a shorter one. *plan*
    is the plan *leaf* belongs to, which says which of its constraints partitioning
    introduced; a root leaf needs none. *probed* is the root probe, whose held keys every
    leaf below the root is sent.
    """
    prompt = prompts.build_leaf_fetch_prompt(
        original_query=leaf.original_query,
        retrieval_query=leaf.query,
        attrs=schema.attrs,
        caller_context=caller_context,
        constraints=leaf.constraints,
        primary_keys=schema.primary_keys,
        estimate=leaf.cardinality.to_dict() if leaf.cardinality is not None else None,
        prompt_version=leaf_prompt,
        partition_keys=partition_keys(plan, leaf) if plan is not None else (),
    )
    if probed is None or leaf.parent_id is None:
        return prompt
    return prompts.with_held(prompt, probed.held, probed.held_total)


def _rejected_attempt(plan: FetchPlan, leaf: Leaf, reason: str) -> FetchPlan:
    return replace(
        plan,
        notes=[*plan.notes, Note(code=Gap.GUARD_COLLAPSED, target_id=leaf.id, detail=reason)],
        attempted_proposals=plan.attempted_proposals + 1,
    )


def _partition_decision(leaf: Leaf, candidate: StrategyCandidate) -> AppliedPartitionDecision:
    if candidate.rule is None:
        raise ConfigError("selected distribution strategy did not retain its identity rule")
    return AppliedPartitionDecision(
        node_id=leaf.id,
        rule=candidate.rule,
        safety=candidate.safety,
    )


def _planning_facts(context: PlanningContext, limits: PlanLimits) -> PlanningFacts:
    attempted = {stage.name: stage.attempted for stage in context.stages}
    return PlanningFacts(
        bootstrap_attempted=attempted.get("bootstrap", False),
        proposal_attempt_limit=proposal_attempt_limit(limits),
        planning_order=context.schedule,
        analysis_calls=int(attempted.get("analysis", False)),
        schema_calls=int(attempted.get("schema", False)),
        cardinality_calls=int(attempted.get("cardinality", False)),
        combined_calls=int(attempted.get("bootstrap", False)),
        context_digest=context.digest,
    )


async def coordinate_plan(
    *,
    mode: str,
    original_query: str,
    caller_context: str,
    caller_constraints: Mapping[str, Constraint],
    declared_schema: Schema,
    schema_mode: str,
    limits: PlanLimits,
    client: object,
    budget: Budget,
    properties: Mapping[str, Any] | None = None,
    partition_key: str | None = None,
    planning_order: str = PLANNING_COMBINED,
    distribution_search: bool = False,
    identity_partition_rules: Sequence[IdentityPartitionRule] = (),
    leaf_prompt: str = prompts.KEYED_LEAF_FETCH_PROMPT_VERSION,
    partition_policy: PartitionPolicy | None = None,
    split_policy: str = SPLIT_ALWAYS,
    split_threshold_rows: int = DEFAULT_SPLIT_THRESHOLD_ROWS,
    probe: Prober | None = None,
    probe_exact_rows: int | None = None,
) -> PlanningBundle:
    """Build planning facts and expand a bounded plan.

    *leaf_prompt* is the leaf contract execution will send. It is read here only to price the
    prompts the plan commits to; it does not change which plan is built. *partition_policy*
    decides how an accepted proposal becomes child leaves; its defaults retain the parent
    wording and use exact-value filters.
    """
    policy = partition_policy or PartitionPolicy()
    if leaf_prompt not in prompts.LEAF_PROMPT_VERSIONS:
        raise ConfigError(
            f"unknown leaf prompt {leaf_prompt!r}; expected one of "
            f"{', '.join(prompts.LEAF_PROMPT_VERSIONS)}"
        )
    if mode not in MODES:
        raise ConfigError(f"unknown mode {mode!r}; expected one of {', '.join(MODES)}")
    if split_policy not in SPLIT_POLICIES:
        raise ConfigError(
            f"unknown split policy {split_policy!r}; expected one of {', '.join(SPLIT_POLICIES)}"
        )
    if split_policy != SPLIT_ALWAYS and mode != MODE_PARTITIONED:
        raise ConfigError("split_policy='evidence' requires partitioned mode")
    if (
        isinstance(split_threshold_rows, bool)
        or not isinstance(split_threshold_rows, int)  # pyright: ignore[reportUnnecessaryIsInstance]
        or split_threshold_rows < 0
    ):
        raise ConfigError("split_threshold_rows must be a non-negative integer")
    if schema_mode not in SCHEMA_MODES:
        raise ConfigError(f"unknown schema mode {schema_mode!r}")
    if mode == MODE_FLAT and schema_mode == SCHEMA_ENRICH:
        raise ConfigError("flat mode cannot enrich schema without a planning call")
    if partition_key is not None and not partition_key.strip():
        raise ConfigError("partition_key must be a non-empty string")
    distribution_search = _require_distribution_search(distribution_search)
    schedule = validate_planning_order(planning_order)
    diagnostics: list[str] = []
    props = dict(properties or {})
    supplied_sink = props.get(C.USAGE_SINK_PROPERTY)
    usages: list[Usage] = (
        cast("list[Usage]", supplied_sink) if isinstance(supplied_sink, list) else []
    )
    usage_start = len(usages)
    props[C.BUDGET_PROPERTY] = budget
    props[C.USAGE_SINK_PROPERTY] = usages
    base_analysis = fallback_analysis(original_query, caller_constraints)
    protected = root_plan(
        mode=mode,
        original_query=original_query,
        retrieval_query=original_query,
        constraints=caller_constraints,
        estimate=None,
    )
    if any(isinstance(value, OneOf) and not value.values for value in caller_constraints.values()):
        diagnostics.append("an explicit empty constraint makes the request unsatisfiable")
        return PlanningBundle(
            plan=protected,
            analysis=base_analysis,
            schema=declared_schema,
            facts=PlanningFacts(
                proposal_attempt_limit=proposal_attempt_limit(limits),
                planning_order=schedule,
            ),
            diagnostics=diagnostics,
        )
    if mode == MODE_FLAT:
        return PlanningBundle(
            plan=protected,
            analysis=base_analysis,
            schema=declared_schema,
            facts=PlanningFacts(
                proposal_attempt_limit=proposal_attempt_limit(limits),
                planning_order=schedule,
            ),
        )

    context, context_usages = await build_planning_context(
        original_query=original_query,
        caller_context=caller_context,
        caller_constraints=caller_constraints,
        declared_schema=declared_schema,
        schema_mode=schema_mode,
        client=client,
        budget=budget,
        properties=props,
        planning_order=schedule,
        extraction_prompt=_fetch_prompt(
            protected.leaves[0], declared_schema, caller_context, leaf_prompt
        ),
    )
    diagnostics.extend(context.diagnostics)
    analysis = context.analysis
    schema = context.schema.to_schema()
    base_facts = replace(
        _planning_facts(context, limits),
        distribution_search_enabled=distribution_search,
    )
    strategy_schema = SchemaDefinition(
        attrs=context.schema.attrs,
        primary_keys=context.schema.primary_keys,
        key_from=context.schema.key_from,
        identity_partitions=tuple(identity_partition_rules),
    )

    bootstrap = next((stage for stage in context.stages if stage.name == "bootstrap"), None)
    if schedule == PLANNING_COMBINED and (bootstrap is None or not bootstrap.parsed):
        return PlanningBundle(
            plan=protected,
            analysis=base_analysis,
            schema=declared_schema,
            facts=base_facts,
            context=context,
            usages=context_usages,
            diagnostics=diagnostics,
        )

    prospective = root_plan(
        mode=mode,
        original_query=original_query,
        retrieval_query=analysis.ask,
        constraints=analysis.constraints,
        estimate=analysis.row_estimate,
        cardinality=analysis.cardinality,
    )
    if extraction_fits(
        client,
        budget,
        props,
        _fetch_prompt(prospective.leaves[0], schema, caller_context, leaf_prompt),
    ):
        plan = prospective
    else:
        diagnostics.append(
            "planning facts made extraction unaffordable; using the original request"
        )
        return PlanningBundle(
            plan=protected,
            analysis=base_analysis,
            schema=declared_schema,
            facts=base_facts,
            context=context,
            usages=context_usages,
            diagnostics=diagnostics,
        )

    if any(
        isinstance(value, OneOf) and not value.values for value in analysis.constraints.values()
    ):
        diagnostics.append("bootstrap returned an explicit empty constraint")
        return PlanningBundle(
            plan=plan,
            analysis=analysis,
            schema=schema,
            facts=base_facts,
            context=context,
            usages=context_usages,
            diagnostics=diagnostics,
        )

    if mode == MODE_ROW_BY_ROW:
        return await _coordinate_entities(
            original_query=original_query,
            caller_context=caller_context,
            analysis=analysis,
            schema=schema,
            plan=plan,
            limits=limits,
            client=client,
            budget=budget,
            properties=props,
            usages=usages,
            usage_start=usage_start,
            diagnostics=diagnostics,
            context=context,
            base_facts=base_facts,
            leaf_prompt=leaf_prompt,
        )

    probed: ProbeOutcome | None = None
    if split_policy == SPLIT_EVIDENCE and _eligible(plan.leaves[0], limits):
        # Before any proposal: the root is asked once, and its answer decides whether a
        # proposal is worth making. A root that cannot split is not probed, because the leaf
        # that would run instead is the same ask.
        if probe is None:
            raise ConfigError("split_policy='evidence' needs a probe to run the root leaf")
        root = plan.leaves[0]
        run = await probe(root, schema)
        if run.budget_denied and run.exception is not None:
            # Refused before it could answer: nothing is known, and a split would need more.
            raise run.exception
        decision = decide(run, root.cardinality, split_threshold_rows, probe_exact_rows)
        held, held_total = (
            held_from_records(
                run.records,
                schema.primary_keys,
                schema.attrs,
                max_excluded_keys=limits.max_excluded_keys,
                max_exclusion_bytes=limits.max_exclusion_bytes,
            )
            if decision != PROBE_ACCEPTED
            else ((), 0)
        )
        probed = ProbeOutcome(run=run, decision=decision, held=held, held_total=held_total)
        if decision == PROBE_ACCEPTED:
            return PlanningBundle(
                plan=plan,
                analysis=analysis,
                schema=schema,
                facts=base_facts,
                context=context,
                usages=planning_usages(usages, usage_start),
                diagnostics=diagnostics,
                probe=probed,
            )

    frontier = [plan.leaves[0]]
    candidates_considered = 0
    distribution_attempts = 0
    distribution_nodes_attempted = 0
    distribution_parse_failures = 0
    usable_candidates = 0
    selected_key: str | None = None
    selected_safety: PartitionSafety | None = None
    selected_disagreement: bool | None = None
    while frontier and (
        distribution_nodes_attempted < distribution_node_attempt_limit(limits)
        if distribution_search
        else plan.attempted_proposals < proposal_attempt_limit(limits)
    ):
        current = frontier.pop(0)
        leaf = next((item for item in plan.leaves if item.id == current.id), None)
        if leaf is None or not _eligible(leaf, limits):
            continue
        ancestor_keys = _ancestor_keys(plan, leaf)
        if distribution_search:
            applicable_rules = [
                rule
                for rule in strategy_schema.identity_partitions
                if rule.applies_to(leaf.constraints)
                and rule.dimension not in ancestor_keys
                and not (
                    leaf.depth == 0
                    and partition_key is not None
                    and rule.dimension != partition_key
                )
            ]
            key_order = {key: index for index, key in enumerate(strategy_schema.primary_keys)}
            attr_order = {attr.name: index for index, attr in enumerate(strategy_schema.attrs)}
            rules = sorted(
                applicable_rules,
                key=lambda rule: (
                    key_order.get(rule.dimension, len(key_order)),
                    attr_order.get(rule.dimension, len(attr_order)),
                    rule.dimension,
                ),
            )[: limits.max_strategy_candidates]
            if rules:
                distribution_nodes_attempted += 1
            candidates_considered += len(rules)
            candidates: list[StrategyCandidate] = []
            last_safety: PartitionSafety | None = None
            for rule in rules:
                declared_type = next(
                    (attr.type for attr in strategy_schema.attrs if attr.name == rule.dimension),
                    None,
                )
                safety = evaluate_partition_safety(
                    rule=rule,
                    dimension=rule.dimension,
                    values=rule.allowed_domain,
                    ordered_primary_keys=strategy_schema.primary_keys,
                    parent_constraints=leaf.constraints,
                    declared_type=declared_type,
                )
                last_safety = safety
                if not safety.accepted:
                    continue
                if not 2 <= len(rule.allowed_domain) <= limits.max_partitions:
                    last_safety = PartitionSafety(
                        status=SAFETY_REJECTED,
                        containment=safety.containment,
                        disjointness=safety.disjointness,
                        coverage=safety.coverage,
                        rule_digest=safety.rule_digest,
                        evidence_code="partition_width_rejected",
                        reasons=(
                            "declared identity domain is outside the configured partition width",
                        ),
                    )
                    continue
                distribution_prompt = prompts.build_distribution_prompt(
                    original_query=original_query,
                    retrieval_query=leaf.query,
                    caller_context=caller_context,
                    schema=schema_payload(schema),
                    constraints=constraint_payload(leaf.constraints),
                    partition_key=rule.dimension,
                    domain=rule.allowed_domain,
                    parent_estimate=(
                        leaf.cardinality.to_dict() if leaf.cardinality is not None else None
                    ),
                )
                if not planning_lookahead(
                    client=client,
                    budget=budget,
                    properties=props,
                    planning_prompt=distribution_prompt,
                    fallback_prompt=_fetch_prompt(
                        leaf, schema, caller_context, leaf_prompt, plan, probed
                    ),
                ):
                    diagnostics.append(f"{leaf.id}: distribution skipped to preserve extraction")
                    break
                distribution_attempts += 1
                response, was_truncated = await call_planning_model(
                    client, distribution_prompt, props, usages
                )
                stats = DistributionEstimator.parse(
                    response if not was_truncated else None,
                    rule=rule,
                    parent=leaf.cardinality
                    or estimate_from_reported(
                        leaf.estimate,
                        row_unit="records in this leaf",
                        source="planner",
                    ),
                    declared_type=declared_type,
                )
                if stats.parse_status != "parsed":
                    distribution_parse_failures += 1
                    diagnostics.extend(f"{leaf.id}: {item}" for item in stats.diagnostics)
                    continue
                if stats.ambiguity_score > limits.ambiguity_threshold:
                    diagnostics.append(
                        f"{leaf.id}: distribution ambiguity exceeded the configured threshold"
                    )
                    continue
                candidate = StrategyEvaluator.candidate(
                    schema=strategy_schema,
                    stats=stats,
                    safety=safety,
                    rule=rule,
                )
                candidates.append(candidate)
            usable_candidates += len(candidates)
            selected = StrategyEvaluator.select(candidates)
            if selected is None:
                selected_safety = last_safety or PartitionSafety(
                    status=SAFETY_UNKNOWN,
                    containment=None,
                    disjointness=None,
                    coverage=None,
                    rule_digest=None,
                    evidence_code="identity_rule_missing",
                    reasons=("no identity partition rule matched the task",),
                )
                reason = "; ".join(selected_safety.reasons) or "no usable distribution"
                plan = _rejected_attempt(plan, leaf, reason)
                diagnostics.append(f"{leaf.id}: {reason}")
                continue
            selected_key = selected.partition_key
            selected_safety = selected.safety
            selected_disagreement = (
                selected.stats.parent_expected_delta != 0
                if selected.stats.parent_expected_delta is not None
                else None
            )
            proposal = PartitionProposal(
                key=selected.partition_key,
                values=tuple(part.value for part in selected.stats.partitions),
                values_from=VALUES_FROM_ESTIMATOR,
                ambiguity=selected.stats.ambiguity_score,
                child_estimates=tuple(part.estimate.expected for part in selected.stats.partitions),
                partition_safety_status=selected.safety.status,
                partition_rule_digest=selected.safety.rule_digest,
            )
            candidate_plan = apply_partition_proposal(
                plan,
                leaf_id=leaf.id,
                proposal=proposal,
                schema=schema,
                limits=limits,
                applied_decision=_partition_decision(leaf, selected),
                schema_contract=PlanSchemaContract.from_schema(schema),
                policy=policy,
            )
            typed_child_estimates = {
                value_identity(part.value): part.estimate for part in selected.stats.partitions
            }
            candidate_plan = _carry_distribution_estimates(
                candidate_plan,
                parent_id=leaf.id,
                key=selected.partition_key,
                estimates=typed_child_estimates,
            )
            if candidate_plan.accepted_internal_nodes == plan.accepted_internal_nodes:
                plan = candidate_plan
                continue
            children = [item for item in candidate_plan.leaves if item.parent_id == leaf.id]
            if not any(
                extraction_fits(
                    client,
                    budget,
                    props,
                    _fetch_prompt(
                        item, schema, caller_context, leaf_prompt, candidate_plan, probed
                    ),
                )
                for item in children
            ):
                plan = _rejected_attempt(plan, leaf, "generated child prompts were unaffordable")
                diagnostics.append(f"{leaf.id}: expansion rejected after repricing")
                continue
            plan = candidate_plan
            frontier.extend(children)
            continue
        proposal_prompt = prompts.build_partition_prompt(
            original_query=original_query,
            retrieval_query=leaf.query,
            caller_context=caller_context,
            schema=schema_payload(schema),
            constraints=constraint_payload(leaf.constraints),
            ancestor_keys=ancestor_keys,
            preferred_key=partition_key if leaf.depth == 0 else None,
            max_partitions=limits.max_partitions,
            prompt_version=(
                prompts.GUARDED_PARTITION_PROMPT_VERSION
                if policy.partition_guardrails
                else prompts.PARTITION_PROMPT_VERSION
            ),
            parent_expected=(
                leaf.cardinality.expected if leaf.cardinality is not None else leaf.estimate
            ),
            target_rows_per_leaf=limits.target_rows_per_leaf,
        )
        if not planning_lookahead(
            client=client,
            budget=budget,
            properties=props,
            planning_prompt=proposal_prompt,
            fallback_prompt=_fetch_prompt(leaf, schema, caller_context, leaf_prompt, plan, probed),
        ):
            diagnostics.append(f"{leaf.id}: proposal skipped to preserve extraction")
            break
        response, was_truncated = await call_planning_model(client, proposal_prompt, props, usages)
        if response is not None and not was_truncated:
            proposal, proposal_failure = partition_proposal_from_response(response, limits)
        elif was_truncated:
            proposal, proposal_failure = None, "partition response was truncated"
        else:
            proposal, proposal_failure = None, "partition response was unavailable"
        if (
            leaf.depth == 0
            and partition_key is not None
            and proposal is not None
            and proposal.key != partition_key
        ):
            diagnostics.append(
                f"{leaf.id}: proposal key {proposal.key!r} did not match "
                f"requested {partition_key!r}"
            )
            proposal_failure = "partition key did not match the requested key"
            proposal = None
        candidate = apply_partition_proposal(
            plan,
            leaf_id=leaf.id,
            proposal=proposal,
            schema=schema,
            limits=limits,
            proposal_failure=proposal_failure,
            policy=policy,
        )
        if candidate.accepted_internal_nodes == plan.accepted_internal_nodes:
            plan = candidate
            continue
        children = [item for item in candidate.leaves if item.parent_id == leaf.id]
        if not any(
            extraction_fits(
                client,
                budget,
                props,
                _fetch_prompt(item, schema, caller_context, leaf_prompt, candidate, probed),
            )
            for item in children
        ):
            plan = _rejected_attempt(plan, leaf, "generated child prompts were unaffordable")
            diagnostics.append(f"{leaf.id}: expansion rejected after repricing")
            continue
        plan = candidate
        frontier.extend(children)

    last_decision = (
        plan.applied_partition_decisions[-1] if plan.applied_partition_decisions else None
    )
    facts = replace(
        base_facts,
        proposal_attempts=plan.attempted_proposals,
        accepted_internal_nodes=plan.accepted_internal_nodes,
        distribution_search_enabled=distribution_search,
        strategy_candidates_considered=candidates_considered,
        distribution_attempts=distribution_attempts,
        distribution_nodes_attempted=distribution_nodes_attempted,
        distribution_parse_failures=distribution_parse_failures,
        usable_strategy_candidates=usable_candidates,
        selected_partition_key=(
            last_decision.rule.dimension if last_decision is not None else selected_key
        ),
        partition_safety_status=(
            last_decision.safety.status
            if last_decision is not None
            else (selected_safety.status if selected_safety else None)
        ),
        partition_rule_digest=(
            last_decision.rule.digest
            if last_decision is not None
            else (selected_safety.rule_digest if selected_safety else None)
        ),
        parent_child_estimate_disagreement=selected_disagreement,
    )
    return PlanningBundle(
        plan=plan,
        analysis=analysis,
        schema=schema,
        facts=facts,
        context=context,
        usages=planning_usages(usages, usage_start),
        diagnostics=diagnostics,
        probe=probed,
    )


async def _coordinate_entities(
    *,
    original_query: str,
    caller_context: str,
    analysis: BootstrapAnalysis | QueryAnalysisResult,
    schema: Schema,
    plan: FetchPlan,
    limits: PlanLimits,
    client: object,
    budget: Budget,
    properties: dict[str, Any],
    usages: list[Usage],
    usage_start: int,
    diagnostics: list[str],
    context: PlanningContext,
    base_facts: PlanningFacts,
    leaf_prompt: str,
) -> PlanningBundle:
    keys = schema.primary_keys
    key = schema.key
    explicit = analysis.constraints.get(key) if key is not None else None
    attempted = False
    if not keys:
        diagnostics.append("row-by-row planning has no identified entity key")
        entity_plan = plan
    elif len(keys) == 1 and key is not None and isinstance(explicit, OneOf) and explicit.values:
        entity_plan = build_entity_plan(
            original_query=original_query,
            retrieval_query=analysis.ask,
            constraints=analysis.constraints,
            key=key,
            entities=list(explicit.values),
            limits=limits,
        )
    elif len(keys) == 1 and key is not None and isinstance(explicit, Exact):
        entity_plan = build_entity_plan(
            original_query=original_query,
            retrieval_query=analysis.ask,
            constraints=analysis.constraints,
            key=key,
            entities=[explicit.value],
            limits=limits,
        )
    else:
        entity_prompt = prompts.build_entity_prompt(
            original_query=original_query,
            retrieval_query=analysis.ask,
            caller_context=caller_context,
            keys=keys,
            constraints=constraint_payload(analysis.constraints),
        )
        if not planning_lookahead(
            client=client,
            budget=budget,
            properties=properties,
            planning_prompt=entity_prompt,
            fallback_prompt=_fetch_prompt(plan.leaves[0], schema, caller_context, leaf_prompt),
        ):
            diagnostics.append("entity enumeration skipped to preserve batch extraction")
            entity_plan = plan
        else:
            attempted = True
            response, truncated = await call_planning_model(
                client, entity_prompt, properties, usages
            )
            entities = (
                _entities_from_response(response, keys, list(schema.attrs), limits.max_leaves)
                if response is not None and not truncated
                else None
            )
            entity_plan = (
                build_key_tuple_plan(
                    original_query=original_query,
                    retrieval_query=analysis.ask,
                    constraints=analysis.constraints,
                    primary_keys=keys,
                    attrs=schema.attrs,
                    entities=entities,
                    limits=limits,
                )
                if entities is not None
                else plan
            )
            if entities is None:
                diagnostics.append("entity enumeration was unusable; using one batch leaf")
            elif not any(
                extraction_fits(
                    client,
                    budget,
                    properties,
                    _fetch_prompt(leaf, schema, caller_context, leaf_prompt, entity_plan),
                )
                for leaf in entity_plan.leaves
            ):
                diagnostics.append("entity prompts were unaffordable; using one batch leaf")
                entity_plan = plan

    facts = replace(
        base_facts,
        accepted_internal_nodes=entity_plan.accepted_internal_nodes,
        entity_enumeration_attempted=attempted,
    )
    return PlanningBundle(
        plan=entity_plan,
        analysis=analysis,
        schema=schema,
        facts=facts,
        context=context,
        usages=planning_usages(usages, usage_start),
        diagnostics=diagnostics,
    )
