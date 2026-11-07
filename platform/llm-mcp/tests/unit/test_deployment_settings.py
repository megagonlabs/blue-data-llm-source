"""The request settings a deployment gives this server through its environment.

The variable names are the CLI profile's, so one deployment's settings mean the same thing
on both surfaces. The provider is replaced by a module that records what it was asked, so
these tests can see the configured model reach the request without contacting anything.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from blue_data_llm_source.redaction import MASK

from blue_data_llm_mcp import tools

QUERY = "the member states of the European Union"
RECORDS = json.dumps([{"country": "France"}, {"country": "Germany"}])
VARIABLES = [variable for variable, _, _ in tools._ENVIRONMENT_SETTINGS]  # pyright: ignore[reportPrivateUsage]
LIMIT_VARIABLES = [variable for variable, _, _ in tools._OPERATOR_LIMITS]  # pyright: ignore[reportPrivateUsage]


class RecordingProvider:
    """Stands in for litellm: answers every call with two records and a readable price."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def completion(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        return {
            "id": "stub",
            "model": str(kwargs["model"]),
            "choices": [{"index": 0, "message": {"role": "assistant", "content": RECORDS}}],
            "usage": {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30},
        }

    async def acompletion(self, **kwargs: Any) -> dict[str, Any]:
        return self.completion(**kwargs)

    def completion_cost(self, completion_response: Any = None, **_: Any) -> float:
        return 0.000_01

    def get_llm_provider(self, model: str, custom_llm_provider: str | None = None, **_: Any) -> Any:
        return model, custom_llm_provider or "openai", None, None

    def get_supported_openai_params(self, **_: Any) -> list[str]:
        return ["reasoning_effort", "max_completion_tokens"]

    def as_module(self) -> ModuleType:
        module = ModuleType("litellm")
        for name in (
            "completion",
            "acompletion",
            "completion_cost",
            "get_llm_provider",
            "get_supported_openai_params",
        ):
            setattr(module, name, getattr(self, name))
        return module


@pytest.fixture
def provider(monkeypatch: pytest.MonkeyPatch) -> RecordingProvider:
    stub = RecordingProvider()
    monkeypatch.setitem(sys.modules, "litellm", stub.as_module())
    return stub


def test_the_variables_are_the_cli_profile_names() -> None:
    assert VARIABLES == [
        "BLUE_DATA_LLM_MODEL",
        "BLUE_DATA_LLM_SOURCE_REASONING_EFFORT",
        "BLUE_DATA_LLM_SOURCE_MAX_TOKENS",
        "BLUE_DATA_LLM_SOURCE_TEMPERATURE",
        "BLUE_DATA_LLM_SOURCE_TIMEOUT",
        "BLUE_DATA_LLM_SOURCE_MAX_PARALLEL",
        "BLUE_DATA_LLM_SOURCE_CACHE",
        "BLUE_DATA_LLM_SOURCE_CACHE_DIR",
        "BLUE_DATA_LLM_SOURCE_PRICE_PER_INPUT_TOKEN",
        "BLUE_DATA_LLM_SOURCE_PRICE_PER_OUTPUT_TOKEN",
    ]


def test_each_setting_is_read_as_its_type() -> None:
    environ = {
        "BLUE_DATA_LLM_MODEL": "gpt-5.4",
        "BLUE_DATA_LLM_SOURCE_REASONING_EFFORT": "none",
        "BLUE_DATA_LLM_SOURCE_MAX_TOKENS": " 512 ",
        "BLUE_DATA_LLM_SOURCE_TEMPERATURE": "0.2",
        "BLUE_DATA_LLM_SOURCE_TIMEOUT": "30",
        "BLUE_DATA_LLM_SOURCE_MAX_PARALLEL": "2",
        "BLUE_DATA_LLM_SOURCE_CACHE": "yes",
        "BLUE_DATA_LLM_SOURCE_CACHE_DIR": "~/llm-cache",
        "BLUE_DATA_LLM_SOURCE_PRICE_PER_INPUT_TOKEN": "1e-6",
        "BLUE_DATA_LLM_SOURCE_PRICE_PER_OUTPUT_TOKEN": "2e-6",
    }
    assert tools.deployment_properties(environ) == {
        "llm.model": "gpt-5.4",
        "llm.reasoning_effort": "none",
        "llm.max_tokens": 512,
        "llm.temperature": 0.2,
        "llm.timeout": 30.0,
        "llm.max_parallel": 2,
        "llm.cache": True,
        "llm.cache_dir": str(Path("~/llm-cache").expanduser()),
        "llm.price_per_input_token": 1e-6,
        "llm.price_per_output_token": 2e-6,
    }
    assert tools.deployment_properties({"BLUE_DATA_LLM_SOURCE_CACHE": "Off"}) == {
        "llm.cache": False
    }


