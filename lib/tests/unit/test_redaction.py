"""Library redaction and serialized metadata tests."""

from __future__ import annotations

import json
from collections.abc import Iterator
from copy import deepcopy
from dataclasses import fields
from typing import Any, cast

import pytest

import blue_data_llm_source as pkg
from blue_data_llm_source import ExecMeta, LlmSource, meta_view, redact
from blue_data_llm_source.redaction import MASK

ENVIRONMENT_VALUE = "canary-environment-value"
STAMPED = "sk-canary-NOTAKEY"


def test_a_value_the_environment_holds_is_masked_whatever_it_looks_like(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", ENVIRONMENT_VALUE)
    masked = redact(f"AuthenticationError: {ENVIRONMENT_VALUE} was rejected")

    assert ENVIRONMENT_VALUE not in masked
    assert masked == f"AuthenticationError: {MASK} was rejected"


def test_escaped_environment_secret_is_masked(monkeypatch: pytest.MonkeyPatch) -> None:
    private = 'abc"def-12345'
    monkeypatch.setenv("OPENAI_API_KEY", private)
    quoted = json.dumps(private)

    assert redact(f'provider failed: {{"detail": {quoted}}}') == (
        f'provider failed: {{"detail": {MASK}}}'
    )
    assert redact(f"provider failed: {quoted[1:-1]}") == f"provider failed: {MASK}"


def test_seven_character_environment_secret_is_masked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "abc1234")
    assert redact("provider failed: abc1234") == "provider failed: ***"


def test_a_short_environment_value_is_not_treated_as_key_material(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BLUE_CANARY_TOKEN", "true")

    assert redact("plan.expose_estimate is true") == "plan.expose_estimate is true"


def test_a_column_name_is_not_key_material(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BLUE_DATA_LLM_SOURCE_PLAN_KEY", "country")

    assert redact("observed keys: country, capital") == "observed keys: country, capital"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (f"invalid api key: {STAMPED}", f"invalid api key: {MASK}"),
        ("api_key=canary-val", f"api_key={MASK}"),
        (
            "headers={'authorization': 'Bearer tok-canary'}",
            f"headers={{'authorization': 'Bearer {MASK}'}}",
        ),
        ("key CANARY0OPAQUE0RUN0NOT0A0KEY0X rejected", f"key {MASK} rejected"),
    ],
)
def test_the_shapes_a_key_is_written_in_are_masked(text: str, expected: str) -> None:
    assert redact(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "gpt-4o-2024-08-06 has no published price here",
        "/private/var/folders/pytest-of-someone/test_a_transport_failure0/home",
        "types this library cannot check: strng",
        "BLUE_DATA_LLM_SOURCE_PRICE_PER_INPUT_TOKEN in the profile",
        "plan.mode must be one of flat, partitioned, row_by_row",
        "limits.max_cost must be a number, got 'abc'",
    ],
)
def test_ordinary_text_survives_unchanged(text: str) -> None:
    assert redact(text) == text


def test_meta_view_masks_request_metadata() -> None:
    meta = ExecMeta(
        model=STAMPED,
        asked=STAMPED,
        requested_attrs=[STAMPED],
        unchecked_types=[STAMPED],
    )
    view = meta_view(meta)

    assert view["model"] == MASK
    assert view["asked"] == MASK
    assert view["requested_attrs"] == [MASK]
    assert view["unchecked_types"] == [MASK]


def test_metadata_masks_credentials_even_when_a_record_was_discarded() -> None:
    meta = ExecMeta(
        primary_keys=[STAMPED, "country"],
        unmapped_keys=[STAMPED],
        remapped_keys={STAMPED: STAMPED},
        gaps=[f"discarded duplicate value {STAMPED}"],
        cardinality_estimate={"row_unit": STAMPED},
        type_mismatches=[{"value": STAMPED}],
        key_collisions=[{"ignored_value": STAMPED}],
    )
    view = meta_view(meta, verbose=True)

    assert STAMPED not in json.dumps(view)
    assert view["primary_keys"] == [MASK, "country"]
    assert view["cardinality_estimate"] == {"row_unit": MASK}
    assert view["gaps"] == [f"discarded duplicate value {MASK}"]
    assert view["key_collisions"] == [{"ignored_value": MASK}]
    assert view["remapped_keys"] == {MASK: MASK}


