"""The profile and configuration engine, as a local stand-in for ``blue_data.utils.config``.

Every Blue CLI in the family sits on one shared engine — ``ProfileStore``,
``ConfigResolver``, ``base_spec``, ``load_spec``, ``MASK``, ``encode`` and ``display`` —
imported from ``blue_data.utils.config``. That module is not in the pinned ``blue-data``
release this repository depends on, and reimplementing the family's configuration story per
CLI is how three CLIs end up with three precedence orders. So the names here are the
engine's names, with the behaviour the family's ``profile.py`` depends on, and
``profile.py`` imports them from here. When the shared engine ships, this module is deleted
and the import at the top of ``profile.py`` changes to point at it — that swap is the whole
point of the naming, and anything added here has to keep it possible.

What the engine owns: the parameter spec and its casts, the ``.profiles`` file and the
selection beside it, the precedence chain below the command line, and the on-disk form of a
secret. What it does not own: any Click, any output, any of this CLI's own vocabulary.

**Errors.** Two, and neither is named ``ConfigError``. The library this CLI wraps exports a
``ConfigError`` of its own meaning "the source rejected a setting", and two exception
classes with one name, reachable in one module, is how a handler ends up catching the wrong
failure. Here, :class:`ProfileError` is a profile operation that could not be carried out —
no such profile, one that already exists, a store that cannot be read — and
:class:`SettingError` is a parameter one: an unknown name, or a value that will not cast.
The CLI reports both as ``ERROR: PROFILE``; a ``blue_data_llm_source.ConfigError`` is
``ERROR: CONFIG``.

**Blank values.** A setting that is present but empty is refused at both ends rather than
read as absent. On disk it stores, reports success and is then skipped by every read; in the
environment it wins its tier and then hands the answer down to the profile. Both are the
same silence, and the whole point of a precedence chain is that the tier that answered is
the tier that had something to say.

**Storage.** ``~/.blue-llmdb`` is created at mode 0700 and every file in it at 0600, because
a profile can hold a provider credential. Every write goes to a temporary file in the same
directory, is flushed and fsynced, and is moved into place with ``os.replace``: a partial
write must never leave a profiles file a later run cannot parse. A complete write must not
either, which is why a value holding a line break is refused rather than stored — the
profiles file is line-oriented, and configparser escapes a newline on the way out but not a
carriage return, so one pasted from a CRLF file came back as a second setting. Profile paths
must be regular files; symlinks and device-like entries are refused.

An existing 0755 home is reported once and left alone. A group- or world-writable home is
refused because another account could replace a settings file. A settings file readable or
writable by group or other is refused because it may hold a provider credential or execution
ceiling. The error names the required ``chmod`` repair; the CLI does not change existing modes.
"""

from __future__ import annotations

import base64
import binascii
import configparser
import contextlib
import io
import json
import os
import stat
import sys
import tempfile
from collections.abc import Generator, Iterable, Iterator, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, cast

__all__ = [
    "DEFAULT_PROFILE",
    "MASK",
    "ConfigResolver",
    "Parameter",
    "ProfileError",
    "ProfileStore",
    "SettingError",
    "Spec",
    "StoreError",
    "base_spec",
    "decode",
    "display",
    "encode",
    "load_spec",
]

DEFAULT_PROFILE = "default"
"""Created and selected on a first run, and the fallback when a selection disappears."""

MASK = "***"
"""What a secret's value looks like anywhere it is printed."""

_ENCODED_PREFIX = "enc:"
_DIR_MODE = 0o700
_FILE_MODE = 0o600


# ------------------------------------------------------------------ errors --
class StoreError(Exception):
    """Base for the two failures this module reports."""


class ProfileError(StoreError):
    """A profile operation could not be carried out."""


class SettingError(StoreError):
    """A configuration parameter is unknown, or its value will not cast.

    The family engine calls this one ``ConfigError``. Renamed here because the library this
    CLI wraps exports a ``ConfigError`` that means something else entirely, and the two are
    reachable from the same handler.
    """