def test_an_unset_or_blank_variable_supplies_nothing() -> None:
    assert tools.deployment_properties({}) == {}
    assert tools.deployment_properties({"BLUE_DATA_LLM_MODEL": "  "}) == {}
    built = tools.build_source({})
    assert "llm.model" not in built.properties
    assert built.max_tokens_for_model("gpt-6-luna") == 25_000


def test_other_variables_are_not_read() -> None:
    assert tools.deployment_properties({"BLUE_DATA_LLM_SOURCE_LIMIT_COST": "0.01"}) == {}


def test_the_configured_model_reaches_the_request(
    monkeypatch: pytest.MonkeyPatch, provider: RecordingProvider
) -> None:
    monkeypatch.setenv("BLUE_DATA_LLM_MODEL", "gpt-4o-mini")
    monkeypatch.setenv("BLUE_DATA_LLM_SOURCE_MAX_TOKENS", "512")

    payload = tools.dispatch("llmdb_query", {"query": QUERY})

    assert [record["country"] for record in payload["records"]] == ["France", "Germany"]
    assert provider.calls
    assert {call["model"] for call in provider.calls} == {"gpt-4o-mini"}
    assert {call["max_tokens"] for call in provider.calls} == {512}


async def test_the_configured_model_reaches_the_async_request(
    monkeypatch: pytest.MonkeyPatch, provider: RecordingProvider
) -> None:
    monkeypatch.setenv("BLUE_DATA_LLM_MODEL", "gpt-5.4-mini")

    await tools.adispatch("llmdb_query", {"query": QUERY})

    assert {call["model"] for call in provider.calls} == {"gpt-5.4-mini"}


def test_the_estimate_prices_the_configured_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BLUE_DATA_LLM_MODEL", "gpt-4o-mini")
    monkeypatch.setenv("BLUE_DATA_LLM_SOURCE_MAX_TOKENS", "512")

    estimate = tools.dispatch("llmdb_estimate", {"query": QUERY})

    assert estimate["model"] == "gpt-4o-mini"
    assert estimate["bounded"] is True
    assert estimate["max_output_tokens"] == 512


def test_configured_rates_bound_a_model_the_table_does_not_carry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BLUE_DATA_LLM_MODEL", "house-model")
    monkeypatch.setenv("BLUE_DATA_LLM_SOURCE_MAX_TOKENS", "512")
    assert tools.dispatch("llmdb_estimate", {"query": QUERY})["bounded"] is False

    monkeypatch.setenv("BLUE_DATA_LLM_SOURCE_PRICE_PER_INPUT_TOKEN", "1e-6")
    monkeypatch.setenv("BLUE_DATA_LLM_SOURCE_PRICE_PER_OUTPUT_TOKEN", "2e-6")
    assert tools.dispatch("llmdb_estimate", {"query": QUERY})["bounded"] is True
    inspected = tools.dispatch("llmdb_inspect_model", {"model": "house-model"})
    assert inspected["cost_ceiling_supported"] is True


def test_a_ceiling_set_on_one_call_does_not_reach_the_next(
    monkeypatch: pytest.MonkeyPatch, provider: RecordingProvider
) -> None:
    monkeypatch.setenv("BLUE_DATA_LLM_MODEL", "gpt-4o-mini")
    monkeypatch.setenv("BLUE_DATA_LLM_SOURCE_MAX_TOKENS", "512")

    # Below the configured model's per-attempt bound, so the call stops before the provider
    # is contacted: the ceiling was claimed against the configured model.
    stopped = tools.dispatch("llmdb_query", {"query": QUERY, "limits.max_cost": 1e-9})
    assert stopped["meta"]["stopped_by"] == "max_cost"
    assert provider.calls == []

    answered = tools.dispatch("llmdb_query", {"query": QUERY})
    assert answered["meta"].get("stopped_by") is None
    assert provider.calls

    bounded = tools.dispatch("llmdb_estimate", {"query": QUERY, "limits.max_calls": 1})
    unbounded = tools.dispatch("llmdb_estimate", {"query": QUERY})
    assert bounded["max_attempts"] == 1
    assert unbounded["max_attempts"] != 1


