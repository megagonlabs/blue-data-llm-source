from __future__ import annotations

import json
from typing import Any

import pytest

from blue_data_llm_source import LlmSource
from blue_data_llm_source.prompts import build_bootstrap_prompt, build_schema_prompt
from blue_data_llm_source.types import Attr
from tests.doubles import ScriptedClient


@pytest.mark.parametrize("keys", [(), ("position",), ("name", "position")])
def test_schema_prompts_distinguish_missing_and_explicit_keys(keys: tuple[str, ...]) -> None:
    declared = [Attr("name", "str"), Attr("position", "int")]
    schema = {
        "attrs": [{"name": attr.name, "type": attr.type} for attr in declared],
        "primary_keys": list(keys),
    }
    prompts = (
        build_schema_prompt(
            original_query="the three innermost planets and their positions from the Sun",
            retrieval_query="the three innermost planets and their positions from the Sun",
            caller_context="",
            constraints={},
            declared_schema=schema,
            schema_mode="preserve",
        ),
        build_bootstrap_prompt(
            "the three innermost planets and their positions from the Sun",
            caller_context="",
            declared_attrs=declared,
            declared_key=None,
            declared_keys=keys,
            schema_mode="preserve",
        ),
    )
    for prompt in prompts:
        instruction = " ".join(prompt.split("Original request:")[0].split())
        assert "Caller-supplied primary keys must remain unchanged." in instruction
        assert "infer" in instruction and "schema and request" in instruction
        assert "only when defensible" in instruction
        assert "empty declared primary_keys list means no key was supplied" in instruction
        assert f"Declared schema: {json.dumps(schema, sort_keys=True)}" in prompt


@pytest.mark.parametrize("schedule", ["planning.schema_first", "planning.combined"])
@pytest.mark.parametrize(
    "caller_keys,model_keys,expected_keys,unusable",
    [
        ((), ["name"], ["name"], False),
        (("position",), ["name"], ["position"], False),
        ((), [], [], True),
        ((), None, [], False),
    ],
)
def test_alias_key_inference_preserves_caller_authority_and_no_key_fallback(
    schedule: str,
    caller_keys: tuple[str, ...],
    model_keys: list[str] | None,
    expected_keys: list[str],
    unusable: bool,
) -> None:
    attrs = [{"name": "name", "type": "str"}, {"name": "position", "type": "int"}]
    schema: dict[str, Any] = {"attrs": attrs}
    if model_keys is None:
        schema["key"] = None
    else:
        schema["primary_keys"] = model_keys
    rows = [{"name": "Mercury", "position": 1}, {"name": "Venus", "position": 2}]
    analysis: dict[str, Any] = {
        "ask": "the two innermost planets and their positions",
        "constraints": {},
    }
    estimate = {"expected": 2, "lower": 2, "upper": 2, "row_unit": "planet"}
    replies = (
        [json.dumps(analysis), json.dumps(schema), json.dumps(estimate)]
        if schedule.endswith("schema_first")
        else [json.dumps({**analysis, "schema": schema, "row_estimate": estimate})]
    )
    client = ScriptedClient([*replies, json.dumps(rows)])
    result = LlmSource(client=client).fetch(
        "the two innermost planets and their positions from the Sun",
        attrs=attrs,
        properties={
            "plan.mode": "partitioned",
            "plan.planning_order": schedule,
            "plan.max_depth": 0,
            "plan.keys": list(caller_keys),
        },
    )
    assert result.records == rows
    assert result.meta.primary_keys == expected_keys
    assert result.meta.type_mismatches == []
    assert (
        any(
            "schema was unusable" in gap or "schema response was unusable" in gap
            for gap in result.meta.gaps
        )
        == unusable
    )
    assert f"Ordered primary key: {json.dumps(expected_keys)}" in client.prompts[-1]
    assert '"type": "str"' in client.prompts[-1]
    assert '"type": "int"' in client.prompts[-1]
