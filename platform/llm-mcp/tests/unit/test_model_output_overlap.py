"""Private model names stay hidden in query metadata and estimates.

Exercise both overlap directions, including model names already masked by the library,
through real source methods and local provider/MCP transports.
"""

from __future__ import annotations

import importlib.util
import json
import math
import os
import sys
import threading
from collections.abc import Callable, Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, cast

import blue_data_llm_source
import pytest
from blue_data_llm_source import Estimate, LlmSource, meta_view
from blue_data_llm_source.llm.client import DEFAULT_MODEL
from blue_data_llm_source.redaction import MASK

from blue_data_llm_mcp import tools

FAKE_KEY = "FAKE_CREDENTIAL_FRAGMENT_7"
# "gpt-" so that a real client routes the name to the OpenAI-compatible path; the package
# publishes no such name, so it is the operator's private model.
CREDENTIAL_IN_MODEL = f"gpt-team-{FAKE_KEY}-model"
PRIVATE_MODEL = "gpt-synthetic-private-model"
QUERY = "the member states of the European Union"
ATTRS = ["country"]

# Readable pieces of the private values below. None may survive in any answer.
LEFTOVERS = ("gpt-team", "team-", "-model", "FAKE", "CREDENTIAL", "FRAGMENT", "_7")

# Each case: configured environment, and the model text the answer would carry.
Case = tuple[dict[str, str], str]
OVERLAPS: dict[str, Case] = {
    # The credential sits inside the private model name.
    "credential-inside-private-model": (
        {"OPENAI_API_KEY": FAKE_KEY, "BLUE_DATA_LLM_MODEL": CREDENTIAL_IN_MODEL},
        CREDENTIAL_IN_MODEL,
    ),
    # A private value sits wholly inside the credential, which the text carries with more.
    "private-inside-credential": (
        {"OPENAI_API_KEY": FAKE_KEY, "BLUE_DATA_LLM_SOURCE_CACHE_DIR": "CREDENTIAL"},
        CREDENTIAL_IN_MODEL,
    ),
    # A private value starts before the credential and ends inside it.
    "private-straddles-credential": (
        {"OPENAI_API_KEY": FAKE_KEY, "BLUE_DATA_LLM_SOURCE_CACHE_DIR": "team-FAKE"},
        f"gpt-team-{FAKE_KEY}",
    ),
    # The same, from the other end of the credential.
    "private-straddles-credential-end": (
        {"OPENAI_API_KEY": FAKE_KEY, "BLUE_DATA_LLM_SOURCE_CACHE_DIR": "FRAGMENT_7-model"},
        f"gpt-{FAKE_KEY}-model",
    ),
}
# What a client that masked its own answer would hand over: the credential already a mask.
ALREADY_MASKED: dict[str, Case] = {
    "masked-inside-private-model": (
        {"OPENAI_API_KEY": FAKE_KEY, "BLUE_DATA_LLM_MODEL": CREDENTIAL_IN_MODEL},
        f"gpt-team-{MASK}-model",
    ),
    "masked-beside-private-cache": (
        {"OPENAI_API_KEY": FAKE_KEY, "BLUE_DATA_LLM_SOURCE_CACHE_DIR": "team-FAKE"},
        f"gpt-team-{MASK}",
    ),
}


def configure(monkeypatch: pytest.MonkeyPatch, environment: dict[str, str]) -> None:
    for name, value in environment.items():
        monkeypatch.setenv(name, value)


def assert_nothing_private_shown(answer: Any) -> None:
    everything = json.dumps(answer)
    for readable in (*LEFTOVERS, CREDENTIAL_IN_MODEL):
        assert readable not in everything, readable


def estimate_environment() -> dict[str, str]:
    """Settings that let a real source bound a cost for a model nobody publishes rates for."""
    return {
        "BLUE_DATA_LLM_SOURCE_MAX_TOKENS": "400",
        "BLUE_DATA_LLM_SOURCE_PRICE_PER_INPUT_TOKEN": "0.000003",
        "BLUE_DATA_LLM_SOURCE_PRICE_PER_OUTPUT_TOKEN": "0.000011",
    }