@pytest.mark.parametrize(
    ("variable", "value", "message"),
    [
        ("BLUE_DATA_LLM_SOURCE_MAX_TOKENS", "lots", "whole number"),
        ("BLUE_DATA_LLM_SOURCE_MAX_PARALLEL", "1.5", "whole number"),
        ("BLUE_DATA_LLM_SOURCE_TEMPERATURE", "warm", "number"),
        ("BLUE_DATA_LLM_SOURCE_PRICE_PER_INPUT_TOKEN", "cheap", "number"),
        ("BLUE_DATA_LLM_SOURCE_CACHE", "maybe", "true or false"),
    ],
)
@pytest.mark.parametrize(
    ("tool", "arguments"),
    [
        ("llmdb_query", {"query": QUERY}),
        ("llmdb_estimate", {"query": QUERY}),
        ("llmdb_inspect_model", {"model": "gpt-4o"}),
    ],
)
def test_a_value_that_cannot_be_read_fails_the_call_by_name(
    monkeypatch: pytest.MonkeyPatch,
    provider: RecordingProvider,
    variable: str,
    value: str,
    message: str,
    tool: str,
    arguments: dict[str, Any],
) -> None:
    monkeypatch.setenv(variable, value)
    with pytest.raises(tools.ToolError) as caught:
        tools.dispatch(tool, arguments)
    assert caught.value.kind == "ConfigError"
    assert variable in caught.value.message
    assert message in caught.value.message
    assert provider.calls == []


@pytest.mark.parametrize(
    ("variable", "value"),
    [
        ("BLUE_DATA_LLM_SOURCE_MAX_TOKENS", "0"),
        ("BLUE_DATA_LLM_SOURCE_REASONING_EFFORT", "turbo"),
        ("BLUE_DATA_LLM_SOURCE_TIMEOUT", "-1"),
    ],
)
@pytest.mark.parametrize(
    ("tool", "arguments"),
    [
        ("llmdb_query", {"query": QUERY}),
        ("llmdb_estimate", {"query": QUERY}),
        ("llmdb_inspect_model", {"model": "gpt-4o"}),
    ],
)
def test_a_value_the_library_refuses_fails_the_call(
    monkeypatch: pytest.MonkeyPatch,
    provider: RecordingProvider,
    variable: str,
    value: str,
    tool: str,
    arguments: dict[str, Any],
) -> None:
    monkeypatch.setenv(variable, value)
    with pytest.raises(tools.ToolError) as caught:
        tools.dispatch(tool, arguments)
    assert caught.value.kind == "ConfigError"
    assert provider.calls == []


@pytest.mark.parametrize(
    ("variable", "value"),
    [
        ("BLUE_DATA_LLM_SOURCE_MAX_TOKENS", "lots"),
        ("BLUE_DATA_LLM_SOURCE_MAX_TOKENS", "0"),
        ("BLUE_DATA_LLM_SOURCE_REASONING_EFFORT", "turbo"),
        ("BLUE_DATA_LLM_SOURCE_CACHE", "maybe"),
    ],
)
def test_the_metadata_still_answers_under_a_malformed_setting(
    monkeypatch: pytest.MonkeyPatch, variable: str, value: str
) -> None:
    """The version is what an operator asks for when every other call is failing."""
    monkeypatch.setenv(variable, value)
    metadata = tools.dispatch("llmdb_inspect_metadata", {})
    assert metadata["version"] == tools.library_version
    assert metadata["variant"] == "openai"


def test_a_malformed_value_is_masked_in_the_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """A credential pasted into the wrong variable is not read back into a transcript."""
    stamped = "sk-canary-NOTAKEY"
    monkeypatch.setenv("BLUE_DATA_LLM_SOURCE_MAX_TOKENS", stamped)
    with pytest.raises(tools.ToolError) as caught:
        tools.dispatch("llmdb_estimate", {"query": QUERY})
    assert stamped not in caught.value.message
    assert "BLUE_DATA_LLM_SOURCE_MAX_TOKENS" in caught.value.message


# ------------------------------------------------ no configured value comes back --
SENTINEL = "private-value-canary"
"""Not credential-shaped, so masking credential shapes alone would let it through."""


def _published(tool: str, arguments: dict[str, Any]) -> str:
    """Whatever a tool call hands back to a caller: its result, or its error text."""
    try:
        return json.dumps(tools.dispatch(tool, arguments), default=str)
    except tools.ToolError as error:
        return f"{error.kind}: {error.message}"


