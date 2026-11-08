"""What the tools show of a caller's text, against what the deployment keeps private.

The text is the caller's own on its way back out, in ``llmdb_inspect_model``'s display or in
an error that quotes it, but the deployment's private values (a model name the package does
not publish, the cache directory) are masked wherever they would appear. The display is
masked; every lookup still reads the name as it arrived.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path
from typing import Any

import blue_data_llm_source
import pytest
from blue_data_llm_source import priceable_models
from blue_data_llm_source.llm.models import published_defaults
from blue_data_llm_source.llm.pricing import rates_for
from blue_data_llm_source.redaction import MASK

from blue_data_llm_mcp import tools

PRIVATE_MODEL = "synthetic-private-model"
# Obviously fake. The credential's name makes the library treat its value as key material.
FAKE_KEY = "FAKE_CREDENTIAL_FRAGMENT_7"
INSPECT = "llmdb_inspect_model"


def inspect(model: str) -> dict[str, Any]:
    return tools.dispatch(INSPECT, {"model": model})


def refused(name: str, arguments: dict[str, Any]) -> tools.ToolError:
    """The error a call is refused with."""
    with pytest.raises(tools.ToolError) as caught:
        tools.dispatch(name, arguments)
    return caught.value


def test_a_configured_private_model_is_masked_in_the_display(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BLUE_DATA_LLM_MODEL", PRIVATE_MODEL)

    shown = inspect(PRIVATE_MODEL)

    assert shown["model"] == MASK
    assert PRIVATE_MODEL not in json.dumps(shown)
    assert shown["priceable"] is False
    assert shown["published_defaults"] is None


async def test_the_async_path_masks_the_same_way(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BLUE_DATA_LLM_MODEL", PRIVATE_MODEL)

    shown = await tools.adispatch(INSPECT, {"model": PRIVATE_MODEL})

    assert shown["model"] == MASK


def test_the_configured_cache_directory_is_masked_in_the_display(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cache = str(tmp_path / "private-cache")
    monkeypatch.setenv("BLUE_DATA_LLM_SOURCE_CACHE_DIR", cache)

    shown = inspect(cache)

    assert shown["model"] == MASK
    assert cache not in json.dumps(shown)


def test_a_private_value_inside_the_input_is_masked_where_it_sits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BLUE_DATA_LLM_MODEL", PRIVATE_MODEL)

    assert inspect(f"vendor/{PRIVATE_MODEL}@v2")["model"] == f"vendor/{MASK}@v2"


def test_a_public_or_unrelated_model_is_still_shown_as_asked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BLUE_DATA_LLM_MODEL", PRIVATE_MODEL)
    monkeypatch.setenv("BLUE_DATA_LLM_SOURCE_CACHE_DIR", "/private/cache")

    assert inspect("gpt-4o")["model"] == "gpt-4o"
    assert inspect("nobody-prices-this")["model"] == "nobody-prices-this"


def test_a_published_name_configured_as_the_model_stays_visible(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The package publishes it, so it is not the operator's to conceal.
    monkeypatch.setenv("BLUE_DATA_LLM_MODEL", "gpt-4o")

    assert inspect("gpt-4o")["model"] == "gpt-4o"


def test_a_credential_inside_a_private_model_does_not_leave_a_prefix_and_suffix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    private = f"team-{FAKE_KEY}-model"
    monkeypatch.setenv("OPENAI_API_KEY", FAKE_KEY)
    monkeypatch.setenv("BLUE_DATA_LLM_MODEL", private)

    shown = inspect(private)

    # Masking the credential first would leave "team-***-model", which no longer contains
    # the private value, so nothing would conceal the rest of it.
    assert shown["model"] == MASK
    assert "team-" not in json.dumps(shown)
    assert "-model" not in json.dumps(shown)


# Pieces of FAKE_KEY. Configured as a private value, each one sits inside the credential.
INSIDE_THE_CREDENTIAL = ("CREDENTIAL", "CREDENTIAL_FRAGMENT", "FAKE_CRED")


@pytest.mark.parametrize("setting", ["BLUE_DATA_LLM_SOURCE_CACHE_DIR", "BLUE_DATA_LLM_MODEL"])
@pytest.mark.parametrize("piece", INSIDE_THE_CREDENTIAL)
def test_a_private_value_inside_a_credential_does_not_leave_its_ends_readable(
    monkeypatch: pytest.MonkeyPatch, setting: str, piece: str
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", FAKE_KEY)
    monkeypatch.setenv(setting, piece)

    shown = inspect(FAKE_KEY)

    # Concealing first would turn the credential into "FAKE_***_FRAGMENT_7", which no longer
    # contains it, so nothing would mask the prefix and suffix that are left.
    assert shown["model"] == MASK
    everything = json.dumps(shown)
    assert FAKE_KEY not in everything
    for readable in ("FAKE", "FRAGMENT", "_7"):
        assert readable not in everything


def test_a_private_value_inside_a_credential_is_masked_when_it_is_part_of_longer_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", FAKE_KEY)
    monkeypatch.setenv("BLUE_DATA_LLM_SOURCE_CACHE_DIR", "CREDENTIAL")

    shown = inspect(f"use {FAKE_KEY} please")

    assert shown["model"] == MASK
    assert "FAKE" not in json.dumps(shown)
    assert "please" not in json.dumps(shown)


def test_a_credential_beside_a_private_value_masks_the_whole_field(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The two do not touch, but whether they do cannot be told once one is masked, so the
    # field is not partly shown.
    monkeypatch.setenv("OPENAI_API_KEY", FAKE_KEY)
    monkeypatch.setenv("BLUE_DATA_LLM_MODEL", PRIVATE_MODEL)

    shown = inspect(f"{PRIVATE_MODEL} with {FAKE_KEY}")

    assert shown["model"] == MASK


def test_a_credential_alone_in_the_input_is_masked_where_it_sits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", FAKE_KEY)
    monkeypatch.setenv("BLUE_DATA_LLM_MODEL", PRIVATE_MODEL)

    assert inspect(f"use {FAKE_KEY} here")["model"] == f"use {MASK} here"


@pytest.mark.parametrize("setting", ["BLUE_DATA_LLM_MODEL", "BLUE_DATA_LLM_SOURCE_CACHE_DIR"])
def test_a_private_value_in_the_callers_text_is_masked_where_it_sits_beside_a_credential_setting(
    monkeypatch: pytest.MonkeyPatch, setting: str
) -> None:
    # A credential is configured but is not in the input, so only the private value is.
    monkeypatch.setenv("OPENAI_API_KEY", FAKE_KEY)
    monkeypatch.setenv(setting, PRIVATE_MODEL)

    shown = inspect(f"please price {PRIVATE_MODEL} now")

    assert shown["model"] == f"please price {MASK} now"


def test_ordinary_names_are_shown_as_asked_whatever_is_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", FAKE_KEY)
    monkeypatch.setenv("BLUE_DATA_LLM_MODEL", PRIVATE_MODEL)
    monkeypatch.setenv("BLUE_DATA_LLM_SOURCE_CACHE_DIR", "/private/cache")

    for name in ("gpt-4o", "nobody-prices-this", "vendor/model@v2", "credential model"):
        assert inspect(name)["model"] == name
    assert inspect("gpt-4o")["priceable"] is True


def test_masked_overlaps_still_answer_from_the_name_as_it_arrived(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # "gpt-4o" is a credential here and "gpt-4" a private value inside it, so the display is
    # masked by both. The price table is still asked about "gpt-4o".
    plain = inspect("gpt-4o")
    monkeypatch.setenv("OPENAI_API_KEY", "gpt-4o")
    monkeypatch.setenv("BLUE_DATA_LLM_SOURCE_CACHE_DIR", "gpt-4")

    shown = inspect("gpt-4o")

    assert shown["model"] == MASK
    assert {key: value for key, value in shown.items() if key != "model"} == {
        key: value for key, value in plain.items() if key != "model"
    }
    rates = rates_for("gpt-4o")
    assert rates is not None
    assert (shown["price_per_input_token"], shown["price_per_output_token"]) == rates
    assert shown["published_defaults"] == published_defaults("gpt-4o")


def test_the_lookups_read_the_name_as_it_arrived_not_as_it_is_shown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A cache directory that happens to read like a published model, so the display of that
    # model is masked while everything about it is still answered from the real table.
    assert "gpt-4o" in priceable_models()
    plain = inspect("gpt-4o")
    assert plain["priceable"] is True
    monkeypatch.setenv("BLUE_DATA_LLM_SOURCE_CACHE_DIR", "gpt-4o")

    shown = inspect("gpt-4o")

    assert shown["model"] == MASK
    assert {key: value for key, value in shown.items() if key != "model"} == {
        key: value for key, value in plain.items() if key != "model"
    }
    rates = rates_for("gpt-4o")
    assert rates is not None
    assert (shown["price_per_input_token"], shown["price_per_output_token"]) == rates
    assert shown["published_defaults"] == published_defaults("gpt-4o")
    assert shown["cost_ceiling_supported"] is True


# An argument name is the caller's own text, and the validator quotes it back when it refuses it.
UNKNOWN = "unknown argument"


def test_a_credential_inside_a_private_value_does_not_leave_its_ends_in_an_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    private = f"team-{FAKE_KEY}-model"
    monkeypatch.setenv("OPENAI_API_KEY", FAKE_KEY)
    monkeypatch.setenv("BLUE_DATA_LLM_MODEL", private)

    error = refused("llmdb_query", {private: 1})

    assert error.kind == tools.INVALID_ARGUMENTS
    assert error.message == MASK
    for text in (error.message, str(error)):
        assert "team-" not in text
        assert "-model" not in text
        assert FAKE_KEY not in text


@pytest.mark.parametrize("setting", ["BLUE_DATA_LLM_SOURCE_CACHE_DIR", "BLUE_DATA_LLM_MODEL"])
@pytest.mark.parametrize("piece", INSIDE_THE_CREDENTIAL)
def test_a_private_value_inside_a_credential_does_not_leave_its_ends_in_an_error(
    monkeypatch: pytest.MonkeyPatch, setting: str, piece: str
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", FAKE_KEY)
    monkeypatch.setenv(setting, piece)

    error = refused("llmdb_query", {FAKE_KEY: 1})

    assert error.kind == tools.INVALID_ARGUMENTS
    assert error.message == MASK
    for text in (error.message, str(error)):
        for readable in ("FAKE", "FRAGMENT", "_7", FAKE_KEY):
            assert readable not in text


def test_an_unknown_tool_name_is_masked_the_same_way(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", FAKE_KEY)
    monkeypatch.setenv("BLUE_DATA_LLM_SOURCE_CACHE_DIR", "CREDENTIAL")

    error = refused(f"run-{FAKE_KEY}", {})

    assert error.kind == tools.UNKNOWN_TOOL
    assert error.message == MASK


@pytest.mark.parametrize("setting", ["BLUE_DATA_LLM_MODEL", "BLUE_DATA_LLM_SOURCE_CACHE_DIR"])
def test_an_error_masks_a_private_value_where_it_sits_when_no_credential_is_in_it(
    monkeypatch: pytest.MonkeyPatch, setting: str
) -> None:
    # A credential is configured but is not in the message, so only the private value is.
    monkeypatch.setenv("OPENAI_API_KEY", FAKE_KEY)
    monkeypatch.setenv(setting, PRIVATE_MODEL)

    error = refused("llmdb_query", {PRIVATE_MODEL: 1})

    assert error.kind == tools.INVALID_ARGUMENTS
    assert error.message.startswith(f"{UNKNOWN} {MASK} for tool 'llmdb_query'")
    assert PRIVATE_MODEL not in error.message


def test_an_error_masks_a_credential_where_it_sits_when_no_private_value_is_in_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", FAKE_KEY)
    monkeypatch.setenv("BLUE_DATA_LLM_MODEL", PRIVATE_MODEL)

    error = refused("llmdb_query", {FAKE_KEY: 1})

    assert error.message.startswith(f"{UNKNOWN} {MASK} for tool 'llmdb_query'")
    assert FAKE_KEY not in error.message


def test_an_ordinary_typed_error_keeps_its_kind_and_its_useful_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plain = refused(INSPECT, {"model": 3})
    monkeypatch.setenv("OPENAI_API_KEY", FAKE_KEY)
    monkeypatch.setenv("BLUE_DATA_LLM_MODEL", PRIVATE_MODEL)
    monkeypatch.setenv("BLUE_DATA_LLM_SOURCE_CACHE_DIR", "/private/cache")

    configured = refused(INSPECT, {"model": 3})
    missing = refused(INSPECT, {})
    unknown = refused("not-a-tool", {})

    assert plain.kind == configured.kind == tools.INVALID_ARGUMENTS
    assert configured.message == plain.message
    assert "'model' must be a string" in configured.message
    assert MASK not in configured.message
    assert missing.kind == tools.INVALID_ARGUMENTS
    assert missing.message == f"tool {INSPECT!r} requires 'model'"
    assert unknown.kind == tools.UNKNOWN_TOOL
    assert "unknown tool 'not-a-tool'" in unknown.message
    assert INSPECT in unknown.message


def server_parameters(tmp_path: Path, **settings: str) -> Any:
    """A real server process over stdio, with a home and a cache of its own.

    Only what the test names reaches the child, and the model is never contacted: the tools
    exercised here answer from the package's tables.
    """
    from mcp import StdioServerParameters

    for directory in ("home", "tmp", "cache", "profiles"):
        (tmp_path / directory).mkdir(exist_ok=True)
    sdk_spec = importlib.util.find_spec("blue_data")
    assert sdk_spec is not None and sdk_spec.origin is not None
    paths = [
        str(Path(tools.__file__).parents[1].resolve()),
        str(Path(blue_data_llm_source.__file__).resolve().parents[1]),
        str(Path(sdk_spec.origin).resolve().parents[1]),
    ]
    paths.extend(
        str(Path(path).resolve())
        for path in os.environ.get("PYTHONPATH", "").split(os.pathsep)
        if path
    )
    env = {key: os.environ[key] for key in ("PATH", "SYSTEMROOT", "WINDIR") if key in os.environ}
    env.update(
        PYTHONPATH=os.pathsep.join(paths),
        HOME=str(tmp_path / "home"),
        TMPDIR=str(tmp_path / "tmp"),
        XDG_CACHE_HOME=str(tmp_path / "cache"),
        BLUE_DATA_LLM_SOURCE_HOME=str(tmp_path / "profiles"),
        PYTHONDONTWRITEBYTECODE="1",
        LITELLM_LOCAL_MODEL_COST_MAP="True",
        LITELLM_DISABLE_DOTENV="True",
        **settings,
    )
    return StdioServerParameters(
        command=sys.executable,
        args=["-B", "-m", "blue_data_llm_mcp.server"],
        env=env,
        cwd=str(tmp_path),
    )


async def call_server(parameters: Any, calls: list[tuple[str, dict[str, Any]]]) -> list[Any]:
    """Each call's decoded answer, from the real MCP SDK client against a real server."""
    import anyio
    from mcp import ClientSession
    from mcp.client.stdio import stdio_client

    answers: list[Any] = []
    with anyio.fail_after(60):
        async with stdio_client(parameters) as (read, write), ClientSession(read, write) as session:
            await session.initialize()
            for name, arguments in calls:
                result = await session.call_tool(name, arguments)
                assert not result.isError, result.content
                answers.append(json.loads(getattr(result.content[0], "text")))
    return answers


