"""The on-disk response cache: ask the same question twice, pay once.

``llm.cache`` turns on a diskcache-backed store under ``llm.cache_dir``. The key is the
whole provider request, so the second of two identical fetches is served from disk: no
provider call, no tokens, no API cost. Rows from a cache hit still count against a row
ceiling, and admission still checks elapsed time, which is what keeps a caller-owned
budget across a sequence whose later calls the cache shortens.

The cache lives inside the client, below the source, so the first call contacts the
provider. This script therefore needs a credential. Its offline sibling,
``backends/cache_stores_demo.py``, drives the same stores through the ``CacheStore``
protocol directly and needs no credential.

Requires: OPENAI_API_KEY, and the provider extra (pip install 'blue-data-llm-source[llm]'),
which brings diskcache along with litellm.

Run from the repository root:
    python3 examples/backends/file_cache_demo.py
"""

import os
import shutil
import tempfile
from pathlib import Path

from blue_data_llm_source import (
    DefaultLlmClient,
    FetchResult,
    LlmSource,
    LlmSourceError,
    Outcome,
)

QUERY = "the four largest lakes in Switzerland by surface area"
ATTRS = ["name", "canton"]


def report(label: str, result: FetchResult) -> None:
    meta = result.meta
    print(label)
    print("  records         ", len(result.records))
    print("  calls           ", meta.calls)
    print("  cache hits      ", meta.cache_hits)
    print("  prompt tokens   ", meta.prompt_tokens)
    print("  cost            ", round(meta.cost, 6))
    print("  latency seconds ", round(meta.latency_seconds, 3))


def main() -> None:
    if not os.environ.get("OPENAI_API_KEY"):
        raise SystemExit("Set OPENAI_API_KEY before running this example.")
    cache_dir = Path(tempfile.mkdtemp(prefix="llmdb-file-cache-"))
    properties = {
        "llm.model": "gpt-4o-mini",
        "llm.max_tokens": 512,
        "llm.temperature": 0,
        "llm.cache": True,
        "llm.cache_dir": str(cache_dir),
    }
    try:
        # One client across both fetches: the cache backend it opens for this directory
        # is held on the client, and `close()` at the end of the `with` releases it.
        with DefaultLlmClient(properties) as client:
            source = LlmSource(properties=properties, client=client)
            try:
                first = source.fetch(QUERY, attrs=ATTRS)
                if first.meta.outcome != Outcome.OK or not first.records:
                    report("first fetch (provider)", first)
                    raise SystemExit("first fetch did not return records")
                second = source.fetch(QUERY, attrs=ATTRS)
                if (
                    second.meta.outcome != Outcome.OK
                    or second.meta.cache_hits < 1
                    or second.records != first.records
                ):
                    report("second fetch (same question)", second)
                    raise SystemExit("second fetch was not the expected cache hit")
            except LlmSourceError as error:
                print(f"{type(error).__name__}: {error}")
                raise SystemExit(1) from error

        report("first fetch (provider)", first)
        print()
        report("second fetch (same question)", second)
        print()
        print("cache files written:", len(list(cache_dir.iterdir())))
        print("same records:", first.records == second.records)
    finally:
        shutil.rmtree(cache_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