PROBED_TOOLS: list[tuple[str, dict[str, Any]]] = [
    ("llmdb_query", {"query": QUERY}),
    ("llmdb_estimate", {"query": QUERY}),
    ("llmdb_inspect_model", {"model": "gpt-4o"}),
    ("llmdb_inspect_metadata", {}),
    ("llmdb_list_models", {}),
]


@pytest.mark.parametrize("variable", VARIABLES)
@pytest.mark.parametrize(("tool", "arguments"), PROBED_TOOLS, ids=[t for t, _ in PROBED_TOOLS])
def test_no_tool_returns_a_configured_value(
    monkeypatch: pytest.MonkeyPatch,
    provider: RecordingProvider,
    tmp_path: Path,
    variable: str,
    tool: str,
    arguments: dict[str, Any],
) -> None:
    """A value is private to the deployment whether or not it parses."""
    value = str(tmp_path / SENTINEL) if variable.endswith("CACHE_DIR") else SENTINEL
    monkeypatch.setenv(variable, value)
    published = _published(tool, dict(arguments))
    assert SENTINEL not in published


@pytest.mark.parametrize(
    ("variable", "value"),
    [
        ("BLUE_DATA_LLM_SOURCE_MAX_TOKENS", "-7731"),
        ("BLUE_DATA_LLM_SOURCE_TIMEOUT", "-7731.5"),
        ("BLUE_DATA_LLM_SOURCE_MAX_PARALLEL", "0"),
        ("BLUE_DATA_LLM_SOURCE_TEMPERATURE", "nan"),
        ("BLUE_DATA_LLM_SOURCE_PRICE_PER_INPUT_TOKEN", "-7731"),
        ("BLUE_DATA_LLM_SOURCE_REASONING_EFFORT", "minimal"),
    ],
)
@pytest.mark.parametrize(
    ("tool", "arguments"), PROBED_TOOLS[:3], ids=[t for t, _ in PROBED_TOOLS[:3]]
)
def test_a_value_the_library_refuses_is_not_quoted_back(
    monkeypatch: pytest.MonkeyPatch,
    provider: RecordingProvider,
    variable: str,
    value: str,
    tool: str,
    arguments: dict[str, Any],
) -> None:
    """The library's own refusal quotes what it refused, so it is replaced, not passed on."""
    monkeypatch.setenv(variable, value)
    with pytest.raises(tools.ToolError) as caught:
        tools.dispatch(tool, dict(arguments))
    assert value not in caught.value.message
    assert variable in caught.value.message
    assert provider.calls == []


def test_a_published_model_name_is_still_reported(
    monkeypatch: pytest.MonkeyPatch, provider: RecordingProvider
) -> None:
    monkeypatch.setenv("BLUE_DATA_LLM_MODEL", "gpt-6-sol")
    assert tools.dispatch("llmdb_estimate", {"query": QUERY})["model"] == "gpt-6-sol"
    assert tools.dispatch("llmdb_query", {"query": QUERY})["meta"]["model"] == "gpt-6-sol"


def test_a_private_model_name_is_masked_in_results(
    monkeypatch: pytest.MonkeyPatch, provider: RecordingProvider
) -> None:
    monkeypatch.setenv("BLUE_DATA_LLM_MODEL", SENTINEL)
    estimate = tools.dispatch("llmdb_estimate", {"query": QUERY})
    assert estimate["model"] == MASK
    assert estimate["bounded"] is False
    assert SENTINEL not in str(estimate["reason"])
    assert tools.dispatch("llmdb_query", {"query": QUERY})["meta"]["model"] == MASK
    assert {call["model"] for call in provider.calls} == {SENTINEL}


# ------------------------------------------------------------ operator ceilings --
def test_the_ceiling_variables_are_the_cli_profile_names() -> None:
    assert LIMIT_VARIABLES == [
        "BLUE_DATA_LLM_SOURCE_LIMIT_COST",
        "BLUE_DATA_LLM_SOURCE_LIMIT_CALLS",
        "BLUE_DATA_LLM_SOURCE_LIMIT_SECONDS",
    ]