@pytest.mark.parametrize(
    ("cache", "nested_model"),
    [
        ("deployment-cache", f"{MASK}/inner"),
        ("deployment-cache-for-tests-20261107", MASK),
    ],
    ids=["plain-path", "credential-shaped-path"],
)
async def test_a_real_client_is_shown_the_masked_display_over_stdio(
    tmp_path: Path, cache: str, nested_model: str
) -> None:
    pytest.importorskip("mcp")
    # Relative paths keep random temporary-directory names out of the masking oracle.
    private = f"team-{FAKE_KEY}-model"
    parameters = server_parameters(
        tmp_path,
        BLUE_DATA_LLM_MODEL=private,
        BLUE_DATA_LLM_SOURCE_CACHE_DIR=cache,
        OPENAI_API_KEY=FAKE_KEY,
    )
    asked = [private, cache, f"{cache}/inner", "gpt-4o", "nobody-prices-this"]

    answers = await call_server(parameters, [(INSPECT, {"model": name}) for name in asked])

    assert [answer["model"] for answer in answers] == [
        MASK,
        MASK,
        nested_model,
        "gpt-4o",
        "nobody-prices-this",
    ]
    for answer in answers[:3]:
        assert answer["priceable"] is False
    assert answers[3]["priceable"] is True
    everything = json.dumps(answers)
    assert private not in everything
    assert cache not in everything
    assert FAKE_KEY not in everything


