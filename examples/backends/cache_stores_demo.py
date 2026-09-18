"""The two cache stores, exercised directly: MemoryCache and FileCache.

``DefaultLlmClient`` drives a cache through two methods, ``get(key)`` and ``set(key,
value)``, which is all the ``CacheStore`` protocol declares. This script drives the
library's own two stores through that same pair, so what it shows is the storage
behaviour a cached fetch depends on -- bounded eviction, persistence across processes,
and a ``clear()`` that will not delete a file it did not write.

No provider is involved, because none is needed to answer questions about a store.
``backends/file_cache_demo.py`` is the end-to-end version, where a second identical
fetch is served from disk; that one needs a credential for its first call.

Run from the repository root:
    python3 examples/backends/cache_stores_demo.py
"""

import json
import shutil
import tempfile
from pathlib import Path

from blue_data_llm_source import FileCache, MemoryCache


def payload(text: str) -> dict[str, object]:
    """A provider response shaped the way the client stores one."""
    return {"choices": [{"message": {"content": text}}]}


def memory_store() -> None:
    print("MemoryCache is bounded and evicts least-recently-used")
    cache = MemoryCache(max_entries=2)
    cache.set("paris", payload('[{"name": "Orly"}]'))
    cache.set("berlin", payload('[{"name": "Tegel"}]'))
    cache.get("paris")  # touching paris makes berlin the coldest entry
    cache.set("madrid", payload('[{"name": "Barajas"}]'))

    for key in ("paris", "berlin", "madrid"):
        hit = cache.get(key)
        print(f"  {key:7} {'hit ' if hit else 'miss'}")
    print("  entries", len(cache))


def file_store(directory: Path) -> None:
    print()
    print("FileCache persists one JSON file per entry")
    cache = FileCache(directory)
    cache.set("paris", payload('[{"name": "Orly"}]'))
    print("  written  ", sorted(p.name for p in directory.iterdir()))

    reopened = FileCache(directory)
    print("  reopened ", json.dumps(reopened.get("paris")))
    print("  missing  ", reopened.get("lisbon"))

    foreign = directory / "settings.json"
    foreign.write_text("{}", encoding="utf-8")
    cache.clear()
    print("  after clear, files the cache did not write are still here:")
    print("   ", sorted(p.name for p in directory.iterdir()))


def main() -> None:
    memory_store()
    directory = Path(tempfile.mkdtemp(prefix="llmdb-cache-demo-"))
    try:
        file_store(directory)
    finally:
        shutil.rmtree(directory)


if __name__ == "__main__":
    main()
