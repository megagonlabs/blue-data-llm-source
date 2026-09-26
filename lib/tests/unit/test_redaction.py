"""What `redact` masks, what it leaves alone, and where the library applies it.

The rule is shared by both adapters, so it is pinned here rather than twice over in their
suites. Each adapter then has its own canary for its own output surfaces.

Every literal below is a sentinel, not a credential.
"""

from __future__ import annotations

from collections.abc import Iterator
from copy import deepcopy
from dataclasses import fields
from typing import Any, cast

import pytest

import blue_data_llm_source as pkg
from blue_data_llm_source import ExecMeta, LlmSource, meta_view, redact
from blue_data_llm_source.redaction import MASK

ENVIRONMENT_VALUE = "canary-environment-value"
"""Deliberately not key-shaped: lower case, no digits, nothing a shape rule would catch.

What makes it a secret is the variable holding it, which is the only rule that can catch a
provider whose key spelling nobody here has seen.
"""

STAMPED = "sk-canary-NOTAKEY"


def test_a_value_the_environment_holds_is_masked_whatever_it_looks_like(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", ENVIRONMENT_VALUE)
    masked = redact(f"AuthenticationError: {ENVIRONMENT_VALUE} was rejected")

    assert ENVIRONMENT_VALUE not in masked
    assert masked == f"AuthenticationError: {MASK} was rejected"


def test_a_short_environment_value_is_not_treated_as_key_material(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Otherwise a variable holding `true` blanks the word out of every sentence."""
    monkeypatch.setenv("BLUE_CANARY_TOKEN", "true")

    assert redact("plan.expose_estimate is true") == "plan.expose_estimate is true"


def test_a_column_name_is_not_key_material(monkeypatch: pytest.MonkeyPatch) -> None:
    """`plan.key` names a record column. Reading its value as a secret would mask a word."""
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
    """A stamped literal, a labelled assignment, a carried header, an opaque run."""
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
    """A message this package needs to hand over whole, in the shapes it actually writes."""
    assert redact(text) == text


def test_what_a_view_narrates_about_the_call_is_masked() -> None:
    """The model it ran against, the question it ended up asking, and the caller's text."""
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


def test_the_diagnostics_that_name_record_keys_are_published_as_they_stand() -> None:
    """They index the records, which are not masked, so masking one here would lie.

    A key the model invented and a value it returned are the answer. A diagnostic naming a
    key that no longer appears in any record is a diagnostic about nothing. `requested_attrs`
    has the same claim and is masked anyway; `views` carries that decision.
    """
    meta = ExecMeta(
        primary_keys=[STAMPED],
        unmapped_keys=[STAMPED],
        remapped_keys={STAMPED: STAMPED},
    )
    view = meta_view(meta, verbose=True)

    assert view["primary_keys"] == [STAMPED]
    assert view["unmapped_keys"] == [STAMPED]
    assert view["remapped_keys"] == {STAMPED: STAMPED}


def test_a_masked_field_still_gives_an_ordinary_answer() -> None:
    """Masking is worth nothing if it costs a field the answer it exists to give."""
    view = meta_view(ExecMeta(model="gpt-4o", requested_attrs=["country", "capital"]))

    assert view["model"] == "gpt-4o"
    assert view["requested_attrs"] == ["country", "capital"]


# ------------------------------------------------------------- the whole view --
# The tests above name the fields. A list of fields is only ever right about the view it was
# written against: a field added to `ExecMeta` reaches this serializer whether or not anybody
# revisits the list, and it is published before the question is asked. So this enumerates the
# boundary instead of the fields. `ExecMeta` is filled from its own annotations, and every
# string the view produces then has to be masked or to be named below with a reason.

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
    # False rather than True, because `accounting_known` publishes only in its false form
    # and a field the view omits is a field this canary does not check.
    "bool": False,
}
"""A value carrying the sentinel wherever the declared shape can hold a string."""


def saturated() -> ExecMeta:
    """An ExecMeta with the sentinel in every string position its annotations allow."""
    values: dict[str, Any] = {}
    for declared in fields(ExecMeta):
        shape = str(declared.type)
        assert shape in FILLED, (
            f"{declared.name} is declared {shape!r}, which this canary cannot fill. "
            "Add the shape, so the field is checked rather than skipped in silence."
        )
        values[declared.name] = deepcopy(FILLED[shape])
    return ExecMeta(**values)


def published_strings(node: object, path: str = "") -> Iterator[tuple[str, str]]:
    """Every string in *node*, each with a path naming its field rather than its value.

    A list index collapses to `[]`. A dict key is itself published, so it is yielded at
    `{}` under its container; a key holding data rather than a field name spells its own
    step `{}` too, which keeps the path stable whatever arrived under it.
    """
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
    "{}": "the view's own field names, written as literals in `views`.",
    "outcome": "a library outcome constant. No caller or model text reaches it.",
    "stopped_by": "a library stop-reason constant, and the first of `stop_reasons`.",
    "split_decision": "a library constant naming what the root probe decided.",
    "stop_reasons[]": "library limit names, from the ceiling that fired.",
    "gaps[]": (
        "operational diagnostics, which quote the record values two leaves disagreed about. "
        "Masking a gap would edit the answer it reports on. It also quotes an attribute name "
        "the caller supplied, on the path where a filter enumerated nothing."
    ),
    "cardinality_estimate{}": "the model's own row-count estimate, published as it parsed.",
    "cardinality_estimate.{}": (
        "the whole estimate subtree as the model reported it, `row_unit` included, which is "
        "free text the model chose. Model output describing the answer."
    ),
    "primary_keys[]": "names keys in the records, which are not masked.",
    "unmapped_keys[]": "names keys the model returned, which are not masked.",
    "remapped_keys{}": "names a key the model returned, which is not masked.",
    "remapped_keys.{}": "names the key it was mapped onto, which is not masked.",
    "record_origins[][]": "plan-generated leaf ids, parallel to the records.",
    "normalization_collisions[]{}": "names keys the model returned, which are not masked.",
    "normalization_collisions[].{}": "the colliding record values themselves, the answer.",
    "type_mismatches[]{}": "diagnostic field names, written as literals in the library.",
    "type_mismatches[].{}": "the value that missed its declared type, which is the answer.",
    "key_collisions[]{}": "diagnostic field names, written as literals in the library.",
    "key_collisions[].{}": "the colliding key and its record values, the answer.",
}
"""Every string `meta_view` publishes without masking, and why each one has to stay.

The entries fall into two groups. A library constant or a field name is text this package
wrote, and there is nothing in it to mask. Everything else is, or indexes, the records —
this source returns model output as data, and a diagnostic naming a key masked here would
describe a record that still contains it under its own name.
"""


