"""Cache stores.

Both implement the ``CacheStore`` protocol in ``types``: ``get`` and ``set``, nothing
else. The decomposition standard for this layer is an abstract store with an in-memory
implementation shipped and a persistent backend optional, so neither of these requires a
server and no code path depends on one being present.
"""

from __future__ import annotations

from blue_data_llm_source.cache.file import FileCache
from blue_data_llm_source.cache.memory import MemoryCache

__all__ = ["FileCache", "MemoryCache"]