def without_model(answer: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in answer.items() if key != "model"}


# ---------------------------------------------------------------- estimate, the real source


@pytest.mark.parametrize("case", ["credential-inside-private-model"])
def test_a_real_estimate_masks_a_private_model_holding_a_credential(
    monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    environment, _ = OVERLAPS[case]
    configure(monkeypatch, {**estimate_environment(), **environment})

    answer = tools.dispatch("llmdb_estimate", {"query": QUERY, "attrs": ATTRS})

    assert answer["model"] == MASK
    assert_nothing_private_shown(answer)


@pytest.mark.parametrize("setting", ["BLUE_DATA_LLM_SOURCE_CACHE_DIR"])
@pytest.mark.parametrize("piece", ["CREDENTIAL", "team-FAKE", "FRAGMENT_7-model"])
def test_a_real_estimate_masks_a_model_overlapping_another_private_value(
    monkeypatch: pytest.MonkeyPatch, setting: str, piece: str
) -> None:
    # The model is itself private, and a second private value overlaps its credential.
    configure(
        monkeypatch,
        {
            **estimate_environment(),
            "OPENAI_API_KEY": FAKE_KEY,
            "BLUE_DATA_LLM_MODEL": CREDENTIAL_IN_MODEL,
            setting: piece,
        },
    )

    answer = tools.dispatch("llmdb_estimate", {"query": QUERY, "attrs": ATTRS})

    assert answer["model"] == MASK
    assert_nothing_private_shown(answer)


async def test_a_real_estimate_masks_the_same_way_on_the_async_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    environment, _ = OVERLAPS["credential-inside-private-model"]
    configure(monkeypatch, {**estimate_environment(), **environment})

    answer = await tools.adispatch("llmdb_estimate", {"query": QUERY, "attrs": ATTRS})

    assert answer["model"] == MASK
    assert_nothing_private_shown(answer)


def test_a_real_estimate_keeps_every_number_and_the_pricing_it_was_given(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure(monkeypatch, estimate_environment())
    monkeypatch.setenv("BLUE_DATA_LLM_MODEL", "gpt-4o")
    control = tools.dispatch("llmdb_estimate", {"query": QUERY, "attrs": ATTRS})
    assert control["model"] == "gpt-4o"
    assert control["bounded"] is True

    environment, _ = OVERLAPS["credential-inside-private-model"]
    configure(monkeypatch, environment)
    answer = tools.dispatch("llmdb_estimate", {"query": QUERY, "attrs": ATTRS})

    assert answer["model"] == MASK
    # Only the name differs: the allowance and the rates are the deployment's, not the model's.
    assert without_model(answer) == without_model(control)
    library = LlmSource(properties=tools.deployment_properties()).estimate(QUERY, attrs=ATTRS)
    assert answer["max_cost"] == library.max_cost
    assert answer["max_cost_per_attempt"] == library.max_cost_per_attempt
    assert answer["max_attempts"] == library.max_attempts
    assert answer["prompt_bytes"] == library.prompt_bytes
    assert answer["max_output_tokens"] == 400
    assert answer["attempts_within_ceiling"] == library.attempts_within_ceiling
    assert library.model == "gpt-team-***-model"  # the library's own masking is unchanged


def test_a_real_estimate_keeps_its_ceiling_arithmetic_under_a_masked_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    environment, _ = OVERLAPS["credential-inside-private-model"]
    configure(monkeypatch, {**estimate_environment(), **environment})
    first = tools.dispatch("llmdb_estimate", {"query": QUERY, "attrs": ATTRS})

    # A ceiling at the figure published admits every attempt the estimate counts.
    again = tools.dispatch(
        "llmdb_estimate",
        {"query": QUERY, "attrs": ATTRS, "limits.max_cost": first["max_cost"]},
    )

    assert again["model"] == MASK
    assert again["attempts_within_ceiling"] == first["max_attempts"]
    assert again["max_cost"] == first["max_cost"]


@pytest.mark.parametrize(
    ("environment", "expected"),
    [
        # Public model names are shown as configured, whatever else is private or secret.
        ({"BLUE_DATA_LLM_MODEL": "gpt-4o", "OPENAI_API_KEY": FAKE_KEY}, "gpt-4o"),
        (
            {
                "BLUE_DATA_LLM_MODEL": "gpt-4o",
                "OPENAI_API_KEY": FAKE_KEY,
                "BLUE_DATA_LLM_SOURCE_CACHE_DIR": "CREDENTIAL",
            },
            "gpt-4o",
        ),
        # A private-only configuration is masked as before.
        ({"BLUE_DATA_LLM_MODEL": PRIVATE_MODEL}, MASK),
        ({"BLUE_DATA_LLM_MODEL": PRIVATE_MODEL, "OPENAI_API_KEY": FAKE_KEY}, MASK),
        # A credential used as the model name was always shown as a mask.
        ({"BLUE_DATA_LLM_MODEL": FAKE_KEY, "OPENAI_API_KEY": FAKE_KEY}, MASK),
        (
            {
                "BLUE_DATA_LLM_MODEL": FAKE_KEY,
                "OPENAI_API_KEY": FAKE_KEY,
                "BLUE_DATA_LLM_SOURCE_CACHE_DIR": "team-FAKE",
            },
            MASK,
        ),
        # No model configured: the package's own default is published, so it is shown.
        (
            {"OPENAI_API_KEY": FAKE_KEY, "BLUE_DATA_LLM_SOURCE_CACHE_DIR": "CREDENTIAL"},
            DEFAULT_MODEL,
        ),
    ],
)
def test_real_estimate_controls_keep_their_existing_behavior(
    monkeypatch: pytest.MonkeyPatch, environment: dict[str, str], expected: str
) -> None:
    configure(monkeypatch, {**estimate_environment(), **environment})

    answer = tools.dispatch("llmdb_estimate", {"query": QUERY, "attrs": ATTRS})

    assert answer["model"] == expected
    assert FAKE_KEY not in json.dumps(answer)
    assert PRIVATE_MODEL not in json.dumps(answer)


def test_an_estimate_carrying_an_already_masked_model_is_masked_whole(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # What the real source hands over: the credential already a mask, with a private prefix and
    # suffix around it. The summary is given the library's own type, not a stand-in for it.
    environment, _ = ALREADY_MASKED["masked-inside-private-model"]
    configure(monkeypatch, environment)

    summary = tools.estimate_summary(
        Estimate(bounded=True, max_cost=0.5, max_cost_per_attempt=0.25, model="gpt-team-***-model")
    )

    assert summary["model"] == MASK
    assert summary["max_cost"] == 0.5
    assert summary["max_cost_per_attempt"] == 0.25
    assert summary["bounded"] is True


def test_the_summary_of_an_unbounded_estimate_keeps_its_typed_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    environment, _ = OVERLAPS["credential-inside-private-model"]
    configure(monkeypatch, environment)  # a private model with no rates and no allowance

    answer = tools.dispatch("llmdb_estimate", {"query": QUERY, "attrs": ATTRS})

    assert answer["bounded"] is False
    assert answer["model"] == MASK
    assert answer["reason"] == "Model pricing is unavailable; set finite input and output rates."
    assert_nothing_private_shown(answer)


# ---------------------------------------------------------------- query, a client we control


def run_query(
    how: str, source: LlmSource, arguments: dict[str, Any] | None = None
) -> dict[str, Any]:
    """The query through one of the four entry points, all of which end in one result shape."""
    import asyncio

    args = {"query": QUERY, "attrs": ATTRS, **(arguments or {})}
    if how == "sync":
        return tools.llmdb_query(args, source=source)
    if how == "dispatch":
        return tools.dispatch("llmdb_query", args, source=source)
    if how == "async":
        return asyncio.run(tools.llmdb_query_async(args, source=source))
    return asyncio.run(tools.adispatch("llmdb_query", args, source=source))


ENTRY_POINTS = ["sync", "dispatch", "async", "adispatch"]


@pytest.mark.parametrize("how", ENTRY_POINTS)
@pytest.mark.parametrize("case", [*OVERLAPS, *ALREADY_MASKED])
def test_a_query_masks_the_whole_model_when_a_private_value_overlaps_a_credential(
    monkeypatch: pytest.MonkeyPatch,
    make_source: Callable[..., tuple[LlmSource, Any]],
    how: str,
    case: str,
) -> None:
    environment, answered = {**OVERLAPS, **ALREADY_MASKED}[case]
    configure(monkeypatch, environment)
    source, _ = make_source(model=answered)

    result = run_query(how, source)

    assert result["meta"]["model"] == MASK
    assert_nothing_private_shown(result["meta"])


@pytest.mark.parametrize("how", ENTRY_POINTS)
def test_a_query_keeps_its_records_and_numbers_under_a_masked_model(
    monkeypatch: pytest.MonkeyPatch,
    make_source: Callable[..., tuple[LlmSource, Any]],
    how: str,
) -> None:
    control_source, _ = make_source(model="gpt-4o")
    control = run_query(how, control_source)
    environment, answered = OVERLAPS["credential-inside-private-model"]
    configure(monkeypatch, environment)
    source, client = make_source(model=answered)

    result = run_query(how, source)

    assert result["meta"]["model"] == MASK
    assert control["meta"]["model"] == "gpt-4o"
    assert result["records"] == control["records"]
    assert result["records"] != []
    for metadata in (control["meta"], result["meta"]):
        elapsed = metadata["elapsed_seconds"]
        assert isinstance(elapsed, (int, float)) and not isinstance(elapsed, bool)
        assert math.isfinite(elapsed) and elapsed >= 0
    # Elapsed time belongs to each request, so compare every other metadata field.
    volatile = {"model", "elapsed_seconds"}
    assert {key: value for key, value in result["meta"].items() if key not in volatile} == {
        key: value for key, value in control["meta"].items() if key not in volatile
    }
    assert result["meta"]["cost"] == client.cost
    assert result["meta"]["prompt_tokens"] == client.prompt_tokens
    assert result["meta"]["completion_tokens"] == client.completion_tokens
    assert result["meta"]["calls"] == 1


@pytest.mark.parametrize("how", ENTRY_POINTS)
@pytest.mark.parametrize(
    ("environment", "answered", "expected"),
    [
        # A private value alone is still masked where it sits, and the rest still reads.
        (
            {"OPENAI_API_KEY": FAKE_KEY, "BLUE_DATA_LLM_MODEL": PRIVATE_MODEL},
            f"vendor/{PRIVATE_MODEL}@v2",
            f"vendor/{MASK}@v2",
        ),
        (
            {"BLUE_DATA_LLM_SOURCE_CACHE_DIR": "/srv/private-cache"},
            "mirror//srv/private-cache/x",
            f"mirror/{MASK}/x",
        ),
        ({"BLUE_DATA_LLM_MODEL": PRIVATE_MODEL}, PRIVATE_MODEL, MASK),
        # A credential alone is masked where it sits.
        (
            {"OPENAI_API_KEY": FAKE_KEY, "BLUE_DATA_LLM_MODEL": PRIVATE_MODEL},
            f"use {FAKE_KEY} here",
            f"use {MASK} here",
        ),
        ({"OPENAI_API_KEY": FAKE_KEY}, f"use {FAKE_KEY} here", f"use {MASK} here"),
        # A mask a client wrote itself, with nothing private configured, stays as it arrived.
        ({"OPENAI_API_KEY": FAKE_KEY}, f"gpt-{MASK}-x", f"gpt-{MASK}-x"),
        # Public and unrelated names are shown as they came.
        (
            {
                "OPENAI_API_KEY": FAKE_KEY,
                "BLUE_DATA_LLM_MODEL": PRIVATE_MODEL,
                "BLUE_DATA_LLM_SOURCE_CACHE_DIR": "/srv/private-cache",
            },
            "gpt-4o",
            "gpt-4o",
        ),
        (
            {"BLUE_DATA_LLM_MODEL": "gpt-4o", "OPENAI_API_KEY": FAKE_KEY},
            "gpt-4o",
            "gpt-4o",
        ),
        (
            {"OPENAI_API_KEY": FAKE_KEY, "BLUE_DATA_LLM_MODEL": PRIVATE_MODEL},
            "vendor/model@v2",
            "vendor/model@v2",
        ),
    ],
)
def test_query_model_controls_keep_their_existing_behavior(
    monkeypatch: pytest.MonkeyPatch,
    make_source: Callable[..., tuple[LlmSource, Any]],
    how: str,
    environment: dict[str, str],
    answered: str,
    expected: str,
) -> None:
    configure(monkeypatch, environment)
    source, _ = make_source(model=answered)

    result = run_query(how, source)

    assert result["meta"]["model"] == expected
    assert FAKE_KEY not in json.dumps(result["meta"])


def test_a_typed_error_keeps_its_kind_and_message_whatever_is_configured(
    monkeypatch: pytest.MonkeyPatch,
    make_source: Callable[..., tuple[LlmSource, Any]],
) -> None:
    source, _ = make_source()
    with pytest.raises(tools.ToolError) as plain:
        tools.dispatch("llmdb_query", {"query": 3}, source=source)
    environment, _ = OVERLAPS["credential-inside-private-model"]
    configure(monkeypatch, environment)

    with pytest.raises(tools.ToolError) as configured:
        tools.dispatch("llmdb_query", {"query": 3}, source=source)

    assert configured.value.kind == plain.value.kind
    assert configured.value.message == plain.value.message


# ---------------------------------------------------------------- a local recorded provider


RECORDED_ANSWER = {
    "id": "recorded-0001",
    "object": "chat.completion",
    "created": 1700000000,
    "model": "replaced-with-the-requested-name",
    "choices": [
        {
            "index": 0,
            "finish_reason": "stop",
            "message": {
                "role": "assistant",
                "content": json.dumps([{"country": "France"}, {"country": "Germany"}]),
            },
        }
    ],
    "usage": {"prompt_tokens": 21, "completion_tokens": 8, "total_tokens": 29},
}


class RecordedProvider(BaseHTTPRequestHandler):
    """Replays the one recorded answer, naming the model as it was asked for."""

    def do_POST(self) -> None:  # noqa: N802 - the name http.server dispatches on
        length = int(self.headers.get("Content-Length", "0"))
        asked = json.loads(self.rfile.read(length) or b"{}")
        cast("RecordingServer", self.server).requests.append(asked)
        body = json.dumps({**RECORDED_ANSWER, "model": asked.get("model", "")}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        return


class RecordingServer(ThreadingHTTPServer):
    """A local server that keeps what it was asked."""

    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), RecordedProvider)
        self.requests: list[dict[str, Any]] = []


class Recorded:
    def __init__(self, server: RecordingServer) -> None:
        self.server = server

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_address[1]}/v1"

    @property
    def requests(self) -> list[dict[str, Any]]:
        return self.server.requests


@pytest.fixture
def provider() -> Iterator[Recorded]:
    server = RecordingServer()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield Recorded(server)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=10)


