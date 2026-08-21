"""A cache double.

``DefaultLlmClient`` drives its cache through ``get``/``set``, which is what
``CacheStore`` declares. This double also implements ``__contains__`` and ``__getitem__``
so a test can assert on the stored entries directly.
"""

from __future__ import annotations

from typing import Any


class MemoryCache:
    """Dict-backed cache with both the protocol surface and the mapping surface."""

    def __init__(self) -> None:
        self.data: dict[str, Any] = {}
        self.hits = 0
        self.misses = 0

    def get(self, key: str) -> Any | None:
        value = self.data.get(key)
        if value is None:
            self.misses += 1
        else:
            self.hits += 1
        return value

    def set(self, key: str, value: Any) -> None:
        self.data[key] = value

    def __contains__(self, key: str) -> bool:
        return key in self.data

    def __getitem__(self, key: str) -> Any:
        self.hits += 1
        return self.data[key]

    def __setitem__(self, key: str, value: Any) -> None:
        self.data[key] = value

    def __len__(self) -> int:
        return len(self.data)