async def test_a_real_client_is_shown_no_part_of_a_credential_holding_a_private_value(
    tmp_path: Path,
) -> None:
    pytest.importorskip("mcp")
    parameters = server_parameters(
        tmp_path,
        BLUE_DATA_LLM_SOURCE_CACHE_DIR="CREDENTIAL",
        OPENAI_API_KEY=FAKE_KEY,
    )

    answers = await call_server(
        parameters,
        [(INSPECT, {"model": FAKE_KEY}), (INSPECT, {"model": "gpt-4o"})],
    )

    assert answers[0]["model"] == MASK
    assert "FAKE" not in json.dumps(answers[0])
    assert "FRAGMENT" not in json.dumps(answers[0])
    assert answers[1]["model"] == "gpt-4o"
    assert answers[1]["priceable"] is True


async def test_an_operator_ceiling_still_binds_a_call_made_through_the_real_server(
    tmp_path: Path,
) -> None:
    pytest.importorskip("mcp")
    # Zero calls allowed, so the query stops before any model is reached.
    parameters = server_parameters(tmp_path, BLUE_DATA_LLM_SOURCE_LIMIT_CALLS="0")

    (answer,) = await call_server(
        parameters, [("llmdb_query", {"query": "the member states of the European Union"})]
    )

    assert answer["records"] == []
    assert answer["meta"]["calls"] == 0
    assert answer["meta"]["stopped_by"] == "max_calls"


