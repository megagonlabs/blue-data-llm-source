"""Composite identity and uncertain-cardinality runtime contracts."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any, cast

import pytest

from blue_data_llm_source import Attr, ConfigError, Exact, JsonScalar, LlmSource, OneOf
from blue_data_llm_source.execution.mapper import run_leaf
from blue_data_llm_source.planning.analysis import strict_constraints
from blue_data_llm_source.planning.bounds import PlanLimits
from blue_data_llm_source.planning.cardinality import (
    PARSE_INVALID,
    PARSE_PARSED,
    CardinalityEstimate,
    estimate_from_dict,
    estimate_from_reported,
)
from blue_data_llm_source.planning.plan import (
    ROOT,
    FetchPlan,
    Gap,
    Leaf,
    build_key_tuple_plan,
)
from blue_data_llm_source.planning.schema import (
    KEY_FROM_CALLER,
    KEY_FROM_NONE,
    Schema,
    schema_from_response,
)
from blue_data_llm_source.planning.transport import WIRE_VERSION, LeafTask
from blue_data_llm_source.prompts import (
    LEGACY_INITIAL_V2_PROMPT_VERSION,
    rendered_held_entries_size,
)
from blue_data_llm_source.reduction.coverage import LeafOutcome, reduce_leaves
from blue_data_llm_source.reduction.identity import key_identity

KEYS = ("Console", "Game")
ATTRS = [Attr("Console", "string"), Attr("Game", "string"), Attr("Year", "integer")]


class Script:
    def __init__(self, answers: list[list[dict[str, Any]]]) -> None:
        self.answers = answers
        self.prompts: list[str] = []

    def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        at = min(len(self.prompts) - 1, len(self.answers) - 1)
        return json.dumps(self.answers[at])


def returning(text: str) -> Callable[[str], str]:
    def answer(_prompt: str) -> str:
        return text

    return answer


def leaf(estimate: CardinalityEstimate | None = None) -> Leaf:
    return Leaf(
        id="0.0",
        parent_id=ROOT,
        query="games",
        original_query="games",
        constraints={},
        estimate=estimate.expected if estimate else None,
        cardinality=estimate,
    )


def schema() -> Schema:
    return Schema(attrs=ATTRS, primary_keys=KEYS, key_from=KEY_FROM_CALLER)


def test_declared_types_normalize_the_full_ordered_tuple_without_changing_display() -> None:
    first = {"Console": " PlayStation 2 ", "Game": "ICO", "Year": "2001"}
    alias = {"Console": "playstation 2", "Game": "ico", "Year": 2001}
    identity = key_identity(first, KEYS, ATTRS)
    assert identity == key_identity(alias, KEYS, ATTRS)
    assert identity is not None
    assert identity.display == (" PlayStation 2 ", "ICO")
    assert key_identity(first, tuple(reversed(KEYS)), ATTRS) != identity


def test_same_game_on_two_consoles_remains_two_identities() -> None:
    left = key_identity({"Console": "PS2", "Game": "Game"}, KEYS, ATTRS)
    right = key_identity({"Console": "Xbox", "Game": "Game"}, KEYS, ATTRS)
    assert left != right


@pytest.mark.parametrize("factor", [0.25, 0.5, 1.0, 2.0, 4.0, None])
def test_model_estimates_are_soft_across_perturbations(factor: float | None) -> None:
    expected = None if factor is None else int(4 * factor)
    estimate = CardinalityEstimate(
        expected=expected,
        row_unit="games",
        source="fixture",
        parse_status=PARSE_PARSED if expected is not None else "unknown",
    )
    model = Script(
        [
            [{"Console": "PS2", "Game": "A", "Year": 2001}],
            [{"Console": "PS2", "Game": "B", "Year": 2002}],
            [{"Console": "PS2", "Game": "C", "Year": 2003}],
            [{"Console": "PS2", "Game": "D", "Year": 2004}],
        ]
    )
    result = run_leaf(
        leaf(estimate),
        model,
        attrs=ATTRS,
        limits=PlanLimits(max_continuations=3),
        primary_keys=KEYS,
        estimate=estimate,
    )
    assert result.attempts == 4
    assert result.observed_key_count == 4
    assert result.marginal_key_yields == (1, 1, 1, 1)
    assert '"expected"' in model.prompts[0]


def test_trusted_exact_rows_is_the_only_row_count_that_hard_stops() -> None:
    model = Script(
        [
            [{"Console": "PS2", "Game": "A", "Year": 2001}],
            [{"Console": "PS2", "Game": "B", "Year": 2002}],
        ]
    )
    result = run_leaf(
        leaf(),
        model,
        attrs=ATTRS,
        limits=PlanLimits(max_continuations=3),
        primary_keys=KEYS,
        exact_rows=1,
    )
    assert result.attempts == 1
    assert result.observed_key_count == 1


def test_incomplete_keys_use_exact_record_repeat_suppression() -> None:
    incomplete = {"Console": "PS2", "Game": None, "Year": 2001}
    model = Script([[incomplete, incomplete]])
    result = run_leaf(
        leaf(),
        model,
        attrs=ATTRS,
        limits=PlanLimits(max_continuations=3),
        primary_keys=KEYS,
    )
    assert result.records == [incomplete]
    assert result.attempts == 1
    assert result.observed_key_count == 0
    assert result.incomplete_key_records == 1


def test_exclusions_are_complete_key_objects_and_large_ones_are_omitted_whole() -> None:
    huge = "x" * 200
    model = Script(
        [
            [{"Console": huge, "Game": "A", "Year": 2001}],
            [{"Console": "PS2", "Game": "B", "Year": 2002}],
        ]
    )
    run_leaf(
        leaf(),
        model,
        attrs=ATTRS,
        limits=PlanLimits(max_continuations=1, max_exclusion_bytes=64),
        primary_keys=KEYS,
    )
    continuation = model.prompts[1]
    assert huge not in continuation
    assert '"Console": "' not in continuation.split("Already returned:", 1)[1]


def test_reducer_merges_on_normalized_tuple_and_reports_display_collision_and_incomplete() -> None:
    plan = FetchPlan(mode="partitioned", leaves=[leaf(), Leaf("0.1", ROOT, "games", {})])
    records, coverage = reduce_leaves(
        plan,
        schema(),
        [
            (
                "0.0",
                [{"Console": "PS2", "Game": "ICO", "Year": 2001}],
                LeafOutcome("0.0"),
            ),
            (
                "0.1",
                [
                    {"Console": " ps2 ", "Game": "ico", "Year": 2002},
                    {"Console": "PS2", "Game": None, "Year": 2003},
                ],
                LeafOutcome("0.1"),
            ),
        ],
    )
    assert len(records) == 2
    codes = [note.code for note in coverage.gaps]
    assert Gap.DEDUP_NORMALIZATION_COLLISION in codes
    assert Gap.DEDUP_CONFLICT in codes
    assert Gap.DEDUP_KEY_ABSENT in codes


def test_leaf_task_writes_v2_with_ordered_keys_and_estimate_and_reads_v1() -> None:
    estimate = CardinalityEstimate(
        expected=10,
        lower=8,
        upper=12,
        row_unit="games",
        source="fixture",
        parse_status=PARSE_PARSED,
    )
    current = LeafTask.from_leaf(
        leaf(estimate), caller_context="", schema=schema(), settings={"max_continuations": 0}
    )
    payload = current.to_dict()
    assert payload["wire_version"] == WIRE_VERSION
    assert payload["primary_keys"] == ["Console", "Game"]
    assert payload["estimate"] == estimate.to_dict()
    assert LeafTask.from_dict(json.loads(json.dumps(payload))) == current

    legacy = {
        "wire_version": 1,
        "prompt_version": LEGACY_INITIAL_V2_PROMPT_VERSION,
        "task_id": "0",
        "parent_id": None,
        "original_query": "games",
        "retrieval_query": "games",
        "caller_context": "",
        "schema": {
            "attrs": [{"name": "Game", "type": "string"}],
            "key": "Game",
            "key_from": "caller",
        },
        "filters": {},
        "settings": {"max_continuations": 0},
    }
    restored = LeafTask.from_dict(legacy)
    assert restored.primary_keys == ("Game",)
    assert restored.to_dict()["wire_version"] == WIRE_VERSION


def test_cardinality_parser_preserves_interval_unit_source_status_and_diagnostics() -> None:
    parsed = estimate_from_reported(
        {"expected": 10, "lower": 8, "upper": 12, "row_unit": "games"},
        source="fixture",
    )
    assert parsed.to_dict() == {
        "expected": 10,
        "lower": 8,
        "upper": 12,
        "row_unit": "games",
        "source": "fixture",
        "parse_status": PARSE_PARSED,
        "diagnostics": [],
    }
    invalid = estimate_from_reported({"expected": 10, "lower": 11, "upper": 12}, source="fixture")
    assert invalid.parse_status == PARSE_INVALID
    assert invalid.expected is None
    assert invalid.diagnostics


def test_facade_accepts_ordered_keys_and_rejects_alias_conflicts() -> None:
    rows = json.dumps(
        [
            {"Console": "PS2", "Game": "ICO"},
            {"Console": "Xbox", "Game": "ICO"},
        ]
    )
    result = LlmSource().fetch(
        "games",
        attrs=[Attr("Console", "string"), Attr("Game", "string")],
        properties={"plan.keys": ["Console", "Game"]},
        llm_fn=returning(rows),
    )
    assert len(result.records) == 2
    assert result.meta.primary_keys == ["Console", "Game"]

    with pytest.raises(ConfigError, match="disagree"):
        LlmSource().fetch(
            "games",
            attrs=[Attr("Console"), Attr("Game")],
            properties={"plan.key": "Game", "plan.keys": ["Console", "Game"]},
            llm_fn=returning(rows),
        )


class TestAlignedIdentityAndContinuation:
    def test_field_collision_order_cannot_leave_a_duplicate_normalized_key(self) -> None:
        response = json.dumps(
            [
                {"Console": "PS2", "GAME": "ICO", "Game": "Other", "Year": 2001},
                {"Console": " ps2 ", "Game": "ico", "Year": 2002},
            ]
        )
        result = LlmSource().fetch(
            "games",
            attrs=ATTRS,
            properties={"plan.keys": list(KEYS)},
            llm_fn=returning(response),
        )
        assert result.records == [{"Console": "PS2", "Game": "ICO", "Year": 2001}]
        assert result.meta.observed_key_count == 1
        assert result.meta.record_origins == [["0.0"]]
        assert result.meta.key_collisions[0]["record_index"] == 0

    def test_complete_same_leaf_duplicate_reaches_reducer_conflict_logic(self) -> None:
        model = Script(
            [
                [
                    {"Console": "PS2", "Game": "ICO", "Year": 2001},
                    {"Console": "ps2", "Game": "ico", "Year": 2002},
                ]
            ]
        )
        run = run_leaf(
            leaf(),
            model,
            attrs=ATTRS,
            limits=PlanLimits(),
            primary_keys=KEYS,
        )
        assert len(run.records) == 2
        assert run.marginal_key_yields == (1,)
        records, coverage = reduce_leaves(
            FetchPlan(mode="flat", leaves=[leaf()]),
            schema(),
            [("0.0", run.records, LeafOutcome("0.0"))],
        )
        assert len(records) == 1
        assert Gap.DEDUP_CONFLICT in [note.code for note in coverage.gaps]

    def test_unique_incomplete_rows_are_retained_without_driving_continuation(self) -> None:
        model = Script([[{"Console": "PS2", "Game": None}, {"Console": "Xbox", "Game": "  "}]])
        run = run_leaf(
            leaf(),
            model,
            attrs=ATTRS,
            limits=PlanLimits(max_continuations=3),
            primary_keys=KEYS,
        )
        assert len(run.records) == 2
        assert run.attempts == 1
        assert run.marginal_key_yields == (0,)
        assert run.incomplete_key_records == 2

    @pytest.mark.parametrize("missing", [None, "", "   "])
    def test_null_empty_and_whitespace_key_values_are_incomplete(self, missing: object) -> None:
        assert key_identity({"Console": "PS2", "Game": missing}, KEYS, ATTRS) is None


class TestCompositeEnumerationScope:
    def test_composite_entities_are_type_normalized_deduped_and_intersected(self) -> None:
        attrs = [Attr("Console", "string"), Attr("Edition", "integer")]
        plan = build_key_tuple_plan(
            original_query="games",
            retrieval_query="games",
            constraints={"Console": Exact("PS2")},
            primary_keys=("Console", "Edition"),
            attrs=attrs,
            entities=[
                {"Console": "PS2", "Edition": 1},
                {"Console": " ps2 ", "Edition": "1"},
                {"Console": "Xbox", "Edition": 2},
            ],
            limits=PlanLimits(max_leaves=4),
        )
        assert len(plan.leaves) == 1
        console = plan.leaves[0].constraints["Console"]
        edition = plan.leaves[0].constraints["Edition"]
        assert isinstance(console, Exact) and console.value == "PS2"
        assert isinstance(edition, Exact) and edition.value == 1

    def test_out_of_scope_composite_enumeration_falls_back_to_bounded_root(self) -> None:
        plan = build_key_tuple_plan(
            original_query="games",
            retrieval_query="games",
            constraints={"Console": Exact("PS2")},
            primary_keys=("Console", "Edition"),
            attrs=[Attr("Console", "string"), Attr("Edition", "integer")],
            entities=[{"Console": "Xbox", "Edition": 2}],
            limits=PlanLimits(max_leaves=4),
        )
        assert [item.id for item in plan.leaves] == [ROOT]
        assert plan.leaves[0].constraints["Console"] == Exact("PS2")


class TestLegacyAndExactTargets:
    def test_legacy_row_target_counts_retained_missing_key_records(self) -> None:
        model = Script([[{"Console": "PS2", "Game": None}, {"Console": "Xbox", "Game": None}]])
        run = run_leaf(
            leaf(),
            model,
            attrs=ATTRS,
            limits=PlanLimits(max_continuations=3),
            primary_keys=KEYS,
            row_target=2,
        )
        assert len(run.records) == 2
        assert run.attempts == 1

    def test_exact_rows_counts_only_complete_unique_keys(self) -> None:
        model = Script(
            [
                [
                    {"Console": "PS2", "Game": None},
                    {"Console": "PS2", "Game": "A"},
                ],
                [{"Console": "PS2", "Game": "B"}],
            ]
        )
        run = run_leaf(
            leaf(),
            model,
            attrs=ATTRS,
            limits=PlanLimits(max_continuations=3),
            primary_keys=KEYS,
            exact_rows=2,
        )
        assert len(run.records) == 3
        assert run.observed_key_count == 2
        assert run.attempts == 2


class TestDefensiveContracts:
    @pytest.mark.parametrize(
        "kwargs",
        [
            {"row_unit": 3},
            {"source": []},
            {"diagnostics": ("bad", 1)},
            {"parse_status": PARSE_PARSED},
            {"expected": 1, "parse_status": "unknown"},
            {"parse_status": PARSE_INVALID},
            {"expected": 1, "parse_status": PARSE_INVALID, "diagnostics": ("bad",)},
        ],
    )
    def test_malformed_cardinality_constructor_states_raise_config_error(
        self, kwargs: dict[str, Any]
    ) -> None:
        with pytest.raises(ConfigError):
            CardinalityEstimate(**cast(Any, kwargs))

    def test_cardinality_diagnostics_are_copied_and_wire_types_are_strict(self) -> None:
        diagnostics = ["  bad  "]
        estimate = CardinalityEstimate(
            parse_status=PARSE_INVALID, diagnostics=cast(Any, diagnostics)
        )
        diagnostics.append("changed")
        assert estimate.diagnostics == ("bad",)
        malformed = estimate.to_dict()
        malformed["source"] = 3
        with pytest.raises(ConfigError, match="source"):
            estimate_from_dict(malformed)
        invalid_unit = estimate_from_reported({"expected": 2, "row_unit": None})
        assert invalid_unit.parse_status == PARSE_INVALID
        assert invalid_unit.expected is None
        assert invalid_unit.diagnostics

    def test_leaf_task_settings_are_copied_immutable_and_not_returned_by_reference(self) -> None:
        settings: dict[str, Any] = {"max_continuations": 0, "model": "x"}
        task = LeafTask.from_leaf(leaf(), caller_context="", schema=schema(), settings=settings)
        settings["model"] = "changed"
        assert task.settings["model"] == "x"
        with pytest.raises(TypeError):
            cast(dict[str, Any], task.settings)["new"] = 1
        payload = task.to_dict()
        cast(dict[str, Any], payload["settings"])["max_continuations"] = 9
        assert task.settings["max_continuations"] == 0

    def test_separate_schema_parser_rejects_conflicting_key_sources(self) -> None:
        parsed = schema_from_response(
            json.dumps(
                {
                    "attrs": [{"name": "Console"}, {"name": "Game"}],
                    "key": "Game",
                    "primary_keys": ["Console", "Game"],
                }
            ),
            [],
        )
        assert parsed.primary_keys == ()
        assert parsed.key_from == KEY_FROM_NONE

    @pytest.mark.parametrize("keys", [("Game", " "), (" Game ", "Game"), cast(Any, (1,))])
    def test_key_names_are_stripped_then_strictly_validated(self, keys: tuple[str, ...]) -> None:
        with pytest.raises((ConfigError, ValueError)):
            Schema(attrs=ATTRS, primary_keys=keys)

    def test_exclusion_limit_measures_the_exact_two_entry_prompt_block(self) -> None:
        older = {"Console": "PS\u2028two", "Game": "ICO"}
        newer = {"Console": "PS2", "Game": "Shadow"}
        limit = rendered_held_entries_size([older, newer]) - 1
        assert rendered_held_entries_size([newer]) <= limit
        model = Script([[older, newer], []])
        run_leaf(
            leaf(),
            model,
            attrs=ATTRS,
            limits=PlanLimits(max_continuations=1, max_exclusion_bytes=limit),
            primary_keys=KEYS,
        )
        assert "PS\\u2028two" not in model.prompts[1]
        assert "PS\u2028two" not in model.prompts[1]
        assert "Shadow" in model.prompts[1]

    def test_constraints_accept_only_finite_json_scalars(self) -> None:
        values: tuple[JsonScalar, ...] = ("x", 1, 1.5, True)
        assert OneOf(values).values == values
        invalid_values: tuple[object, ...] = (
            None,
            " ",
            float("nan"),
            float("inf"),
            [],
            {},
        )
        for invalid in invalid_values:
            with pytest.raises(ConfigError):
                Exact(cast(Any, invalid))

    def test_scalar_constraints_parse_and_round_trip_over_the_wire(self) -> None:
        parsed = strict_constraints({"year": [2023, 2024], "active": True})
        assert parsed is not None
        assert parsed == {"year": OneOf((2023, 2024)), "active": Exact(True)}
        current = LeafTask.from_leaf(
            Leaf(
                id="0.0",
                parent_id=ROOT,
                query="games",
                original_query="games",
                constraints=parsed,
            ),
            caller_context="",
            schema=schema(),
            settings={},
        )
        assert LeafTask.from_dict(json.loads(json.dumps(current.to_dict()))) == current

        spaced = LeafTask.from_leaf(
            Leaf(
                id="0.1",
                parent_id=ROOT,
                query="games",
                original_query="games",
                constraints={"label": Exact(" keep ")},
            ),
            caller_context="",
            schema=schema(),
            settings={},
        )
        assert LeafTask.from_dict(spaced.to_dict()).filters["label"] == Exact(" keep ")

    def test_schema_snapshots_attrs_and_leaf_task_keeps_it_immutable(self) -> None:
        attrs = [Attr("Console", "string"), Attr("Game", "string")]
        built = Schema(attrs=attrs, primary_keys=KEYS, key_from=KEY_FROM_CALLER)
        task = LeafTask.from_leaf(leaf(), caller_context="", schema=built, settings={})
        attrs.append(Attr("Changed"))
        assert tuple(attr.name for attr in task.schema.attrs) == KEYS
        with pytest.raises(AttributeError):
            cast(Any, task.schema.attrs).append(Attr("Changed"))
        with pytest.raises(TypeError):
            cast(Any, task.schema.attrs)[0] = Attr("Changed")
        assert LeafTask.from_dict(task.to_dict()) == task

    def test_leaf_task_count_settings_reject_wrong_types_and_negative_values(self) -> None:
        names = (
            "max_continuations",
            "max_excluded_keys",
            "max_exclusion_bytes",
            "max_output_tokens",
            "max_retries",
            "exact_rows",
        )
        invalid_values: tuple[object, ...] = (-1, True, "1", {})
        for name in names:
            for value in invalid_values:
                with pytest.raises(ConfigError, match=name):
                    LeafTask.from_leaf(
                        leaf(), caller_context="", schema=schema(), settings={name: value}
                    )
        with pytest.raises(ConfigError, match="max_output_tokens"):
            LeafTask.from_leaf(
                leaf(),
                caller_context="",
                schema=schema(),
                settings={"max_output_tokens": 0},
            )

    @pytest.mark.parametrize(
        "name,value",
        [
            ("empty_leaf_retry", 1),
            ("empty_leaf_retry", "false"),
            ("empty_leaf_retry", {}),
            ("model", 1),
            ("model", True),
            ("model", {}),
            ("model", ""),
            ("model", "   "),
            ("temperature", True),
            ("temperature", "0"),
            ("temperature", {}),
            ("temperature", float("nan")),
            ("temperature", float("inf")),
        ],
    )
    def test_leaf_task_non_count_settings_are_validated_by_field(
        self, name: str, value: object
    ) -> None:
        with pytest.raises(ConfigError, match=name):
            LeafTask.from_leaf(leaf(), caller_context="", schema=schema(), settings={name: value})

    def test_nullable_task_settings_remain_valid(self) -> None:
        task = LeafTask.from_leaf(
            leaf(),
            caller_context="",
            schema=schema(),
            settings={
                "model": None,
                "temperature": None,
                "max_output_tokens": None,
                "exact_rows": None,
            },
        )
        assert LeafTask.from_dict(task.to_dict()) == task

    @pytest.mark.parametrize("diagnostics", [("",), (" ",)])
    def test_cardinality_diagnostics_must_be_nonempty(self, diagnostics: tuple[str, ...]) -> None:
        with pytest.raises(ConfigError, match="diagnostics"):
            CardinalityEstimate(parse_status=PARSE_INVALID, diagnostics=diagnostics)