@pytest.mark.parametrize(
    ("operator", "call", "expected"),
    [
        ({}, {}, None),
        ({"BLUE_DATA_LLM_SOURCE_LIMIT_COST": "0.5"}, {}, {"limits.max_cost": 0.5}),
        (
            {"BLUE_DATA_LLM_SOURCE_LIMIT_COST": "0.5"},
            {"limits.max_cost": 0.2},
            {"limits.max_cost": 0.2},
        ),
        (
            {"BLUE_DATA_LLM_SOURCE_LIMIT_COST": "0.5"},
            {"limits.max_cost": 5.0},
            {"limits.max_cost": 0.5},
        ),
        ({}, {"limits.max_cost": 5.0}, {"limits.max_cost": 5.0}),
        (
            {"BLUE_DATA_LLM_SOURCE_LIMIT_CALLS": "3", "BLUE_DATA_LLM_SOURCE_LIMIT_SECONDS": "60"},
            {"limits.max_calls": 9, "limits.max_seconds": 10},
            {"limits.max_calls": 3, "limits.max_seconds": 10},
        ),
    ],
    ids=[
        "none",
        "operator-only",
        "call-lower-wins",
        "call-higher-clamped",
        "call-only",
        "each-axis",
    ],
)
def test_the_effective_ceiling_is_the_lower_of_operator_and_call(
    monkeypatch: pytest.MonkeyPatch,
    operator: dict[str, str],
    call: dict[str, Any],
    expected: dict[str, Any] | None,
) -> None:
    for variable, value in operator.items():
        monkeypatch.setenv(variable, value)
    assert tools.limit_properties(dict(call)) == expected


def test_an_operator_ceiling_binds_a_call_that_sets_none(
    monkeypatch: pytest.MonkeyPatch, provider: RecordingProvider
) -> None:
    monkeypatch.setenv("BLUE_DATA_LLM_MODEL", "gpt-4o-mini")
    monkeypatch.setenv("BLUE_DATA_LLM_SOURCE_MAX_TOKENS", "512")
    monkeypatch.setenv("BLUE_DATA_LLM_SOURCE_LIMIT_COST", "0.0000001")

    stopped = tools.dispatch("llmdb_query", {"query": QUERY})
    assert stopped["meta"]["stopped_by"] == "max_cost"
    assert provider.calls == []

    raised = tools.dispatch("llmdb_query", {"query": QUERY, "limits.max_cost": 100.0})
    assert raised["meta"]["stopped_by"] == "max_cost"
    assert provider.calls == []

    estimate = tools.dispatch("llmdb_estimate", {"query": QUERY, "limits.max_cost": 100.0})
    assert estimate["attempts_within_ceiling"] == 0


def test_a_call_can_tighten_an_operator_ceiling(
    monkeypatch: pytest.MonkeyPatch, provider: RecordingProvider
) -> None:
    monkeypatch.setenv("BLUE_DATA_LLM_MODEL", "gpt-4o-mini")
    monkeypatch.setenv("BLUE_DATA_LLM_SOURCE_MAX_TOKENS", "512")
    monkeypatch.setenv("BLUE_DATA_LLM_SOURCE_LIMIT_COST", "100")

    answered = tools.dispatch("llmdb_query", {"query": QUERY})
    assert answered["meta"].get("stopped_by") is None
    assert tools.dispatch("llmdb_estimate", {"query": QUERY})["attempts_within_ceiling"] == 3

    tightened = tools.dispatch("llmdb_query", {"query": QUERY, "limits.max_cost": 0.0000001})
    assert tightened["meta"]["stopped_by"] == "max_cost"


def test_an_operator_ceiling_is_read_per_call_and_does_not_linger(
    monkeypatch: pytest.MonkeyPatch, provider: RecordingProvider
) -> None:
    monkeypatch.setenv("BLUE_DATA_LLM_MODEL", "gpt-4o-mini")
    monkeypatch.setenv("BLUE_DATA_LLM_SOURCE_LIMIT_CALLS", "0")
    assert tools.dispatch("llmdb_query", {"query": QUERY})["meta"]["stopped_by"] == "max_calls"

    monkeypatch.delenv("BLUE_DATA_LLM_SOURCE_LIMIT_CALLS")
    assert tools.dispatch("llmdb_query", {"query": QUERY})["meta"].get("stopped_by") is None
    assert tools.dispatch("llmdb_estimate", {"query": QUERY})["attempts_within_ceiling"] is None