# -------------------------------------------------------------- parameters --
def _cast_bool(name: str, value: object) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    raise SettingError(f"{name} must be true or false, got {value!r}")


def _cast_int(name: str, value: object) -> int:
    if isinstance(value, bool):
        raise SettingError(f"{name} must be a whole number, got {value!r}")
    if isinstance(value, int):
        return value
    try:
        return int(str(value).strip())
    except ValueError:
        raise SettingError(f"{name} must be a whole number, got {value!r}") from None


def _cast_float(name: str, value: object) -> float:
    if isinstance(value, bool):
        raise SettingError(f"{name} must be a number, got {value!r}")
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).strip())
    except ValueError:
        raise SettingError(f"{name} must be a number, got {value!r}") from None


def _cast_path(name: str, value: object) -> str:
    text = str(value).strip()
    if not text:
        raise SettingError(f"{name} must be a path")
    return str(Path(text).expanduser())


CASTS = ("str", "secret", "int", "float", "bool", "directory", "file")
"""Every cast a spec may declare. An unlisted one is a spec that will not load."""


@dataclass(frozen=True)
class Parameter:
    """One configuration parameter. Its name is also its environment variable's name."""

    name: str
    default: Any = None
    prompt: str = ""
    required: bool = False
    cast: str = "str"
    inherits: tuple[str, ...] = ()

    @property
    def is_secret(self) -> bool:
        return self.cast == "secret"

    def cast_value(self, value: object) -> Any:
        """*value* as this parameter's declared type, or a :class:`SettingError` naming it.

        Accepts a value that is already the right type, because a spec's own default
        arrives from JSON as an ``int`` or a ``bool`` rather than as text.
        """
        if value is None:
            return None
        if self.cast == "int":
            return _cast_int(self.name, value)
        if self.cast == "float":
            return _cast_float(self.name, value)
        if self.cast == "bool":
            return _cast_bool(self.name, value)
        if self.cast in {"directory", "file"}:
            return _cast_path(self.name, value)
        return str(value)


class Spec:
    """The parameters one CLI knows about, in declaration order."""

    def __init__(self, parameters: Iterable[Parameter] = ()) -> None:
        self._parameters: dict[str, Parameter] = {p.name: p for p in parameters}

    def __iter__(self) -> Iterator[Parameter]:
        return iter(self._parameters.values())

    def __len__(self) -> int:
        return len(self._parameters)

    def __contains__(self, name: object) -> bool:
        return name in self._parameters

    def __getitem__(self, name: str) -> Parameter:
        try:
            return self._parameters[name]
        except KeyError:
            raise SettingError(f"unknown configuration parameter: {name}") from None

    def names(self) -> tuple[str, ...]:
        return tuple(self._parameters)

    def required(self) -> list[Parameter]:
        """The parameters ``profile config`` walks with no argument.

        Keeping this list short is the difference between a usable first run and an
        interrogation. Nothing in this CLI blocks on one being unset.
        """
        return [p for p in self._parameters.values() if p.required]

    def with_default(self, name: str, default: Any) -> Spec:
        """This spec with one parameter's default replaced.

        For a default that cannot be written in the spec file because it is computed —
        a path under the CLI home, which the environment can move.
        """
        parameters = [
            replace(p, default=default) if p.name == name else p for p in self._parameters.values()
        ]
        return Spec(parameters)

    def merged_with(self, other: Spec) -> Spec:
        """This spec plus *other*'s parameters; a repeated name takes *other*'s."""
        return Spec([*self._parameters.values(), *other])


