"""The shipped documents describe the shipped types, checked rather than swept.

These tests derive the answer from the code. A new ``Outcome`` member or ``ExecMeta`` field
therefore turns a reference document that omits it into a failing test.

What this cannot do is judge prose. A document may describe a field correctly or wrongly and
this will not tell them apart; it only refuses a document that has not heard of it.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import fields
from inspect import signature
from pathlib import Path
from typing import cast

import pytest

import blue_data_llm_source.prompts as prompts
import blue_data_llm_source.source as source_module
from blue_data_llm_source.planning.coordinator import coordinate_plan
from blue_data_llm_source.reduction.parsing import parse_records, salvage_records
from blue_data_llm_source.reduction.verifier import dedupe
from blue_data_llm_source.types import ExecMeta, Outcome

ROOT = Path(__file__).resolve().parents[3]
"""The payload root — `lib/tests/unit/x.py` up to the directory holding `docs/` and `lib/`."""

REFERENCE = [
    "docs/design/06-library.md",
    "docs/design/09-api-interface-spec.md",
    "docs/design/10-data-model.md",
    "lib/README.md",
]
"""Documents that publish the result types as a reference a reader relies on.

Not every Markdown file: a scenario walk-through or an architecture note mentions a field in
passing and is not claiming to enumerate them. These references carry the field tables and the
outcome vocabulary, and a member missing from one of them is a reference that is wrong.
"""

PLATFORM_READMES = ["platform/cli/README.md", "platform/mcp/README.md"]
# Adapter tests derive the full default key set from each serializer and compare it with the
# fenced sample. This list checks only the shared names both platform READMEs promise in prose.
ALWAYS_PRESENT_PLATFORM_FIELDS = [
    "gaps",
    "stop_reasons",
    "requested_attrs",
    "row_estimate",
    "cardinality_estimate",
    "primary_keys",
    "observed_key_count",
    "marginal_key_yields",
    "incomplete_key_records",
]


def text(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


@pytest.mark.parametrize("member", sorted(Outcome.ALL))
@pytest.mark.parametrize("document", REFERENCE)
def test_every_outcome_member_appears_in_every_reference_document(
    document: str, member: str
) -> None:
    assert f"`{member}`" in text(document), f"{document} does not mention outcome {member!r}"


@pytest.mark.parametrize("field_name", sorted(f.name for f in fields(ExecMeta)))
@pytest.mark.parametrize("document", REFERENCE)
def test_every_exec_meta_field_appears_in_every_reference_document(
    document: str, field_name: str
) -> None:
    # The bare name as a word, not a backticked one. A field listed in a fenced code block is
    # documented; requiring the prose form would measure house style rather than coverage,
    # and a check that fails for the wrong reason gets relaxed rather than obeyed.
    found = re.search(rf"\b{re.escape(field_name)}\b", text(document))
    assert found, f"{document} does not mention meta.{field_name!r}"


@pytest.mark.parametrize("field_name", ALWAYS_PRESENT_PLATFORM_FIELDS)
@pytest.mark.parametrize("document", PLATFORM_READMES)
def test_every_always_present_platform_field_appears_in_its_readme(
    document: str, field_name: str
) -> None:
    found = re.search(rf"\b{re.escape(field_name)}\b", text(document))
    assert found, f"{document} does not mention always-present meta.{field_name!r}"


def test_a_document_that_reproduces_the_outcome_class_reproduces_all_of_it() -> None:
    """A code block claiming to be the class is a contract, not an illustration.

    `06-library.md` published an `Outcome` class and `ALL` tuple that omitted a member its own
    prose two paragraphs later described. A reader trusts the block over the prose, because a
    block looks generated.
    """
    body = text("docs/design/06-library.md")
    block = re.search(r"class Outcome:\n(.*?)\n```", body, re.S)
    assert block is not None, "06-library.md no longer publishes the Outcome class"
    for member in Outcome.ALL:
        assert f'"{member}"' in block.group(1), f"the published Outcome class omits {member!r}"


def test_the_published_outcome_vocabulary_is_closed() -> None:
    """`ALL` is what every consumer switches on, so a member outside it is unreachable.

    The mirror of the tests above: those refuse a member the documents have not heard of, and
    this refuses one the code declares and does not publish in its own tuple.
    """
    declared = {
        value
        for name, value in vars(Outcome).items()
        if not name.startswith("_") and isinstance(value, str)
    }
    assert declared == set(Outcome.ALL)


def test_prompt_reference_names_every_shipped_builder_and_version() -> None:
    documented_builders = text("docs/design/06-library.md")
    builders = {
        name
        for name, value in vars(prompts).items()
        if name.startswith("build_") and name.endswith("_prompt") and callable(value)
    }
    assert len(builders) == 9
    for name in builders:
        assert f"`{name}(" in documented_builders, f"06-library.md omits {name}"

    data_model = text("docs/design/10-data-model.md")
    assert prompts.LEGACY_INITIAL_V2_PROMPT_VERSION in data_model
    assert prompts.INITIAL_V2_PROMPT_VERSION in data_model


@pytest.mark.parametrize(
    ("function", "name"),
    [
        (prompts.build_fetch_prompt, "build_fetch_prompt"),
        (parse_records, "parse_records"),
        (salvage_records, "salvage_records"),
        (dedupe, "dedupe"),
    ],
)
def test_reference_signatures_name_every_current_parameter(
    function: Callable[..., object], name: str
) -> None:
    body = text("docs/design/06-library.md")
    row = re.search(rf"^\| `{name}\(([^`]*)\)`", body, re.M)
    assert row is not None, f"06-library.md omits the {name} signature"
    for parameter in signature(function).parameters:
        assert re.search(rf"\b{re.escape(parameter)}\b", row.group(1)), (
            f"06-library.md omits {name} parameter {parameter!r}"
        )


def test_configuration_reference_names_every_facade_plan_key() -> None:
    body = text("docs/design/11-testing-strategy.md")
    section = body.split("## 6. Configuration Tests", 1)[1].split("## 7. The Base Class Tests", 1)[
        0
    ]
    plan_keys = cast("frozenset[str]", getattr(source_module, "_PLAN_KEYS"))
    for key in sorted(plan_keys):
        assert f"`plan.{key}`" in section, f"configuration tests omit plan.{key}"


def test_planner_does_not_publish_execution_only_estimate_exposure() -> None:
    assert "expose_estimate" not in signature(coordinate_plan).parameters
