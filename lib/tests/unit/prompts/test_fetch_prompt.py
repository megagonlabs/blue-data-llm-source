"""The fetch prompt: what it carries, and what it refuses to interpret."""

from __future__ import annotations

import pytest

from blue_data_llm_source import prompts
from blue_data_llm_source.types import Attr

# The query that broke the implementation this design replaces. Template substitution
# followed by Jinja2, run until the string stopped changing, expanded whatever the user
# happened to type. All three markers must survive as literal text.
HOSTILE_QUERY = "Explain ${input}, {{jinja}} and {braces} in one sentence"


def test_query_text_appears_in_the_prompt() -> None:
    prompt = prompts.build_fetch_prompt("who is the president?", [], "")
    assert "who is the president?" in prompt


def test_template_markers_in_the_query_survive_verbatim() -> None:
    prompt = prompts.build_fetch_prompt(HOSTILE_QUERY, [], "")
    assert "${input}" in prompt
    assert "{{jinja}}" in prompt
    assert "{braces}" in prompt
    assert HOSTILE_QUERY in prompt


def test_template_markers_in_the_context_survive_verbatim() -> None:
    prompt = prompts.build_fetch_prompt("who?", [], "answer with ${name}, not {{name}}")
    assert "${name}" in prompt
    assert "{{name}}" in prompt


def test_template_markers_in_an_attribute_name_survive_verbatim() -> None:
    prompt = prompts.build_fetch_prompt("who?", [Attr(name="{party}")], "")
    assert "- {party}" in prompt


def test_a_query_that_looks_like_a_field_name_is_not_substituted() -> None:
    # "{query}" is a real field of the template. Inserted text is never re-scanned, so
    # it must come out as the four characters the caller typed.
    prompt = prompts.build_fetch_prompt("{query}", [], "")
    assert "{query}" in prompt
    assert prompt.count("{query}") == 1


def test_render_attributes_reports_none_specified_when_empty() -> None:
    assert prompts.render_attributes([]) == "(none specified)"


def test_render_attributes_lists_names_without_types() -> None:
    rendered = prompts.render_attributes([Attr("name"), Attr("party")])
    assert rendered == "- name\n- party"


def test_render_attributes_includes_a_declared_type() -> None:
    assert prompts.render_attributes([Attr("age", "int")]) == "- age: int"


def test_requested_attributes_reach_the_prompt() -> None:
    prompt = prompts.build_fetch_prompt("who?", [Attr("name"), Attr("age", "int")], "")
    assert "- name" in prompt
    assert "- age: int" in prompt


def test_missing_context_renders_as_none() -> None:
    prompt = prompts.build_fetch_prompt("who?", [], "   ")
    assert "Context:\n(none)" in prompt


def test_context_reaches_the_prompt() -> None:
    prompt = prompts.build_fetch_prompt("who?", [], "use the last name only")
    assert "use the last name only" in prompt


@pytest.mark.parametrize("marker", ["Query:", "Attributes:", "Context:", "Output:"])
def test_prompt_keeps_its_section_headings(marker: str) -> None:
    prompt = prompts.build_fetch_prompt("who?", [], "")
    assert marker in prompt