def base_spec() -> Spec:
    """The family-wide parameters every Blue CLI declares."""
    return Spec(
        [
            Parameter(
                "OPENAI_API_KEY",
                cast="secret",
                required=True,
                prompt="OpenAI API key (the provider SDK reads it from the environment)",
            ),
            Parameter(
                "BLUE_DATA_LLM_MODEL",
                default="gpt-6-luna",
                required=True,
                prompt="Model to call",
            ),
            Parameter(
                "BLUE_DATA_CLI_FORMAT",
                default="table",
                prompt="Default output format: table, json, nl, or csv",
            ),
            Parameter(
                "BLUE_DATA_CLI_LOG_LIMIT",
                default=80,
                cast="int",
                prompt="Most characters per truncated log line",
            ),
        ]
    )


def load_spec(path: str | os.PathLike[str]) -> Spec:
    """One CLI's own parameters, read from a ``profile_spec.json`` beside its ``profile.py``."""
    body = Path(path).read_text(encoding="utf-8")
    loaded: object = json.loads(body)
    if not isinstance(loaded, dict):
        raise SettingError(f"{path} must hold one JSON object of parameters")
    raw = cast("dict[str, Any]", loaded)
    parameters: list[Parameter] = []
    for name, declared in raw.items():
        if not isinstance(declared, dict):
            raise SettingError(f"{path}: parameter {name} must be a JSON object")
        entry = cast("dict[str, Any]", declared)
        declared_cast = str(entry.get("cast", "str"))
        if declared_cast not in CASTS:
            raise SettingError(f"{path}: parameter {name} declares unknown cast {declared_cast!r}")
        inherits: Any = entry.get("inherits") or ()
        parameters.append(
            Parameter(
                name=str(name),
                default=entry.get("default"),
                prompt=str(entry.get("prompt", "")),
                required=bool(entry.get("required", False)),
                cast=declared_cast,
                inherits=tuple(str(item) for item in inherits),
            )
        )
    return Spec(parameters)


# ------------------------------------------------------------------ secrets --
def encode(value: str) -> str:
    """The form a secret takes on disk.

    Obfuscation, not encryption: anyone who can read the file can decode it, and the file
    mode is the real protection. It exists so a credential is not sitting in plain text in a
    file a screen-share or a backup might expose, which is a different and smaller claim.

    A ``$NAME`` reference is stored as written, because the point of a reference is that the
    value is never in the file at all.
    """
    if value.startswith("$"):
        return value
    return _ENCODED_PREFIX + base64.b64encode(value.encode("utf-8")).decode("ascii")


def decode(raw: str) -> str | None:
    """The usable value behind a stored secret, or None when a reference does not resolve."""
    if raw.startswith("$"):
        return os.environ.get(raw[1:]) or None
    if raw.startswith(_ENCODED_PREFIX):
        try:
            return base64.b64decode(raw[len(_ENCODED_PREFIX) :].encode("ascii")).decode("utf-8")
        except (binascii.Error, ValueError, UnicodeDecodeError):
            return None
    return raw


def display(raw: str) -> str:
    """A stored secret as it may be shown: masked, or a reference named but not resolved.

    A reference is named because naming the variable is how the user learns what to export;
    whether it currently resolves is the other half of that answer. A directly stored secret
    shows as the mask and nothing else.
    """
    if raw.startswith("$"):
        return f"{raw} ({'set' if os.environ.get(raw[1:]) else 'unset'})"
    return MASK


# ------------------------------------------------------------------ on disk --
_reported_modes: set[str] = set()


def _validate_mode(
    path: Path,
    status: os.stat_result,
    forbidden: int,
    repair: int,
    reason: str,
    *,
    directory: bool = False,
) -> None:
    kind_ok = stat.S_ISDIR(status.st_mode) if directory else stat.S_ISREG(status.st_mode)
    expected = "a directory" if directory else "a regular file"
    if not kind_ok:
        raise ProfileError(f"refusing to use {os.fspath(path)!r}: it is not {expected}")
    effective_uid = os.geteuid() if hasattr(os, "geteuid") else None
    if effective_uid is not None and status.st_uid != effective_uid:
        raise ProfileError(
            f"refusing to use {os.fspath(path)!r}: it is owned by uid {status.st_uid}, "
            f"not the current uid {effective_uid}"
        )
    mode = stat.S_IMODE(status.st_mode)
    if mode & forbidden:
        raise ProfileError(
            f"refusing to use {os.fspath(path)!r}: mode {mode:04o} {reason}. "
            f"Set that path to mode {repair:04o} with chmod and try again."
        )


