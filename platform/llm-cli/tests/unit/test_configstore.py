"""The profile store and the resolver, below the Click layer.

These are the rules the CLI's configuration story rests on: where a value comes from, what
a secret looks like on disk, and what a write is allowed to touch. They are checked here
rather than through commands, because a command test would pass with any of several wrong
answers to "which tier won".
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from blue_data_llm_cli import configstore
from blue_data_llm_cli.configstore import (
    MASK,
    ConfigResolver,
    ProfileError,
    ProfileStore,
    SettingError,
    Spec,
    base_spec,
    decode,
    display,
    encode,
    load_spec,
)

SPEC_PATH = Path(configstore.__file__).with_name("profile_spec.json")


def cli_spec() -> Spec:
    """The same merge `profile.py` makes: family parameters plus this CLI's own."""
    return base_spec().merged_with(load_spec(SPEC_PATH))


@pytest.fixture
def home(tmp_path: Path) -> Path:
    return tmp_path / ".blue-llmdb"


@pytest.fixture
def store(home: Path) -> ProfileStore:
    store = ProfileStore(home)
    store.initialize()
    return store


def resolver(store: ProfileStore, profile: str | None = None, **environment: str) -> ConfigResolver:
    return ConfigResolver(cli_spec(), store, profile=profile, environment=environment)


# ----------------------------------------------------------------- the spec --
def test_the_walked_list_is_the_two_parameters_a_first_run_is_asked_for() -> None:
    """Short on purpose: a long walk is an interrogation, and nothing here blocks on one."""
    assert [p.name for p in cli_spec().required()] == ["OPENAI_API_KEY", "BLUE_DATA_LLM_MODEL"]


def test_the_family_parameters_keep_the_family_names_and_casts() -> None:
    spec = base_spec()
    assert spec["OPENAI_API_KEY"].is_secret
    assert spec["BLUE_DATA_LLM_MODEL"].default == "gpt-6-luna"
    assert spec["BLUE_DATA_CLI_FORMAT"].default == "table"
    assert spec["BLUE_DATA_CLI_LOG_LIMIT"].cast == "int"


def test_an_unknown_parameter_names_itself() -> None:
    with pytest.raises(SettingError, match="unknown configuration parameter: NOT_A_PARAM"):
        cli_spec()["NOT_A_PARAM"]


def test_a_cast_failure_names_the_parameter() -> None:
    with pytest.raises(SettingError, match="BLUE_DATA_LLM_SOURCE_MAX_RECORDS"):
        cli_spec()["BLUE_DATA_LLM_SOURCE_MAX_RECORDS"].cast_value("many")


def test_merging_keeps_both_halves_and_lets_the_cli_win(tmp_path: Path) -> None:
    extra = tmp_path / "spec.json"
    extra.write_text('{"BLUE_DATA_CLI_FORMAT": {"default": "json", "cast": "str"}}')
    merged = base_spec().merged_with(load_spec(extra))
    assert merged["BLUE_DATA_CLI_FORMAT"].default == "json"
    assert "OPENAI_API_KEY" in merged


def test_a_spec_declaring_an_unknown_cast_does_not_load(tmp_path: Path) -> None:
    bad = tmp_path / "spec.json"
    bad.write_text('{"X": {"cast": "colour"}}')
    with pytest.raises(SettingError, match="unknown cast"):
        load_spec(bad)


# -------------------------------------------------------------- the profile --
def test_a_first_run_creates_and_selects_default(home: Path) -> None:
    store = ProfileStore(home)
    store.initialize()
    assert store.list_profiles() == ["default"]
    assert store.selected() == "default"


def test_a_removed_profile_is_not_resurrected(store: ProfileStore) -> None:
    """Keyed on the profiles file, so the next command does not undo the removal."""
    store.add("staging")
    store.remove("default")
    store.initialize()
    assert store.list_profiles() == ["staging"]


def test_adding_a_profile_twice_says_so(store: ProfileStore) -> None:
    store.add("staging")
    with pytest.raises(ProfileError, match="already exists"):
        store.add("staging")


