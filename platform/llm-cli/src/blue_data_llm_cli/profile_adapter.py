"""CLI storage protections and invocation lifetime over blue-data configuration."""

from __future__ import annotations

import configparser
import contextlib
import io
import os
import stat
import sys
import tempfile
from collections.abc import Generator, Mapping
from pathlib import Path
from typing import Any

from blue_data.utils.config import (
    DEFAULT_PROFILE,
    ConfigError,
    ConfigSpec,
    ParamSpec,
    ProfileError,
)
from blue_data.utils.config import (
    ConfigResolver as SharedResolver,
)
from blue_data.utils.config import (
    ProfileStore as SharedStore,
)

_DIR_MODE = 0o700
_FILE_MODE = 0o600

_reported_modes: set[str] = set()

_EXPECTED = {"bool": "true or false", "int": "a whole number", "float": "a number"}


def cast_value(param: ParamSpec, value: Any) -> Any:
    """Cast *value* for *param*, naming the parameter and the type it needs on failure.

    The shared casts report Python's conversion text for numbers, and a boolean failure
    does not name the parameter at all.
    """
    try:
        return param.cast_value(value)
    except ConfigError:
        expected = _EXPECTED.get(param.cast)
        if expected is None:
            raise
        raise ConfigError(f"{param.name} must be {expected}, got {value!r}") from None


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

    Filesystem errors become ``ProfileError`` so commands can report them consistently.
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