def _refuse_mode(
    path: Path,
    forbidden: int,
    repair: int,
    reason: str,
    *,
    directory: bool = False,
) -> None:
    """Refuse an unsafe type, owner, or permission mode."""
    try:
        status = path.lstat()
    except OSError as error:
        raise ProfileError(f"cannot inspect {path}: {error.strerror or error}") from None
    _validate_mode(path, status, forbidden, repair, reason, directory=directory)


def _read_private_text(path: Path) -> str:
    """Validate the opened settings file before reading it."""
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        _validate_mode(
            path,
            os.fstat(descriptor),
            0o066,
            _FILE_MODE,
            "lets another local account read or replace stored settings",
        )
        with os.fdopen(descriptor, "r", encoding="utf-8") as stream:
            descriptor = -1
            return stream.read()
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _report_wide_mode(path: Path, intended: int, rewritten: bool = False) -> None:
    """Say once that a path is more readable than this CLI would have made it.

    Never widened to match, and never narrowed in silence. A directory is left exactly as
    found. A file is restored to *intended* by the next write, because the write installs a
    fresh temporary file's mode along with its contents — so the warning says which of the
    two is about to happen rather than making one claim for both.
    """
    try:
        mode = path.stat().st_mode & 0o777
    except OSError:
        return
    if not mode & ~intended & 0o777:
        return
    key = str(path)
    if key in _reported_modes:
        return
    _reported_modes.add(key)
    outcome = f"The next write to it restores {intended:04o}." if rewritten else "Left as found."
    sys.stderr.write(
        f"warning: {path} is mode {mode:04o}; this CLI creates it {intended:04o} because a "
        f"profile can hold a provider credential. {outcome}\n"
    )


@contextlib.contextmanager
def _readable(path: Path, doing: str) -> Generator[None, None, None]:
    """Turn a filesystem refusal into this module's own error.

    A directory where a file belongs, a mode that forbids the read, a full disk: none of
    those are this package's exceptions, and none of them should reach a user as a
    traceback. Every command builds a resolver, so an unreadable store took all twelve down.
    """
    try:
        yield
    except OSError as error:
        raise ProfileError(f"cannot {doing} {path}: {error.strerror or error}") from None


def _write_atomic(path: Path, text: str) -> None:
    """Replace *path*'s contents, or leave them exactly as they were."""
    with _readable(path, "write"):
        handle, temporary = tempfile.mkstemp(
            dir=str(path.parent), prefix=f"{path.name}.", suffix=".tmp"
        )
        try:
            os.fchmod(handle, _FILE_MODE)
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                stream.write(text)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(temporary)
            raise


class _Ini(configparser.RawConfigParser):
    """Case-preserving and interpolation-free.

    Parameter names are upper case and *are* environment variable names, which the default
    ``optionxform`` would fold to lower case; and a stored value may contain a ``%``, which
    the interpolating parser would try to expand.
    """

    def optionxform(self, optionstr: str) -> str:
        return optionstr