def test_every_string_the_view_publishes_is_masked_or_has_a_written_reason() -> None:
    """The keeper is the boundary itself, not the fields that were on it when it was written."""
    view = meta_view(saturated(), verbose=True)
    open_paths = {path for path, value in published_strings(view) if MASK not in value}

    # Equality both ways: an unlisted path is a field published in the clear that nobody
    # decided about, and a listed path that no longer appears is a reason for a rule that
    # is no longer there.
    assert open_paths == set(UNMASKED)


def test_a_record_value_shaped_like_a_key_is_returned_intact() -> None:
    """The boundary, stated as a fetch: this source returns model output as data."""
    payload = f'[{{"name": "canary", "code": "{STAMPED}"}}]'

    def model(prompt: str) -> str:
        return payload

    result = LlmSource().fetch("one record", llm_fn=model)

    assert result.records == [{"name": "canary", "code": STAMPED}]


def test_the_rule_is_exported_from_the_package_root() -> None:
    """Both adapters call it, so it is a supported surface rather than an internal helper."""
    assert pkg.redact is redact
    assert "redact" in pkg.__all__


# ------------------------------------------------------------------ estimate --
# `estimate` publishes two fields built from the configured model name, and it is the
# one surface that returns a library error message as a *result field* rather than
# raising it, so neither passes the error boundary where the other masking happens.
PASTED_KEY = "sk-canary-NOTAKEY"


def test_an_estimate_does_not_return_a_credential_pasted_into_the_model() -> None:
    estimate = LlmSource(properties={"llm.model": PASTED_KEY}).estimate("who?")
    assert PASTED_KEY not in str(estimate.model)
    assert PASTED_KEY not in str(estimate.reason)
    assert estimate.model == MASK


def test_an_estimate_still_names_a_model_that_is_only_a_model() -> None:
    """Masking is worth nothing if it costs the field its ordinary answer."""
    estimate = LlmSource(properties={"llm.model": "gpt-4o"}).estimate("who?")
    assert estimate.model == "gpt-4o"
    assert estimate.bounded is True