def reach(monkeypatch: pytest.MonkeyPatch, provider: Recorded) -> None:
    """Point the real client at the local server, with the deployment's rates and allowance."""
    monkeypatch.setenv("OPENAI_API_BASE", provider.url)
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("OPENAI_API_KEY", FAKE_KEY)
    configure(monkeypatch, estimate_environment())
    monkeypatch.setenv("LITELLM_LOCAL_MODEL_COST_MAP", "True")


def test_a_real_client_query_masks_a_private_model_a_provider_names_back(
    monkeypatch: pytest.MonkeyPatch, provider: Recorded
) -> None:
    reach(monkeypatch, provider)
    environment, _ = OVERLAPS["credential-inside-private-model"]
    configure(monkeypatch, environment)

    result = tools.dispatch("llmdb_query", {"query": QUERY, "attrs": ATTRS})

    # The same deployment, asked through the library alone: what the tool must not change.
    direct = LlmSource(properties=tools.deployment_properties()).fetch(QUERY, attrs=ATTRS)
    unmasked = meta_view(direct.meta, verbose=True)
    assert [request["model"] for request in provider.requests] == [CREDENTIAL_IN_MODEL] * 2
    assert direct.meta.model == CREDENTIAL_IN_MODEL  # the provider named the model it was asked
    assert result["meta"]["model"] == MASK
    assert_nothing_private_shown(result)
    assert result["records"] == list(direct.records)
    assert result["records"] == [{"country": "France"}, {"country": "Germany"}]
    volatile = {"model", "elapsed_seconds"}
    assert {k: v for k, v in result["meta"].items() if k not in volatile} == {
        k: v for k, v in unmasked.items() if k not in volatile
    }
    assert result["meta"]["prompt_tokens"] == 21
    assert result["meta"]["completion_tokens"] == 8
    assert result["meta"]["calls"] == 1