class ProfileStore:
    """The ``.profiles`` file and the selection beside it."""

    def __init__(self, home: str | os.PathLike[str]) -> None:
        self.home = Path(home).expanduser()

    @property
    def profiles_path(self) -> Path:
        return self.home / ".profiles"

    @property
    def selection_path(self) -> Path:
        return self.home / ".selected.profile"

    # -- lifecycle --
    def initialize(self) -> None:
        """Create and select ``default`` on a first run.

        Keyed on the profiles file, so a profile removed on purpose is not resurrected by
        the next command.
        """
        self._check_home()
        if os.path.lexists(self.profiles_path):
            self._check_file(self.profiles_path)
            return
        if os.path.lexists(self.selection_path):
            self._check_file(self.selection_path)
        self._ensure_home()
        self.add(DEFAULT_PROFILE)
        self.select(DEFAULT_PROFILE)

    # -- reading --
    def list_profiles(self) -> list[str]:
        return self._read().sections()

    def exists(self, name: str) -> bool:
        return self._read().has_section(name)

    def values(self, name: str) -> dict[str, str]:
        ini = self._read()
        if not ini.has_section(name):
            return {}
        return dict(ini.items(name))

    def selected(self) -> str:
        self._check_home()
        path = self.selection_path
        if not os.path.lexists(path):
            return DEFAULT_PROFILE
        self._check_file(path)
        _report_wide_mode(path, _FILE_MODE, rewritten=True)
        with _readable(path, "read"):
            return _read_private_text(path).strip() or DEFAULT_PROFILE

    # -- writing --
    def add(self, name: str) -> None:
        self._check_name(name)
        ini = self._read()
        if ini.has_section(name):
            raise ProfileError(f"profile {name!r} already exists")
        ini.add_section(name)
        self._write(ini)

    def remove(self, name: str) -> None:
        """Delete *name*'s section and nothing else, leaving the selection valid.

        Not the cache directory, not another profile's section, and not the selection file
        unless it pointed here — in which case it moves to a profile that exists:
        ``default`` where that survives, otherwise the first one left.

        Which is also why the last profile cannot go. ``default`` was removable while the
        selection fell back to it unconditionally, so removing it left the store reporting
        a selected profile that was not in the list, and every later run resolving against
        a section that was not there. The store holds at least one profile and the
        selection names one of them; that pair is the invariant, and it is cheaper to keep
        than to check for everywhere it would otherwise have to be.
        """
        ini = self._read()
        if not ini.has_section(name):
            raise ProfileError(f"no such profile: {name!r}")
        remaining = [section for section in ini.sections() if section != name]
        if not remaining:
            raise ProfileError(
                f"{name!r} is the only profile, and the selection has to name one that "
                f"exists. Add another profile first, or delete the profiles file to start "
                f"over."
            )
        selected = self.selected()
        ini.remove_section(name)
        self._write(ini)
        if selected == name:
            self._write_selection(DEFAULT_PROFILE if DEFAULT_PROFILE in remaining else remaining[0])

    def select(self, name: str) -> None:
        if not self.exists(name):
            raise ProfileError(f"no such profile: {name!r}")
        self._write_selection(name)

    def set_value(self, profile: str, name: str, value: str, create: bool = False) -> None:
        """Store one value, in one write.

        *create* adds a missing section as part of that same write. Adding it separately
        first meant a value the file cannot hold was refused with the profile already
        created and empty, so a command that rejected its argument had still changed the
        store.
        """
        self._check_value(name, value)
        ini = self._read()
        if not ini.has_section(profile):
            if not create:
                raise ProfileError(f"no such profile: {profile!r}")
            self._check_name(profile)
            ini.add_section(profile)
        ini.set(profile, name, value)
        self._write(ini)

    # -- internals --
    @staticmethod
    def _check_value(name: str, value: str) -> None:
        """Refuse a value the profiles file cannot hold without changing meaning.

        configparser escapes a newline on the way out, by indenting the continuation, and
        does not escape a carriage return; reading the file back translates that return into
        a line break at top level. A value pasted from a CRLF file therefore came back as a
        second setting, and one shaped like INI came back as a second *profile*. A blank
        value is refused for a quieter reason: it stores, reports success, and is then
        skipped by every read, so it silently unsets whatever was there.
        """
        if any(character in value for character in "\r\n"):
            raise SettingError(
                f"{name} cannot hold a line break: the profiles file is line-oriented, and a "
                f"value containing one would be read back as a separate setting"
            )
        if not value.strip():
            raise SettingError(
                f"{name} cannot be set to a blank value: it would be stored and then ignored "
                f"by every read. Edit the profiles file by hand to remove a setting."
            )

    @staticmethod
    def _check_name(name: str) -> None:
        if not name.strip():
            raise ProfileError("a profile name cannot be blank")
        if name != name.strip() or any(c in name for c in "[]\n\r/\\"):
            raise ProfileError(
                f"profile name {name!r} cannot hold brackets, slashes, newlines or padding"
            )
        if name.upper() == "DEFAULT":
            # configparser's own reserved section name, whose keys leak into every other one.
            if name != DEFAULT_PROFILE:
                raise ProfileError(f"profile name {name!r} is reserved; use 'default'")

    def _check_home(self) -> None:
        home = self.home
        if not os.path.lexists(home):
            return
        _refuse_mode(
            home,
            0o022,
            _DIR_MODE,
            "lets another local account replace profile files",
            directory=True,
        )
        _report_wide_mode(home, _DIR_MODE)

    def _check_file(self, path: Path) -> None:
        _refuse_mode(
            path,
            0o066,
            _FILE_MODE,
            "lets another local account read or replace stored settings",
        )

    def _ensure_home(self) -> None:
        home = self.home
        if os.path.lexists(home):
            self._check_home()
            return
        with _readable(home, "create"):
            home.mkdir(parents=True, exist_ok=True)
            # Set rather than passed to `mkdir`, which applies the ambient umask to it.
            os.chmod(home, _DIR_MODE)

    def _read(self) -> _Ini:
        ini = _Ini()
        self._check_home()
        path = self.profiles_path
        if not os.path.lexists(path):
            return ini
        self._check_file(path)
        _report_wide_mode(path, _FILE_MODE, rewritten=True)
        with _readable(path, "read"):
            body = _read_private_text(path)
        try:
            ini.read_string(body, source=str(path))
        except (configparser.Error, UnicodeDecodeError) as error:
            raise ProfileError(f"{path} cannot be read as a profiles file: {error}") from None
        return ini

    def _write(self, ini: _Ini) -> None:
        self._ensure_home()
        if os.path.lexists(self.profiles_path):
            self._check_file(self.profiles_path)
        buffer = io.StringIO()
        ini.write(buffer)
        _write_atomic(self.profiles_path, buffer.getvalue())

    def _write_selection(self, name: str) -> None:
        self._ensure_home()
        if os.path.lexists(self.selection_path):
            self._check_file(self.selection_path)
        _write_atomic(self.selection_path, name)