class ProfileStore(SharedStore):
    """Shared profile operations with private, atomic file and selection I/O."""

    def initialize(self, name: str = DEFAULT_PROFILE) -> bool:
        self._check_name(name)
        self._check_home()
        if os.path.lexists(self.profiles_path):
            self._check_file(self.profiles_path)
            return False
        if os.path.lexists(self.selected_path):
            self._check_file(self.selected_path)
        created = super().initialize(name)
        if created:
            self.select(name)
        return created

    def add(self, name: str, values: dict[str, str] | None = None) -> None:
        self._check_name(name)
        for key, value in (values or {}).items():
            self.check_value(key, value)
        try:
            super().add(name, values=values)
        except ProfileError as error:
            if "already exists" in str(error):
                raise ProfileError(
                    f"{error}. Select it with `blue-llmdb profile select NAME`."
                ) from None
            raise

    def selected(self) -> str:
        self._check_home()
        path = self.selected_path
        if not os.path.lexists(path):
            return DEFAULT_PROFILE
        self._check_file(path)
        _report_wide_mode(path, _FILE_MODE, rewritten=True)
        with _readable(path, "read"):
            return _read_private_text(path).strip() or DEFAULT_PROFILE

    def select(self, name: str) -> None:
        self._check_name(name)
        if not self.exists(name):
            raise ProfileError(f"no such profile: {name!r}")
        self._write_selection(name)

    def remove(self, name: str) -> None:
        remaining = [entry for entry in self.list_profiles() if entry != name]
        if not self.exists(name):
            raise ProfileError(f"no such profile: {name!r}")
        if not remaining:
            raise ProfileError(f"{name!r} is the only profile. Add another profile first.")
        selected = self.selected()
        with _readable(self.selected_path, "remove selection for"):
            super().remove(name)
        if selected == name:
            self._write_selection(DEFAULT_PROFILE if DEFAULT_PROFILE in remaining else remaining[0])

    def set_value(self, name: str, key: str, value: str, create: bool = False) -> None:
        self.check_value(key, value)
        if create and not self.exists(name):
            self.add(name, values={key: value})
        else:
            super().set_value(name, key, value)

    @staticmethod
    def check_value(name: str, value: str) -> None:
        """Refuse a value the profiles file cannot hold without changing meaning.

        Newlines and carriage returns can be parsed as another setting or profile on
        reload. Blank values are skipped by reads, so they cannot be stored as settings.
        """
        if any(character in value for character in "\r\n"):
            raise ConfigError(
                f"{name} cannot hold a line break: the profiles file is line-oriented, and a "
                f"value containing one would be read back as a separate setting"
            )
        if not value.strip():
            raise ConfigError(
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

    def _parser(self) -> configparser.ConfigParser:
        ini = configparser.ConfigParser(interpolation=None)
        ini.optionxform = str  # pyright: ignore[reportAttributeAccessIssue]
        self._check_home()
        path = self.profiles_path
        if not os.path.lexists(path):
            return ini
        self._check_file(path)
        _report_wide_mode(path, _FILE_MODE, rewritten=True)
        try:
            with _readable(path, "read"):
                body = _read_private_text(path)
            ini.read_string(body, source=str(path))
        except (configparser.Error, UnicodeDecodeError) as error:
            line = getattr(error, "lineno", None)
            location = f" at line {line}" if isinstance(line, int) else ""
            raise ProfileError(
                f"{path} cannot be read as a profiles file ({type(error).__name__}{location})"
            ) from None
        return ini

    def _write(self, parser: configparser.ConfigParser) -> None:
        self._ensure_home()
        if os.path.lexists(self.profiles_path):
            self._check_file(self.profiles_path)
        buffer = io.StringIO()
        parser.write(buffer)
        _write_atomic(self.profiles_path, buffer.getvalue())

    def _write_selection(self, name: str) -> None:
        self._ensure_home()
        if os.path.lexists(self.selected_path):
            self._check_file(self.selected_path)
        _write_atomic(self.selected_path, name)


class ConfigResolver(SharedResolver):
    """Refuse CLI blanks, name the parameter in cast failures, and restore exports."""

    def __init__(
        self,
        spec: ConfigSpec,
        store: SharedStore,
        profile: str | None = None,
        environment: Mapping[str, str] | None = None,
        *,
        allow_missing: bool = False,
    ) -> None:
        self.environment = dict(os.environ if environment is None else environment)
        blank = [
            p.name
            for p in spec
            if p.name in self.environment and not self.environment[p.name].strip()
        ]
        if blank:
            raise ConfigError(
                f"exported with an empty value: {', '.join(blank)}. Unset it, or give it a value."
            )
        super().__init__(spec, store, profile=profile, environ=dict(self.environment))
        # Shared resolution reads its snapshot but exports into this invocation's environment.
        self.environ = os.environ
        self._previous = {p.name: os.environ.get(p.name) for p in spec}
        if allow_missing and not store.exists(self.profile):
            self._values = {}

    def get(self, name: str, override: Any = None) -> Any:
        try:
            return super().get(name, override)
        except ConfigError:
            self._recast(name, override)
            raise

    def source_of(self, name: str) -> str:
        try:
            return super().source_of(name)
        except ConfigError:
            self._recast(name)
            raise

    def _recast(self, name: str, override: Any = None) -> None:
        """Repeat the cast that failed through `cast_value`, in precedence order."""
        param = self.spec[name]
        if override is not None:
            cast_value(param, override)
            return
        chain = self._chain(param)
        stored = self.profile_values()
        candidates = [(p, self.environment.get(p.name)) for p in chain]
        candidates += [(p, stored.get(p.name)) for p in chain if not p.is_secret]
        candidates += [(p, p.default) for p in chain]
        for candidate, raw in candidates:
            if raw:
                cast_value(candidate, raw)

    def export(self) -> dict[str, str]:
        """Apply the profile, refusing a stored blank that would drop its setting unseen."""
        for key, value in self.profile_values().items():
            if key in self.spec and not value.strip():
                command = f"profile config {key}" + ("" if self.spec[key].is_secret else " VALUE")
                raise ConfigError(
                    f"{key} is blank in profile {self.profile!r} in {self.store.profiles_path}; "
                    f"it would be read as unset. Set it with `blue-llmdb {command} --profile "
                    f"NAME`, or delete that line."
                )
        return super().export()

    def restore(self) -> None:
        for name, previous in self._previous.items():
            if previous is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = previous
        self._previous.clear()