def test_a_real_client_query_shows_a_public_model_as_the_provider_named_it(
    monkeypatch: pytest.MonkeyPatch, provider: Recorded
) -> None:
    reach(monkeypatch, provider)
    monkeypatch.setenv("BLUE_DATA_LLM_MODEL", "gpt-4o")
    monkeypatch.setenv("BLUE_DATA_LLM_SOURCE_CACHE_DIR", "CREDENTIAL")

    result = tools.dispatch("llmdb_query", {"query": QUERY, "attrs": ATTRS})

    assert result["meta"]["model"] == "gpt-4o"
    assert result["meta"]["cost"] > 0
    assert result["meta"]["prompt_tokens"] == 21
    assert result["records"] == [{"country": "France"}, {"country": "Germany"}]


async def test_a_real_client_query_masks_the_same_way_on_the_async_path(
    monkeypatch: pytest.MonkeyPatch, provider: Recorded
) -> None:
    reach(monkeypatch, provider)
    environment, _ = OVERLAPS["credential-inside-private-model"]
    configure(monkeypatch, environment)

    result = await tools.adispatch("llmdb_query", {"query": QUERY, "attrs": ATTRS})

    assert [request["model"] for request in provider.requests] == [CREDENTIAL_IN_MODEL]
    assert result["meta"]["model"] == MASK
    assert result["records"] == [{"country": "France"}, {"country": "Germany"}]
    assert_nothing_private_shown(result)