def test_a_masked_field_still_gives_an_ordinary_answer() -> None:
    view = meta_view(ExecMeta(model="gpt-4o", requested_attrs=["country", "capital"]))

    assert view["model"] == "gpt-4o"
    assert view["requested_attrs"] == ["country", "capital"]


# Populate all ExecMeta fields so a new field cannot bypass the redaction check.

FILLED: dict[str, Any] = {
    "str": STAMPED,
    "str | None": STAMPED,
    "list[str]": [STAMPED],
    "list[list[str]]": [[STAMPED]],
    "dict[str, str]": {STAMPED: STAMPED},
    "list[dict[str, Any]]": [{STAMPED: STAMPED}],
    "dict[str, object] | None": {STAMPED: STAMPED},
    "int": 1,
    "int | None": 1,
    "float": 1.0,
    "list[int]": [1],
    # accounting_known appears in the view only when false.
    "bool": False,
}


def saturated() -> ExecMeta:
    values: dict[str, Any] = {}
    for declared in fields(ExecMeta):
        shape = str(declared.type)
        assert shape in FILLED, (
            f"{declared.name} is declared {shape!r}, which this canary cannot fill. "
            "Add the shape to include the field in this test."
        )
        values[declared.name] = deepcopy(FILLED[shape])
    return ExecMeta(**values)


def published_strings(node: object, path: str = "") -> Iterator[tuple[str, str]]:
    """Yield strings with stable field paths, including dictionary keys."""
    if isinstance(node, str):
        yield path, node
    elif isinstance(node, list):
        for item in cast("list[object]", node):
            yield from published_strings(item, f"{path}[]")
    elif isinstance(node, dict):
        for key, value in cast("dict[str, object]", node).items():
            yield f"{path}{{}}", key
            step = "{}" if STAMPED in key else key
            yield from published_strings(value, f"{path}.{step}" if path else step)


UNMASKED: dict[str, str] = {
    "{}": "field names written by meta_view",
}


def test_every_string_the_view_publishes_is_masked_or_has_a_written_reason() -> None:
    view = meta_view(saturated(), verbose=True)
    open_paths = {path for path, value in published_strings(view) if MASK not in value}

    # Equality detects both new unmasked fields and stale exceptions.
    assert open_paths == set(UNMASKED)


def test_a_record_value_shaped_like_a_key_is_returned_intact() -> None:
    payload = f'[{{"name": "canary", "code": "{STAMPED}"}}]'

    def model(prompt: str) -> str:
        return payload

    result = LlmSource().fetch("one record", llm_fn=model)

    assert result.records == [{"name": "canary", "code": STAMPED}]


def test_the_rule_is_exported_from_the_package_root() -> None:
    assert pkg.redact is redact
    assert "redact" in pkg.__all__


# Estimate exposes model names and error reasons as result fields.
PASTED_KEY = "sk-canary-NOTAKEY"


def test_an_estimate_does_not_return_a_credential_pasted_into_the_model() -> None:
    estimate = LlmSource(properties={"llm.model": PASTED_KEY}).estimate("who?")
    assert PASTED_KEY not in str(estimate.model)
    assert PASTED_KEY not in str(estimate.reason)
    assert estimate.model == MASK


def test_an_estimate_still_names_a_model_that_is_only_a_model() -> None:
    estimate = LlmSource(properties={"llm.model": "gpt-4o"}).estimate("who?")
    assert estimate.model == "gpt-4o"
    assert estimate.bounded is True
