"""Filesystem cache, one JSON file per entry."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import threading
from pathlib import Path
from typing import Any

# Prefixed so an entry is identifiable as this cache's, and so `clear` has something to
# match on beyond the extension.
_PREFIX = "llmcache-"
_OWNED_NAME = re.compile(rf"{_PREFIX}[0-9a-f]{{64}}\.json")


class FileCache:
    """Persists entries under a directory, one JSON file each.

    Uses only the standard library, so persistence does not depend on the optional
    provider extra. A key is hashed to a filename because cache keys are long serialised
    request dicts and are not path-safe.
    """

    def __init__(self, directory: str | os.PathLike[str] = ".llm_cache") -> None:
        self.directory = Path(directory).expanduser()
        self.directory.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def _path(self, key: str) -> Path:
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
        return self.directory / f"{_PREFIX}{digest}.json"

    def _owned(self, entry: Path) -> bool:
        """Whether this cache wrote *entry*.

        Names are prefixed and the rest must be a SHA-256 digest, so pointing the cache at a
        directory that already holds JSON — a config directory, another component's state —
        cannot make :meth:`clear` delete it. Deleting every ``*.json`` in a directory the
        caller merely selected is a data loss they cannot recover.
        """
        return bool(_OWNED_NAME.fullmatch(entry.name))

    def get(self, key: str) -> Any | None:
        path = self._path(key)
        try:
            with path.open(encoding="utf-8") as handle:
                return json.load(handle)
        except FileNotFoundError:
            return None
        except (OSError, ValueError):
            # A corrupt or unreadable entry is a miss, not an error. A cache that can
            # fail a request it was meant to make cheaper is worse than no cache.
            #
            # `ValueError` rather than `json.JSONDecodeError`, which is a subclass of it and
            # is not the only way this fails: a file holding bytes that are not UTF-8 raises
            # `UnicodeDecodeError`, which is a `ValueError` and was not caught, so a single
            # corrupt entry raised out of every later request for that key and there was no
            # way to recover it short of deleting the file. The corrupt-cache test wrote
            # valid ASCII, so it could not see this.
            return None

    def set(self, key: str, value: Any) -> None:
        path = self._path(key)
        with self._lock:
            try:
                # Write to a temporary file and replace, so a reader never sees a
                # half-written entry.
                fd, tmp = tempfile.mkstemp(dir=self.directory, suffix=".tmp")
                try:
                    with os.fdopen(fd, "w", encoding="utf-8") as handle:
                        json.dump(value, handle)
                    os.replace(tmp, path)
                except BaseException:
                    Path(tmp).unlink(missing_ok=True)
                    raise
            except (OSError, TypeError, ValueError):
                # Unserialisable or unwritable: skip caching rather than fail the call.
                return

    def clear(self) -> None:
        """Remove this cache's own entries. Anything else in the directory is left alone."""
        with self._lock:
            for entry in self.directory.glob(f"{_PREFIX}*.json"):
                if self._owned(entry):
                    entry.unlink(missing_ok=True)