# --------------------------------------------------------------- resolution --
@dataclass(frozen=True)
class Resolved:
    """One parameter's winning value and the tier it came from."""

    value: Any
    source: str
    raw: str | None = field(default=None)


ENVIRONMENT = "environment"
DEFAULTED = "default"
UNSET = "unset"


def _refuse_blank_environment(spec: Spec, environment: Mapping[str, str]) -> None:
    """Refuse a parameter exported with nothing in it.

    A precedence chain answers with the first tier that has a value, and a variable exported
    as ``''`` is a tier that has one. Reading it as unset drops the setting to the profile
    without saying so — the same silence the store already refuses at write time, where a
    blank value stores, reports success, and is then ignored by every read. An empty export
    is a state a shell produces easily and never means "use the profile's", so it is
    answered rather than guessed at.
    """
    blank = [p.name for p in spec if p.name in environment and not environment[p.name].strip()]
    if not blank:
        return
    raise SettingError(
        f"exported with an empty value: {', '.join(blank)}. An empty export is not an unset "
        f"one, and reading it as unset would silently hand the setting to the profile. "
        f"Unset the variable, or give it a value."
    )


class ConfigResolver:
    """Environment, then profile, then the spec's default. The command line is above all three.

    A flag never reaches here: the CLI applies it on top of what this returns, which is why
    :meth:`source_of` can report ``environment``, ``profile:<name>`` or ``default`` but
    never ``flag``.

    The environment is snapshotted at construction, before :meth:`export` writes anything
    into it. Resolving against the live environment after an export would attribute a
    profile's own values to the environment tier — the resolver reporting on the exports it
    just made. A second resolver built later in the same run (``profile inspect NAME`` for a
    profile that is not the selected one) is handed the first one's snapshot for the same
    reason.
    """

    def __init__(
        self,
        spec: Spec,
        store: ProfileStore,
        profile: str | None = None,
        environment: Mapping[str, str] | None = None,
    ) -> None:
        self._spec = spec
        self._store = store
        self.profile = profile or store.selected()
        self.environment: Mapping[str, str] = dict(
            environment if environment is not None else os.environ
        )
        _refuse_blank_environment(self._spec, self.environment)
        self._overwritten: dict[str, str | None] = {}
        self._values = store.values(self.profile)

    @property
    def spec(self) -> Spec:
        return self._spec

    def profile_values(self) -> dict[str, str]:
        """The profile's stored values, in the form they take on disk."""
        return dict(self._values)

    def resolve(self, name: str) -> Resolved:
        parameter = self._spec[name]
        found = self._lookup(parameter, seen=set())
        if found is not None:
            raw, source = found
            value = decode(raw) if parameter.is_secret else parameter.cast_value(raw)
            return Resolved(value=value, source=source, raw=raw)
        if parameter.default is not None:
            return Resolved(value=parameter.cast_value(parameter.default), source=DEFAULTED)
        return Resolved(value=None, source=UNSET)

    def get(self, name: str) -> Any:
        return self.resolve(name).value

    def source_of(self, name: str) -> str:
        return self.resolve(name).source

    def export(self) -> None:
        """Write the resolved values into ``os.environ``.

        That is where the provider SDK and any remaining ``envvar=`` option already read
        their configuration from, so exporting is how a profile reaches them at all. A
        variable the caller set themselves is left alone: the environment already outranks
        the profile, and overwriting it here would invert that.
        """
        for parameter in self._spec:
            if parameter.name in self.environment:
                # Already set by the caller, and the environment outranks the profile.
                continue
            value = self.get(parameter.name)
            if value is None:
                continue
            self._overwritten.setdefault(parameter.name, os.environ.get(parameter.name))
            os.environ[parameter.name] = str(value)

    def restore(self) -> None:
        """Put ``os.environ`` back the way :meth:`export` found it.

        One CLI run is one process, so in normal use this runs as the process is ending and
        changes nothing. It matters where several invocations share an interpreter — a test
        harness, or anything embedding the CLI — because a resolver built by the second one
        would otherwise find the first one's exports in ``os.environ`` and report a
        profile's own values as environment values.
        """
        for name, previous in self._overwritten.items():
            if previous is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = previous
        self._overwritten.clear()

    def _lookup(self, parameter: Parameter, seen: set[str]) -> tuple[str, str] | None:
        """The first raw value for *parameter* or anything it inherits from, with its tier.

        Presence, not truth: a variable exported with nothing in it is refused at
        construction, so by here "set" and "has something in it" are the same question.
        """
        if parameter.name in seen:
            return None
        seen.add(parameter.name)
        if parameter.name in self.environment:
            return self.environment[parameter.name], ENVIRONMENT
        stored = self._values.get(parameter.name)
        if stored is not None and stored != "":
            return stored, f"profile:{self.profile}"
        for inherited in parameter.inherits:
            if inherited not in self._spec:
                continue
            found = self._lookup(self._spec[inherited], seen)
            if found is not None:
                return found
        return None
