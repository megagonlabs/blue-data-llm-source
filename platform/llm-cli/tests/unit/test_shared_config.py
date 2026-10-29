"""Shared-engine delegation and CLI invocation boundaries."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest
from blue_data.utils import config as shared
from blue_data.utils.config import resolve
from click.testing import CliRunner

from blue_data_llm_cli.commands import cli
from blue_data_llm_cli.profile import cli_spec
from blue_data_llm_cli.profile_adapter import ConfigResolver, ProfileStore


def test_shared_engine_performs_spec_resolution_secrets_and_profile_operations(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def record(owner: Any, name: str) -> None:
        original = getattr(owner, name)

        def wrapped(*args: Any, **kwargs: Any) -> Any:
            calls.append(name)
            return original(*args, **kwargs)

        monkeypatch.setattr(owner, name, wrapped)

    for name in ("add", "set_value", "values", "list_profiles", "remove"):
        record(shared.ProfileStore, name)
    for name in ("get", "source_of", "export"):
        record(shared.ConfigResolver, name)
    record(shared.ParamSpec, "cast_value")
    record(resolve, "decode")
    spec = cli_spec()
    assert type(spec) is shared.ConfigSpec
    assert all(type(p) is shared.ParamSpec for p in spec)
    assert "BLUE_DATA_LLM_PROVIDER" not in spec
    assert "ANTHROPIC_API_KEY" not in spec
    store = ProfileStore(tmp_path / "profiles")
    store.initialize()
    store.set_value("default", "BLUE_DATA_LLM_MODEL", "chosen")
    store.set_value("default", "OPENAI_API_KEY", shared.encode("$SYNTHETIC_REFERENCE"))
    store.add("other", values={"BLUE_DATA_LLM_SOURCE_LIMIT_COST": "0"})
    resolver = ConfigResolver(spec, store, environment={"SYNTHETIC_REFERENCE": "fixture-value"})
    try:
        assert resolver.get("BLUE_DATA_LLM_MODEL") == "chosen"
        assert resolver.get("OPENAI_API_KEY") == "fixture-value"
        assert resolver.source_of("OPENAI_API_KEY") == "profile:default"
        resolver.export()
    finally:
        resolver.restore()
    store.remove("other")
    assert set(calls) >= {
        "add",
        "set_value",
        "values",
        "list_profiles",
        "remove",
        "get",
        "source_of",
        "export",
        "cast_value",
        "decode",
    }


@pytest.mark.parametrize(
    ("args", "answer"),
    [
        (["UNKNOWN", "value"], None),
        (["OPENAI_API_KEY", "rejected-argument"], None),
        (["BLUE_DATA_LLM_MODEL", "  "], None),
        (["BLUE_DATA_LLM_SOURCE_MAX_RECORDS", "invalid"], None),
        (["BLUE_DATA_LLM_MODEL"], "\n"),
        (["BLUE_DATA_LLM_MODEL"], ""),
    ],
)
def test_refused_named_config_never_creates_a_profile(
    runner: CliRunner,
    cli_home: Path,
    args: list[str],
    answer: str | None,
) -> None:
    assert runner.invoke(cli, ["profile", "list"]).exit_code == 0
    store = ProfileStore(cli_home)
    before = (store.profiles_path.read_bytes(), store.selected_path.read_bytes())
    result = runner.invoke(cli, ["profile", "config", *args, "--profile", "new"], input=answer)
    assert result.exit_code in {0, 2, 4}
    assert not store.exists("new")
    assert (store.profiles_path.read_bytes(), store.selected_path.read_bytes()) == before


def test_named_creation_delegates_one_populated_add(
    runner: CliRunner,
    cli_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert runner.invoke(cli, ["profile", "list"]).exit_code == 0
    original = shared.ProfileStore.add
    calls: list[tuple[str, dict[str, str] | None]] = []

    def added(self: Any, name: str, values: dict[str, str] | None = None) -> None:
        calls.append((name, values))
        original(self, name, values=values)

    monkeypatch.setattr(shared.ProfileStore, "add", added)
    result = runner.invoke(
        cli, ["profile", "config", "BLUE_DATA_LLM_MODEL", "chosen", "--profile", "new"]
    )
    assert result.exit_code == 0, result.output
    assert calls == [("new", {"BLUE_DATA_LLM_MODEL": "chosen"})]
    assert ProfileStore(cli_home).selected() == "default"


def test_unresolved_reference_and_named_inspection_use_preexport_snapshot(
    runner: CliRunner,
    cli_home: Path,
) -> None:
    assert runner.invoke(cli, ["profile", "add", "other"]).exit_code == 0
    store = ProfileStore(cli_home)
    store.set_value("default", "OPENAI_API_KEY", "$UNSET_SYNTHETIC_REF")
    store.set_value("other", "OPENAI_API_KEY", "$BLUE_DATA_LLM_MODEL")
    # The default model exported during startup must not resolve this reference.
    for name, reference in [
        ("default", "$UNSET_SYNTHETIC_REF"),
        ("other", "$BLUE_DATA_LLM_MODEL"),
    ] * 2:
        result = runner.invoke(cli, ["profile", "inspect", name, "--format", "json"])
        assert result.exit_code == 0, result.output
        value = json.loads(result.stdout)["values"]["OPENAI_API_KEY"]
        assert value == {"value": f"{reference} (unset)", "source": f"profile:{name}"}
    assert "BLUE_DATA_LLM_MODEL" not in os.environ


def test_partial_shared_export_failure_restores_all_environment(
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = shared.ConfigResolver.export

    def fail(self: Any) -> Any:
        original(self)
        raise shared.ConfigError("synthetic failure after export")

    monkeypatch.setattr(shared.ConfigResolver, "export", fail)
    before = dict(os.environ)
    result = runner.invoke(cli, ["metadata", "inspect", "--format", "json"])
    assert result.exit_code == 4
    assert json.loads(result.stdout)["error"]["code"] == "PROFILE"
    assert dict(os.environ) == before


def test_invalid_profile_encoding_does_not_echo_file_contents(tmp_path: Path) -> None:
    store = ProfileStore(tmp_path / "profiles")
    store.initialize()
    store.profiles_path.write_bytes(b"[default]\nPRIVATE = synthetic-private-marker\xff\n")
    with pytest.raises(shared.ProfileError) as caught:
        store.list_profiles()
    assert "UnicodeDecodeError" in str(caught.value)
    assert "synthetic-private-marker" not in str(caught.value)


def test_repeated_restore_does_not_rewrite_later_environment_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolver = ConfigResolver(cli_spec(), ProfileStore(tmp_path / "absent"), environment={})
    resolver.export()
    resolver.restore()
    monkeypatch.setenv("BLUE_DATA_LLM_MODEL", "later-invocation")
    resolver.restore()
    assert os.environ["BLUE_DATA_LLM_MODEL"] == "later-invocation"
