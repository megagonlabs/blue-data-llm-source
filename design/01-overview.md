# LlmSource — Overview

## Objective

Most data sources answer questions by reading a table, document, graph, or vector index. A language model can instead answer from information encoded in its parameters. That information may be useful when no maintained table exists, but it can also be incomplete, incorrect, or outdated. LlmSource exposes model-generated answers as typed records through the same query interface as other data sources. Downstream systems can then join and process the records while retaining diagnostics about shape, coverage, and execution.

## What LlmSource Does

blue-data-llm-source presents a language model through the data-layer source interface. It can:

- Fetch structured records from a natural-language request, an optional attribute list, and optional context. The source has no retrieval step or backing store.
- Align the keys a model returns onto the attributes the caller asked for, coerce values to their declared types where the coercion is unambiguous, and drop exact duplicates.
- Report remapped keys, unmatched keys, type mismatches, and key collisions instead of silently discarding them.
- Bound a run by row count, elapsed time, API cost, and provider call count. Calls, time, and cost are reserved before provider contact. Cost uses an upper bound derived from the package's price table and is reconciled after the response; it is not the provider's invoice. A request under a cost ceiling is refused if the model has no known price or its output allowance is unbounded. Rows are capped after parsing. The time limit covers admission and cooperative waiting; [the detailed contract](../lib/README.md#what-the-time-ceiling-actually-bounds) lists its exclusions.
- Apply `limits.*` values in `optional_properties` to one `execute_query` or `execute_query_nl` call without modifying source configuration.
- Accept a caller-owned `Budget` for a total shared across multiple fetches. Each fetch derives a child budget and cannot spend more than the parent has left.
- Estimate the upper cost bound for a flat request without contacting a provider. `estimate` uses the same prompt and bound calculation as runtime reservation. If the model cannot be priced, it returns an unbounded result with a reason.
- Report what a run cost, including the model used, the number of calls, prompt and completion tokens, spend, elapsed time, cache hits, and which limit stopped it.
- Distinguish empty-query, empty-model-response, filtered, partial, limit-stopped, and truncated results through a closed outcome vocabulary.
- Implement `execute_query` and `execute_query_nl` under protocol `llm` and variant `openai`. Inheriting `blue_data.source.source.DataSource` allows the source to be registered alongside SQL, document, graph, and vector sources.
- Accept an injected model client. The library holds no credentials, requires no provider SDK in its base dependencies, and can run offline in tests. Each client declares the budget axes it enforces; a ceiling on any undeclared axis is refused.

## What It Depends On

The package has two runtime dependencies: `blue-data>=0.1.0` and `tenacity>=9.0.0`. `blue-data` supplies the `DataSource` base class used by the other sources in the layer, so interface divergence can be detected by type checking. It also installs `pandas`, `numpy`, and `jsonpath-ng`, which this package does not use directly.

The package depends on the data layer but not on its orchestration runtime. It imports neither `blue` nor `blue_data_ops`, and a test enforces that boundary.

Inheritance does not add protocol-name construction. Callers build an `LlmSource` and register the instance. `connect_source` returns the live instance by name or reconstructs one from the recorded class path after the live entry is gone. Direct construction by protocol name would require a change to the upstream registry.

## Design Philosophy

A model may return a plausible but incomplete answer without signaling failure. LlmSource therefore makes its boundaries observable: it checks requested types, maps or reports returned keys, reserves every provider attempt against a budget, and includes execution and schema diagnostics in each result. These checks show whether the response has the requested shape and whether execution stayed within configured limits. They do not establish factual accuracy or completeness.
