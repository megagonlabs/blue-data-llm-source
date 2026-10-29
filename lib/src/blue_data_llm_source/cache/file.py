"""Filesystem cache, one JSON file per entry."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import tempfile
import threading
from pathlib import Path
from typing import Any

_PREFIX = "llmcache-"


class FileCache:
    """Persists entries under a directory, one JSON file each.

    Uses only the standard library, so persistence does not depend on the optional
    provider extra. A key is hashed to a filename because cache keys are long serialized
    request dicts and are not path-safe.
    """

    def __init__(self, directory: str | os.PathLike[str] = ".llm_cache") -> None:
        self.directory = Path(directory).expanduser()
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._require_private_directory()
        self._lock = threading.Lock()
        self._written: dict[Path, tuple[int, int]] = {}

    def _require_private_directory(self) -> None:
        mode = self.directory.lstat()
        if not stat.S_ISDIR(mode.st_mode) or (
            os.name == "posix" and (mode.st_uid != os.getuid() or mode.st_mode & 0o077)
        ):
            raise PermissionError(
                "FileCache directory must be an owned, private directory (mode 0700)"
            )

    def _path(self, key: str) -> Path:
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
        return self.directory / f"{_PREFIX}{digest}.json"

    def get(self, key: str) -> Any | None:
        self._require_private_directory()
        path = self._path(key)
        try:
            with path.open(encoding="utf-8") as handle:
                return json.load(handle)
        except FileNotFoundError:
            return None
        except (OSError, ValueError):
            # ValueError also covers invalid UTF-8 in a cached file.
            return None

    def set(self, key: str, value: Any) -> None:
        path = self._path(key)
        with self._lock:
            self._require_private_directory()
            try:
                # Write to a temporary file and replace, so a reader never sees a
                # half-written entry.
                fd, tmp = tempfile.mkstemp(dir=self.directory, suffix=".tmp")
                try:
                    with os.fdopen(fd, "w", encoding="utf-8") as handle:
                        json.dump(value, handle)
                    os.replace(tmp, path)
                    saved = path.stat()
                    self._written[path] = (saved.st_dev, saved.st_ino)
                except BaseException:
                    Path(tmp).unlink(missing_ok=True)
                    raise
            except (OSError, TypeError, ValueError):
                # Unserializable or unwritable: skip caching rather than fail the call.
                return

    def clear(self) -> None:
        """Remove entries this instance wrote, leaving other files intact."""
        with self._lock:
            self._require_private_directory()
            for entry, identity in list(self._written.items()):
                try:
                    current = entry.lstat()
                except FileNotFoundError:
                    self._written.pop(entry, None)
                    continue
                if stat.S_ISREG(current.st_mode) and (current.st_dev, current.st_ino) == identity:
                    entry.unlink()
                self._written.pop(entry, None)
