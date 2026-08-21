"""In-process cache."""

from __future__ import annotations

import threading
from typing import Any


class MemoryCache:
    """A bounded in-process cache with least-recently-used eviction.

    Bounded rather than unbounded because the values are provider responses and a long
    running process asking many distinct questions would otherwise grow without limit.
    """

    def __init__(self, max_entries: int = 512) -> None:
        if max_entries <= 0:
            raise ValueError("max_entries must be greater than zero")
        self.max_entries = max_entries
        self._data: dict[str, Any] = {}
        self._lock = threading.Lock()

    def get(self, key: str) -> Any | None:
        with self._lock:
            if key not in self._data:
                return None
            value = self._data.pop(key)
            self._data[key] = value  # move to the most-recent end
            return value

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            if key in self._data:
                self._data.pop(key)
            elif len(self._data) >= self.max_entries:
                oldest = next(iter(self._data))
                self._data.pop(oldest)
            self._data[key] = value

    def clear(self) -> None:
        with self._lock:
            self._data.clear()

    def __len__(self) -> int:
        return len(self._data)