def test_removing_an_absent_profile_says_so(store: ProfileStore) -> None:
    with pytest.raises(ProfileError, match="no such profile"):
        store.remove("ghost")


def test_selecting_an_absent_profile_says_so(store: ProfileStore) -> None:
    with pytest.raises(ProfileError, match="no such profile"):
        store.select("ghost")


def test_remove_touches_only_that_section(store: ProfileStore) -> None:
    store.add("staging")
    store.add("keep")
    store.set_value("keep", "BLUE_DATA_LLM_MODEL", "gpt-4o-mini")
    store.set_value("staging", "BLUE_DATA_LLM_MODEL", "gpt-4")
    cache = store.home / "cache"
    cache.mkdir()
    (cache / "entry.json").write_text("{}")

    store.remove("staging")

    assert store.list_profiles() == ["default", "keep"]
    assert store.values("keep") == {"BLUE_DATA_LLM_MODEL": "gpt-4o-mini"}
    assert (cache / "entry.json").exists()
    assert store.selected() == "default"


def test_removing_the_selected_profile_falls_back_to_default(store: ProfileStore) -> None:
    store.add("staging")
    store.select("staging")
    store.remove("staging")
    assert store.selected() == "default"


def test_the_profiles_file_is_ini_with_one_section_per_profile(store: ProfileStore) -> None:
    store.add("staging")
    store.set_value("staging", "BLUE_DATA_LLM_MODEL", "gpt-4o-mini")
    text = (store.home / ".profiles").read_text()
    assert "[staging]" in text
    assert text.index("[staging]") < text.index("gpt-4o-mini")


def test_parameter_names_keep_their_case_on_disk(store: ProfileStore) -> None:
    """The parameter name is the environment variable name; lower-casing it breaks both."""
    store.set_value("default", "BLUE_DATA_LLM_MODEL", "gpt-4")
    assert "BLUE_DATA_LLM_MODEL" in (store.home / ".profiles").read_text()
    assert store.values("default") == {"BLUE_DATA_LLM_MODEL": "gpt-4"}


def test_a_value_holding_a_percent_survives_the_round_trip(store: ProfileStore) -> None:
    store.set_value("default", "BLUE_DATA_LLM_MODEL", "gpt-100%")
    assert store.values("default")["BLUE_DATA_LLM_MODEL"] == "gpt-100%"


# -------------------------------------------------------------- the storage --
def test_the_home_directory_is_private(home: Path) -> None:
    ProfileStore(home).initialize()
    assert stat.S_IMODE(home.stat().st_mode) == 0o700


def test_every_file_written_is_private(store: ProfileStore) -> None:
    store.set_value("default", "BLUE_DATA_LLM_MODEL", "gpt-4")
    assert stat.S_IMODE((store.home / ".profiles").stat().st_mode) == 0o600
    assert stat.S_IMODE((store.home / ".selected.profile").stat().st_mode) == 0o600


def test_a_write_leaves_no_temporary_file_behind(store: ProfileStore) -> None:
    store.set_value("default", "BLUE_DATA_LLM_MODEL", "gpt-4")
    assert sorted(p.name for p in store.home.iterdir()) == [".profiles", ".selected.profile"]