@pytest.mark.parametrize(
    ("variable", "value"),
    [
        ("BLUE_DATA_LLM_SOURCE_LIMIT_COST", SENTINEL),
        ("BLUE_DATA_LLM_SOURCE_LIMIT_COST", "-7731"),
        ("BLUE_DATA_LLM_SOURCE_LIMIT_COST", "nan"),
        ("BLUE_DATA_LLM_SOURCE_LIMIT_CALLS", "2.5"),
        ("BLUE_DATA_LLM_SOURCE_LIMIT_CALLS", "-7731"),
        ("BLUE_DATA_LLM_SOURCE_LIMIT_SECONDS", SENTINEL),
    ],
)
@pytest.mark.parametrize(
    ("tool", "arguments"), PROBED_TOOLS[:2], ids=[t for t, _ in PROBED_TOOLS[:2]]
)
def test_a_malformed_operator_ceiling_fails_the_call_without_its_value(
    monkeypatch: pytest.MonkeyPatch,
    provider: RecordingProvider,
    variable: str,
    value: str,
    tool: str,
    arguments: dict[str, Any],
) -> None:
    monkeypatch.setenv(variable, value)
    with pytest.raises(tools.ToolError) as caught:
        tools.dispatch(tool, dict(arguments))
    assert caught.value.kind == "ConfigError"
    assert variable in caught.value.message
    assert value not in caught.value.message
    assert provider.calls == []


@pytest.mark.parametrize("tool", ["llmdb_query", "llmdb_estimate"])
def test_a_retry_allowance_cannot_be_raised_over_mcp(
    tool: str, provider: RecordingProvider
) -> None:
    """The server does not accept a retry allowance at all, so the cap cannot be passed here."""
    with pytest.raises(tools.ToolError) as caught:
        tools.dispatch(tool, {"query": QUERY, "limits.max_retries": 10**309})
    assert caught.value.kind == "InvalidArguments"
    assert provider.calls == []


@pytest.mark.parametrize("variable", LIMIT_VARIABLES)
@pytest.mark.parametrize("value", ["", " ", "\t\n"])
@pytest.mark.parametrize("asynchronous", [False, True])
def test_blank_operator_limits_fail_before_contact_and_recover(
    monkeypatch: pytest.MonkeyPatch,
    provider: RecordingProvider,
    variable: str,
    value: str,
    asynchronous: bool,
) -> None:
    import asyncio

    def call() -> Any:
        args = {"query": QUERY}
        if asynchronous:
            return asyncio.run(tools.adispatch("llmdb_query", args))
        return tools.dispatch("llmdb_query", args)

    monkeypatch.setenv(variable, value)
    with pytest.raises(tools.ToolError) as caught:
        call()
    assert caught.value.kind == "ConfigError"
    assert variable in str(caught.value)
    assert not provider.calls
    monkeypatch.delenv(variable)
    assert call()["records"]
    assert len(provider.calls) == 1


@pytest.mark.parametrize("variable", LIMIT_VARIABLES)
@pytest.mark.parametrize("value", ["-1", "nan", "inf", "-inf", "malformed"])
def test_every_operator_axis_rejects_invalid_values_safely(variable: str, value: str) -> None:
    with pytest.raises(tools.ToolError) as caught:
        tools.operator_limits({variable: value})
    assert caught.value.kind == "ConfigError"
    assert variable in str(caught.value)
    assert value not in str(caught.value)


@pytest.mark.parametrize("variable", LIMIT_VARIABLES)
def test_every_operator_axis_accepts_zero_and_distinguishes_absence(variable: str) -> None:
    assert tools.operator_limits({}) == {}
    result = tools.operator_limits({variable: "0"})
    assert len(result) == 1
    assert next(iter(result.values())) == 0


@pytest.mark.parametrize("asynchronous", [False, True])
def test_operator_call_and_source_ceilings_compose(
    monkeypatch: pytest.MonkeyPatch, make_source: Any, asynchronous: bool
) -> None:
    import asyncio

    monkeypatch.setenv("BLUE_DATA_LLM_SOURCE_LIMIT_COST", "1")
    source, client = make_source()
    source.properties["limits.max_cost"] = 0
    arguments = {"query": QUERY, "limits.max_cost": 2}
    result = (
        asyncio.run(tools.adispatch("llmdb_query", arguments, source=source))
        if asynchronous
        else tools.dispatch("llmdb_query", arguments, source=source)
    )
    assert result["records"] == []
    assert result["meta"]["stopped_by"] == "max_cost"
    assert not client.prompts
    assert source.properties["limits.max_cost"] == 0
    assert arguments["limits.max_cost"] == 2