async def test_a_real_client_is_refused_without_the_private_value_it_sent_as_an_argument(
    tmp_path: Path,
) -> None:
    pytest.importorskip("mcp")
    import anyio
    from mcp import ClientSession
    from mcp.client.stdio import stdio_client

    private = f"team-{FAKE_KEY}-model"
    parameters = server_parameters(tmp_path, BLUE_DATA_LLM_MODEL=private, OPENAI_API_KEY=FAKE_KEY)
    refusals: list[tuple[bool, str]] = []
    with anyio.fail_after(60):
        async with stdio_client(parameters) as (read, write), ClientSession(read, write) as session:
            await session.initialize()
            for arguments in ({private: 1}, {FAKE_KEY: 1}, {"model": 3}):
                result = await session.call_tool(INSPECT, arguments)
                refusals.append((result.isError, getattr(result.content[0], "text")))

    assert [failed for failed, _ in refusals] == [True, True, True]
    assert refusals[0][1] == f"InvalidArguments: {MASK}"
    # A credential alone is masked where it sits, and the rest of the refusal still reads.
    assert refusals[1][1].startswith(f"InvalidArguments: {UNKNOWN} {MASK} for tool")
    # The control: an ordinary refusal still says what is wrong.
    assert refusals[2][1].startswith("InvalidArguments: 'model' must be a string")
    everything = json.dumps(refusals)
    for readable in (private, FAKE_KEY, "team-", "-model", "FAKE", "FRAGMENT"):
        assert readable not in everything