def test_a_failed_write_leaves_the_previous_contents_intact(
    store: ProfileStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole reason the write is atomic: a half-written profiles file is unreadable."""
    store.set_value("default", "BLUE_DATA_LLM_MODEL", "gpt-4")
    before = (store.home / ".profiles").read_text()

    def explode(*args: object, **kwargs: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(configstore.os, "replace", explode)
    with pytest.raises(ProfileError, match="disk full"):
        store.set_value("default", "BLUE_DATA_LLM_MODEL", "gpt-4o-mini")

    assert (store.home / ".profiles").read_text() == before
    assert sorted(p.name for p in store.home.iterdir()) == [".profiles", ".selected.profile"]


def test_a_non_regular_profiles_path_is_refused(home: Path) -> None:
    home.mkdir(parents=True)
    (home / ".profiles").mkdir()
    with pytest.raises(ProfileError, match="not a regular file"):
        ProfileStore(home).list_profiles()


def test_a_non_regular_selection_path_is_refused(home: Path) -> None:
    home.mkdir(parents=True)
    (home / ".selected.profile").mkdir()
    with pytest.raises(ProfileError, match="not a regular file"):
        ProfileStore(home).selected()


def test_initialize_refuses_an_exposed_selection_before_creating_profiles(home: Path) -> None:
    home.mkdir(mode=0o700)
    selection = home / ".selected.profile"
    selection.write_text("default")
    os.chmod(selection, 0o644)

    with pytest.raises(ProfileError, match="mode 0600 with chmod"):
        ProfileStore(home).initialize()

    assert not (home / ".profiles").exists()
    assert selection.read_text() == "default"
    assert stat.S_IMODE(selection.stat().st_mode) == 0o644


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFO permissions are POSIX-only")
def test_initialize_refuses_a_selection_fifo_before_creating_profiles(home: Path) -> None:
    home.mkdir(mode=0o700)
    selection = home / ".selected.profile"
    os.mkfifo(selection, mode=0o600)

    with pytest.raises(ProfileError, match="not a regular file"):
        ProfileStore(home).initialize()

    assert not (home / ".profiles").exists()
    assert stat.S_ISFIFO(selection.lstat().st_mode)


def test_initialize_refuses_a_broken_selection_link_before_creating_profiles(home: Path) -> None:
    home.mkdir(mode=0o700)
    selection = home / ".selected.profile"
    selection.symlink_to(home / "missing")

    with pytest.raises(ProfileError, match="not a regular file"):
        ProfileStore(home).initialize()

    assert not (home / ".profiles").exists()
    assert selection.is_symlink()
    assert not selection.exists()


def test_a_home_that_cannot_be_written_is_an_error_too(tmp_path: Path) -> None:
    read_only = tmp_path / "read-only"
    read_only.mkdir(mode=0o500)
    with pytest.raises(ProfileError):
        ProfileStore(read_only / "home").initialize()


@pytest.mark.parametrize("value", ["a\rb", "a\nb", "a\r[injected]\rOPENAI_API_KEY = x"])
def test_a_value_holding_a_line_break_is_refused(store: ProfileStore, value: str) -> None:
    """configparser escapes a newline on the way out and not a carriage return.

    A value pasted from a CRLF file came back as a separate setting, and one shaped like INI
    came back as a separate profile — from a write that reported success.
    """
    with pytest.raises(SettingError, match="line break"):
        store.set_value("default", "BLUE_DATA_LLM_MODEL", value)
    assert "injected" not in (store.home / ".profiles").read_text()


def test_a_blank_value_is_refused_rather_than_stored_and_ignored(store: ProfileStore) -> None:
    store.set_value("default", "BLUE_DATA_LLM_MODEL", "gpt-4o-mini")
    with pytest.raises(SettingError, match="blank"):
        store.set_value("default", "BLUE_DATA_LLM_MODEL", "  ")
    assert store.values("default")["BLUE_DATA_LLM_MODEL"] == "gpt-4o-mini"


def test_a_wider_existing_home_is_reported_once_and_left_alone(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    home.mkdir(parents=True)
    os.chmod(home, 0o755)
    configstore._reported_modes.clear()  # pyright: ignore[reportPrivateUsage]

    store = ProfileStore(home)
    store.initialize()
    store.add("staging")

    assert stat.S_IMODE(home.stat().st_mode) == 0o755
    assert capsys.readouterr().err.count(str(home)) == 1


def test_a_home_owned_by_another_uid_is_refused(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home.mkdir(mode=0o700)
    real_lstat = Path.lstat

    def other_owner(candidate: Path) -> os.stat_result:
        status = real_lstat(candidate)
        if candidate != home:
            return status
        fields = list(status)
        fields[4] = status.st_uid + 1
        return os.stat_result(fields)

    monkeypatch.setattr(Path, "lstat", other_owner)
    with pytest.raises(ProfileError, match="not the current uid"):
        ProfileStore(home).initialize()


def test_a_home_symlink_is_refused(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir(mode=0o700)
    home = tmp_path / "home"
    home.symlink_to(target, target_is_directory=True)

    with pytest.raises(ProfileError, match="not a directory"):
        ProfileStore(home).initialize()


@pytest.mark.parametrize("mode", [0o720, 0o702, 0o777])
def test_initialize_refuses_a_writable_home_without_creating_files(home: Path, mode: int) -> None:
    home.mkdir(parents=True)
    os.chmod(home, mode)

    with pytest.raises(ProfileError, match="mode 0700 with chmod"):
        ProfileStore(home).initialize()

    assert list(home.iterdir()) == []


@pytest.mark.parametrize("mode", [0o720, 0o702, 0o777])
def test_a_home_writable_by_another_account_is_refused_without_mutation(
    home: Path, mode: int
) -> None:
    home.mkdir(parents=True)
    profiles = home / ".profiles"
    profiles.write_text("[default]\nBLUE_DATA_LLM_MODEL = before\n")
    os.chmod(profiles, 0o600)
    os.chmod(home, mode)
    before = profiles.read_bytes()

    store = ProfileStore(home)
    with pytest.raises(ProfileError, match="mode 0700 with chmod"):
        store.list_profiles()
    with pytest.raises(ProfileError, match="mode 0700 with chmod"):
        store.set_value("default", "BLUE_DATA_LLM_MODEL", "after")

    assert profiles.read_bytes() == before
    assert sorted(path.name for path in home.iterdir()) == [".profiles"]


@pytest.mark.parametrize("mode", [0o640, 0o604, 0o620, 0o602])
def test_an_exposed_profiles_file_is_refused_without_mutation(
    store: ProfileStore, mode: int
) -> None:
    path = store.profiles_path
    before = path.read_bytes()
    os.chmod(path, mode)

    with pytest.raises(ProfileError, match="mode 0600 with chmod"):
        store.values("default")
    with pytest.raises(ProfileError, match="mode 0600 with chmod"):
        store.set_value("default", "BLUE_DATA_LLM_MODEL", "after")

    assert path.read_bytes() == before
    assert stat.S_IMODE(path.stat().st_mode) == mode


@pytest.mark.parametrize("mode", [0o640, 0o604, 0o620, 0o602])
def test_an_exposed_selection_file_is_refused_without_mutation(
    store: ProfileStore, mode: int
) -> None:
    path = store.selection_path
    before = path.read_bytes()
    os.chmod(path, mode)

    with pytest.raises(ProfileError, match="mode 0600 with chmod"):
        store.selected()
    with pytest.raises(ProfileError, match="mode 0600 with chmod"):
        store.select("default")

    assert path.read_bytes() == before
    assert stat.S_IMODE(path.stat().st_mode) == mode


def test_remove_checks_the_selection_before_mutating_profiles(store: ProfileStore) -> None:
    store.add("staging")
    store.select("staging")
    profiles_before = store.profiles_path.read_bytes()
    selection_before = store.selection_path.read_bytes()
    os.chmod(store.selection_path, 0o644)

    with pytest.raises(ProfileError, match="mode 0600 with chmod"):
        store.remove("staging")

    assert store.profiles_path.read_bytes() == profiles_before
    assert store.selection_path.read_bytes() == selection_before


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFO permissions are POSIX-only")
def test_a_profiles_fifo_is_refused_on_initialize_read_and_write(home: Path) -> None:
    home.mkdir(mode=0o700)
    path = home / ".profiles"
    os.mkfifo(path, mode=0o600)
    store = ProfileStore(home)

    with pytest.raises(ProfileError, match="not a regular file"):
        store.initialize()
    with pytest.raises(ProfileError, match="not a regular file"):
        store.list_profiles()
    with pytest.raises(ProfileError, match="not a regular file"):
        store.add("staging")

    assert stat.S_ISFIFO(path.lstat().st_mode)


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFO permissions are POSIX-only")
def test_a_selection_fifo_is_refused_on_read_and_write(store: ProfileStore) -> None:
    store.selection_path.unlink()
    os.mkfifo(store.selection_path, mode=0o600)

    with pytest.raises(ProfileError, match="not a regular file"):
        store.selected()
    with pytest.raises(ProfileError, match="not a regular file"):
        store.select("default")

    assert stat.S_ISFIFO(store.selection_path.lstat().st_mode)


def test_a_profile_file_owned_by_another_uid_is_refused(
    store: ProfileStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_lstat = Path.lstat
    path = store.profiles_path

    def other_owner(candidate: Path) -> os.stat_result:
        status = real_lstat(candidate)
        if candidate != path:
            return status
        fields = list(status)
        fields[4] = status.st_uid + 1
        return os.stat_result(fields)

    monkeypatch.setattr(Path, "lstat", other_owner)
    with pytest.raises(ProfileError, match="not the current uid"):
        store.values("default")


def test_a_selection_file_owned_by_another_uid_is_refused(
    store: ProfileStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_lstat = Path.lstat
    path = store.selection_path

    def other_owner(candidate: Path) -> os.stat_result:
        status = real_lstat(candidate)
        if candidate != path:
            return status
        fields = list(status)
        fields[4] = status.st_uid + 1
        return os.stat_result(fields)

    monkeypatch.setattr(Path, "lstat", other_owner)
    with pytest.raises(ProfileError, match="not the current uid"):
        store.selected()


def test_an_opened_profile_file_is_revalidated_before_read(
    store: ProfileStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_fstat = os.fstat

    def changed_owner(descriptor: int) -> os.stat_result:
        status = real_fstat(descriptor)
        fields = list(status)
        fields[4] = status.st_uid + 1
        return os.stat_result(fields)

    monkeypatch.setattr(os, "fstat", changed_owner)
    with pytest.raises(ProfileError, match="not the current uid"):
        store.values("default")


def test_permission_remediation_is_not_a_copyable_shell_command(tmp_path: Path) -> None:
    home = tmp_path / "unsafe; echo injected"
    home.mkdir(mode=0o700)
    os.chmod(home, 0o777)

    with pytest.raises(ProfileError) as refusal:
        ProfileStore(home).initialize()

    message = str(refusal.value)
    assert "mode 0700 with chmod" in message
    assert "chmod 700 " not in message


def test_a_symlink_out_of_the_home_is_refused_rather_than_followed(
    home: Path, tmp_path: Path
) -> None:
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "stolen").write_text("[default]\n")
    home.mkdir(parents=True)
    (home / ".profiles").symlink_to(elsewhere / "stolen")

    with pytest.raises(ProfileError, match="not a regular file"):
        ProfileStore(home).list_profiles()


def test_a_symlink_inside_the_home_is_refused(home: Path) -> None:
    home.mkdir(parents=True)
    (home / "real").write_text("[default]\n")
    os.chmod(home / "real", 0o600)
    (home / ".profiles").symlink_to(home / "real")
    with pytest.raises(ProfileError, match="not a regular file"):
        ProfileStore(home).list_profiles()


def test_a_broken_profiles_symlink_is_refused_without_replacement(home: Path) -> None:
    home.mkdir(mode=0o700)
    path = home / ".profiles"
    path.symlink_to(home / "missing")
    store = ProfileStore(home)

    with pytest.raises(ProfileError, match="not a regular file"):
        store.initialize()
    with pytest.raises(ProfileError, match="not a regular file"):
        store.list_profiles()
    with pytest.raises(ProfileError, match="not a regular file"):
        store.add("staging")

    assert path.is_symlink()
    assert not path.exists()


def test_a_broken_selection_symlink_is_refused_without_replacement(store: ProfileStore) -> None:
    path = store.selection_path
    path.unlink()
    path.symlink_to(store.home / "missing")

    with pytest.raises(ProfileError, match="not a regular file"):
        store.selected()
    with pytest.raises(ProfileError, match="not a regular file"):
        store.select("default")

    assert path.is_symlink()
    assert not path.exists()


# --------------------------------------------------------------- the secret --
def test_a_secret_is_not_stored_in_plain_text(store: ProfileStore) -> None:
    store.set_value("default", "OPENAI_API_KEY", encode("typed-at-the-prompt"))
    stored = (store.home / ".profiles").read_text()
    assert "typed-at-the-prompt" not in stored
    assert "enc:" in stored
    assert decode(store.values("default")["OPENAI_API_KEY"]) == "typed-at-the-prompt"


def test_a_reference_is_stored_as_written_and_resolved_at_use(
    store: ProfileStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    store.set_value("default", "OPENAI_API_KEY", encode("$MY_KEY"))
    assert store.values("default")["OPENAI_API_KEY"] == "$MY_KEY"
    monkeypatch.delenv("MY_KEY", raising=False)
    assert decode("$MY_KEY") is None
    monkeypatch.setenv("MY_KEY", "exported-not-stored")
    assert decode("$MY_KEY") == "exported-not-stored"


def test_display_masks_a_stored_secret_and_names_a_reference(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert display(encode("typed-at-the-prompt")) == MASK
    monkeypatch.delenv("MY_KEY", raising=False)
    assert display("$MY_KEY") == "$MY_KEY (unset)"
    monkeypatch.setenv("MY_KEY", "anything")
    assert display("$MY_KEY") == "$MY_KEY (set)"


# ------------------------------------------------------------- the resolver --
def test_the_environment_outranks_the_profile(store: ProfileStore) -> None:
    store.set_value("default", "BLUE_DATA_LLM_MODEL", "from-profile")
    config = resolver(store, BLUE_DATA_LLM_MODEL="from-environment")
    assert config.get("BLUE_DATA_LLM_MODEL") == "from-environment"
    assert config.source_of("BLUE_DATA_LLM_MODEL") == "environment"


@pytest.mark.parametrize("exported", ["", "   "])
def test_an_empty_export_is_refused_rather_than_read_as_unset(
    store: ProfileStore, exported: str
) -> None:
    """It used to win its tier and then hand the answer down to the profile in silence.

    Which is the same defect the store already refuses at write time: the tier that
    answered has to be the tier that had something to say.
    """
    store.set_value("default", "BLUE_DATA_LLM_MODEL", "profile-model")
    with pytest.raises(SettingError, match="BLUE_DATA_LLM_MODEL"):
        resolver(store, BLUE_DATA_LLM_MODEL=exported)


def test_an_empty_export_is_refused_by_name_even_where_it_was_the_only_tier(
    store: ProfileStore,
) -> None:
    """No profile value to fall through to, and still refused: the export is the mistake."""
    with pytest.raises(SettingError, match="BLUE_DATA_LLM_SOURCE_LIMIT_COST"):
        resolver(store, BLUE_DATA_LLM_SOURCE_LIMIT_COST="")


def test_the_profile_outranks_the_default(store: ProfileStore) -> None:
    store.set_value("default", "BLUE_DATA_LLM_MODEL", "from-profile")
    config = resolver(store)
    assert config.get("BLUE_DATA_LLM_MODEL") == "from-profile"
    assert config.source_of("BLUE_DATA_LLM_MODEL") == "profile:default"


def test_the_default_is_the_last_tier(store: ProfileStore) -> None:
    config = resolver(store)
    assert config.get("BLUE_DATA_LLM_MODEL") == "gpt-6-luna"
    assert config.source_of("BLUE_DATA_LLM_MODEL") == "default"


def test_a_parameter_with_no_default_reports_unset(store: ProfileStore) -> None:
    config = resolver(store)
    assert config.get("BLUE_DATA_LLM_SOURCE_LIMIT_COST") is None
    assert config.source_of("BLUE_DATA_LLM_SOURCE_LIMIT_COST") == "unset"


def test_a_resolved_value_arrives_cast(store: ProfileStore) -> None:
    store.set_value("default", "BLUE_DATA_LLM_SOURCE_LIMIT_COST", "0.5")
    store.set_value("default", "BLUE_DATA_LLM_SOURCE_MAX_RECORDS", "20")
    store.set_value("default", "BLUE_DATA_LLM_SOURCE_CACHE", "true")
    config = resolver(store)
    assert config.get("BLUE_DATA_LLM_SOURCE_LIMIT_COST") == 0.5
    assert config.get("BLUE_DATA_LLM_SOURCE_MAX_RECORDS") == 20
    assert config.get("BLUE_DATA_LLM_SOURCE_CACHE") is True


def test_export_writes_the_resolved_values_into_the_environment(
    store: ProfileStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    store.set_value("default", "BLUE_DATA_LLM_MODEL", "gpt-4o-mini")
    monkeypatch.delenv("BLUE_DATA_LLM_MODEL", raising=False)
    ConfigResolver(cli_spec(), store, environment={}).export()
    assert os.environ["BLUE_DATA_LLM_MODEL"] == "gpt-4o-mini"


def test_export_does_not_overwrite_what_the_caller_set(
    store: ProfileStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    store.set_value("default", "BLUE_DATA_LLM_MODEL", "gpt-4o-mini")
    monkeypatch.setenv("BLUE_DATA_LLM_MODEL", "chosen-by-hand")
    ConfigResolver(cli_spec(), store).export()
    assert os.environ["BLUE_DATA_LLM_MODEL"] == "chosen-by-hand"


def test_an_export_is_undone_when_it_is_restored(
    store: ProfileStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One run is one process, but a test harness is not, and the tiers have to survive it."""
    store.set_value("default", "BLUE_DATA_LLM_MODEL", "gpt-4o-mini")
    monkeypatch.delenv("BLUE_DATA_LLM_MODEL", raising=False)
    config = ConfigResolver(cli_spec(), store)
    config.export()
    assert os.environ["BLUE_DATA_LLM_MODEL"] == "gpt-4o-mini"
    config.restore()
    assert "BLUE_DATA_LLM_MODEL" not in os.environ


def test_restoring_puts_back_what_the_caller_had(
    store: ProfileStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BLUE_DATA_CLI_LOG_LIMIT", "120")
    config = ConfigResolver(cli_spec(), store)
    config.export()
    config.restore()
    assert os.environ["BLUE_DATA_CLI_LOG_LIMIT"] == "120"


def test_a_resolver_reports_the_tier_that_won_rather_than_its_own_exports(
    store: ProfileStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The snapshot is the whole point: after `export`, the live environment holds the
    profile's own values, and resolving against it would call them environment values."""
    store.set_value("default", "BLUE_DATA_LLM_MODEL", "gpt-4o-mini")
    monkeypatch.delenv("BLUE_DATA_LLM_MODEL", raising=False)
    config = ConfigResolver(cli_spec(), store)
    config.export()
    assert config.source_of("BLUE_DATA_LLM_MODEL") == "profile:default"


def test_a_secret_resolves_through_its_reference(
    store: ProfileStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    store.set_value("default", "OPENAI_API_KEY", encode("$MY_KEY"))
    monkeypatch.setenv("MY_KEY", "exported-not-stored")
    assert resolver(store).get("OPENAI_API_KEY") == "exported-not-stored"


def test_an_inherited_parameter_is_tried_before_the_default(store: ProfileStore) -> None:
    spec = Spec(
        [
            configstore.Parameter("BLUE_DATA_LLM_MODEL", default="gpt-4o"),
            configstore.Parameter(
                "BLUE_DATA_LLM_SOURCE_QUERY_MODEL",
                inherits=("BLUE_DATA_LLM_MODEL",),
            ),
        ]
    )
    store.set_value("default", "BLUE_DATA_LLM_MODEL", "gpt-4o-mini")
    config = ConfigResolver(spec, store, environment={})
    assert config.get("BLUE_DATA_LLM_SOURCE_QUERY_MODEL") == "gpt-4o-mini"
