"""The four fixtures from the data-source spec.

These are the parity check: the same four queries the spec walks through, answered by a
scripted model so the shape of the result is what is under test, not the model.
"""

from __future__ import annotations

from blue_data_llm_source import LlmSource
from tests.doubles import ScriptedClient

PRESIDENT = "Who is the current US president?"
THREE_PRESIDENTS = "Show me 3 presidents of the United States"

FREEFORM_ANSWER = """```json
[{"name": "Donald Trump", "party": "Republican", "took office": "2025-01-20"}]
```"""

SPLIT_NAME_ANSWER = """```json
[{"first name": "Donald", "last name": "Trump", "age": 79, "party": "Republican"}]
```"""

LAST_NAME_ANSWER = """```json
[{"name": "Trump", "age": 79, "party": "Republican"}]
```"""

THREE_PRESIDENTS_ANSWER = """```json
[{"name": "Washington", "year": 1789, "party": "None"},
 {"name": "Lincoln", "year": 1861, "party": "Republican"},
 {"name": "Roosevelt", "year": 1933, "party": "Democratic"}]
```"""


def test_fixture_1_a_query_with_no_attributes_returns_the_models_own_keys() -> None:
    client = ScriptedClient([FREEFORM_ANSWER])
    source = LlmSource(client=client)

    result = source.fetch(PRESIDENT)

    assert len(result) == 1
    assert result.records == [
        {"name": "Donald Trump", "party": "Republican", "took office": "2025-01-20"}
    ]
    assert result.meta.requested_attrs == []
    assert result.meta.observed_keys == ["name", "party", "took office"]
    assert result.meta.unmapped_keys == []


def test_fixture_2_requested_attributes_are_the_exact_keys_returned() -> None:
    attr_names = ["first name", "last name", "age", "party"]
    client = ScriptedClient([SPLIT_NAME_ANSWER])
    source = LlmSource(client=client)

    result = source.fetch(PRESIDENT, attrs=attr_names)

    assert len(result) == 1
    assert list(result.records[0]) == attr_names
    assert result.records[0]["first name"] == "Donald"
    assert result.records[0]["last name"] == "Trump"
    assert result.meta.requested_attrs == attr_names
    assert result.meta.unmapped_keys == []


def test_fixture_2_requested_attributes_reach_the_prompt() -> None:
    client = ScriptedClient([SPLIT_NAME_ANSWER])
    LlmSource(client=client).fetch(PRESIDENT, attrs=["first name", "last name", "age", "party"])

    prompt = client.prompts[0]
    for name in ("- first name", "- last name", "- age", "- party"):
        assert name in prompt


def test_fixture_3_context_reaches_the_prompt_and_shapes_the_keys() -> None:
    context = "The name in the output should be the last name."
    client = ScriptedClient([LAST_NAME_ANSWER])
    source = LlmSource(client=client)

    result = source.fetch(PRESIDENT, attrs=["name", "age", "party"], context=context)

    assert context in client.prompts[0]
    assert list(result.records[0]) == ["name", "age", "party"]
    assert result.records[0]["name"] == "Trump"


def test_fixture_4_a_multi_record_query_returns_every_record() -> None:
    attr_names = ["name", "year", "party"]
    context = "Use the year the president took office."
    client = ScriptedClient([THREE_PRESIDENTS_ANSWER])
    source = LlmSource(client=client)

    result = source.fetch(THREE_PRESIDENTS, attrs=attr_names, context=context)

    assert len(result) == 3
    assert [record["name"] for record in result.records] == [
        "Washington",
        "Lincoln",
        "Roosevelt",
    ]
    for record in result.records:
        assert list(record) == attr_names
    assert context in client.prompts[0]
    assert THREE_PRESIDENTS in client.prompts[0]


def test_every_fixture_query_reaches_the_model_verbatim() -> None:
    client = ScriptedClient([FREEFORM_ANSWER])
    LlmSource(client=client).fetch(PRESIDENT)
    assert PRESIDENT in client.prompts[0]


def test_one_fetch_is_one_call_to_the_model() -> None:
    client = ScriptedClient([FREEFORM_ANSWER])
    result = LlmSource(client=client).fetch(PRESIDENT)

    assert len(client.prompts) == 1
    assert result.meta.calls == 1