# ---------------------------------------------------------------- the real server over stdio


def server_parameters(tmp_path: Path, **settings: str) -> Any:
    """A real server process over stdio, with a home and a cache of its own."""
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
        NO_PROXY="127.0.0.1,localhost",
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


async def test_a_real_client_is_shown_a_masked_model_in_an_estimate_over_stdio(
    tmp_path: Path,
) -> None:
    pytest.importorskip("mcp")
    parameters = server_parameters(
        tmp_path,
        BLUE_DATA_LLM_MODEL=CREDENTIAL_IN_MODEL,
        BLUE_DATA_LLM_SOURCE_CACHE_DIR=str(tmp_path / "deployment-cache"),
        OPENAI_API_KEY=FAKE_KEY,
        **estimate_environment(),
    )

    (answer,) = await call_server(
        parameters, [("llmdb_estimate", {"query": QUERY, "attrs": ATTRS})]
    )

    assert answer["model"] == MASK
    assert answer["bounded"] is True
    assert answer["max_output_tokens"] == 400
    assert answer["max_cost"] > 0
    assert_nothing_private_shown(answer)


async def test_a_real_client_is_shown_a_masked_model_in_a_query_over_stdio(
    tmp_path: Path, provider: Recorded
) -> None:
    pytest.importorskip("mcp")
    parameters = server_parameters(
        tmp_path,
        BLUE_DATA_LLM_MODEL=CREDENTIAL_IN_MODEL,
        OPENAI_API_KEY=FAKE_KEY,
        OPENAI_API_BASE=provider.url,
        **estimate_environment(),
    )

    (answer,) = await call_server(parameters, [("llmdb_query", {"query": QUERY, "attrs": ATTRS})])

    assert [request["model"] for request in provider.requests] == [CREDENTIAL_IN_MODEL]
    assert answer["records"] == [{"country": "France"}, {"country": "Germany"}]
    assert answer["meta"]["model"] == MASK
    assert answer["meta"]["calls"] == 1
    assert answer["meta"]["prompt_tokens"] == 21
    assert answer["meta"]["completion_tokens"] == 8
    assert_nothing_private_shown(answer)
