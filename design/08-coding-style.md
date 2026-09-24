# LlmSource — Coding Style

## Tooling

| Concern | Tool | Config |
|---------|------|--------|
| Formatting | `ruff format` | `pyproject.toml` |
| Linting | `ruff` | `pyproject.toml` |
| Type checking | `pyright` (strict) | `pyproject.toml` |

Run all three checks before committing a change. They currently run manually because no CI platform is configured. The proposed pipeline is described in [12-deployment-ops.md](12-deployment-ops.md).

### `pyproject.toml` configuration

```toml
[tool.ruff]
line-length = 100
target-version = "py310"

[tool.ruff.lint]
select = ["E", "F", "I", "UP"]   # pycodestyle, pyflakes, isort, pyupgrade

[tool.pyright]
pythonVersion = "3.10"
typeCheckingMode = "strict"
venvPath = "."
venv = ".venv"
```

---

## Type Hints

Type hints are required on all public methods, public class attributes, module-level constants, and private methods.

Use Python 3.10+ syntax throughout, with no imports from `typing` where newer syntax applies:

```python
# correct
def strip_fence(text: str) -> str: ...
async def acomplete(
    self, prompt: str, properties: dict[str, Any] | None = None
) -> str: ...


# incorrect
from typing import Optional


def strip_fence(text: Optional[str]) -> str: ...
```

`Any` appears in exactly one situation: a public parameter that deliberately accepts several shapes and is normalised on the first line of the method. `attrs` accepts names, `Attr` instances, or mappings; `context` accepts a string or a list of strings; `client` and `llm_fn` accept anything structurally adequate. Each is passed straight to a `checks` function or a wrapper that produces a precise type, and nothing downstream sees `Any`. Widening a parameter to avoid an annotation elsewhere is not the same thing and is not allowed.

The library includes a `py.typed` marker file to declare inline type information. The two service packages do not: they are applications, not libraries, and nothing imports them as a typed dependency. `blue-data-kb-source` and `blue-data-user-source` mark only their libraries for the same reason.

---

## Async

I/O-bound methods use `async def`. Non-I/O work is synchronous; making a pure function a coroutine adds an unnecessary `await` for the caller.

The data layer's source query surface is synchronous, so `execute_query` and `execute_query_nl` cannot be coroutines without breaking their contract. They are thin compatibility adapters to the async core, as is `fetch`, the synchronous wrapper over `afetch`. These methods contain no logic beyond argument extraction and the bridge.

```python
# correct — async core, thin sync adapter
class LlmSource:
    async def afetch(self, query: str, ...) -> FetchResult: ...

    def fetch(self, query: str, ...) -> FetchResult:
        return _run(self.afetch(query, ...))

    def execute_query(self, query: Any, ...) -> list[dict[str, Any]]:
        return self.fetch(...).records

# incorrect — logic duplicated across a sync and an async path
class LlmSource:
    def fetch(self, query: str, ...) -> FetchResult:
        # ... a second implementation of the same algorithm
```

Calling `fetch` from inside a running event loop raises `ConfigError` and names `afetch` as the method to use. This avoids a nested event loop failure that would otherwise surface far from its cause.

Reduction and validation modules use synchronous functions. They perform no I/O and are pure, so each can be tested without a fixture.

---

## Naming

| Construct | Convention | Example |
|-----------|-----------|---------|
| Classes | PascalCase | `LlmSource`, `DefaultLlmClient`, `FetchResult` |
| Methods / functions | snake_case | `afetch`, `build_fetch_prompt`, `normalize_attrs` |
| Module-level constants | UPPER_SNAKE_CASE | `PROTOCOL`, `OP_FETCH`, `DEFAULT_MODEL` |
| Module-private constants | leading `_` plus UPPER_SNAKE_CASE | `_ACCEPTED`, `_CONTROL`, `_RESERVED`, `_FENCE` |
| Private members | single `_` prefix | `_resolve_client`, `_call_kwargs`, `_semaphore` |
| Modules / packages | lowercase | `llm/`, `reduction/`, `client.py` |

An asynchronous method that has a synchronous counterpart carries an `a` prefix, following the convention the provider ecosystem already uses: `fetch` and `afetch`, `complete` and `acomplete`.

Do not use double-underscore name mangling. Use a single `_` for all private members.

---

## Imports

- Absolute imports only within the package; no relative imports.
- Order: `__future__`, then stdlib, then third-party, then internal, each group separated by a blank line.
- No wildcard imports.
- Every module begins with `from __future__ import annotations`.

```python
from __future__ import annotations

import asyncio
import time
from typing import Any

from tenacity import (
    AsyncRetrying,
    Retrying,
    retry_if_exception_type,
    stop_after_attempt,
)

from blue_data_llm_source.llm.budget import Budget
from blue_data_llm_source.types import ConfigError, TransportError, Usage
```

Import ordering is enforced by `ruff` through its isort rules.

**Optional dependencies are imported inside the function that uses them.** `litellm` and `diskcache` are imported at the point of use rather than at module scope, so the package installs, imports, and runs its tests with neither present. An `ImportError` at that point is caught and re-raised as a `ConfigError` naming the extra that provides the missing package.

---

## Docstrings

Module docstrings state the module's purpose and any non-obvious constraint that governs it.

Classes and public methods use a one-line docstring unless callers need additional contract information that the signature does not express. Types and defaults remain in signatures. Do not add docstrings to `__init__`; apply the same rule to properties.

Private functions use a docstring only when it preserves a non-obvious invariant or ordering requirement. Otherwise the function name and signature are sufficient.

```python
"""Budget on five axes, reserved before spend and reconciled after it.

The implementation this client is derived from accounted usage after each call. That
records an overrun rather than preventing one, which is fine for a single call and wrong
for a fan-out: by the time the total is read, the money is gone.

Every axis is claimed before the provider is contacted, under one lock, so concurrent
callers cannot race past a ceiling. Cost is claimed the only way an unknown price can be:
an estimated upper bound is reserved before the attempt, then resolved against the best
accounting the client can verify.

Reserving rather than settling is what makes the two hard cases work. A cost that cannot be
read leaves its reservation standing, so an accounting failure is not a free call. And ten
concurrent leaves each commit an estimate before spending, so they cannot collectively
overshoot by ten calls' worth while each sees room for one.
"""


class Budget:
    """Tracks spend against Limits and refuses the attempt that would cross one."""

    def reserve(self, estimated_cost: float = 0.0) -> Reservation:
        """Claim one provider attempt and an upper bound on what it will cost.

        Raises BudgetExceeded naming the axis that refused, before anything is spent. Call
        once per attempt that will reach the provider, including every retry: counting
        logical operations instead of attempts lets a run with three retries make three
        times the calls its limit allows.
        """
        ...

    def _stop(self, limit: str, message: str) -> None:
        # No docstring: the name and the signature say all of it.
        ...

    def _crossed_ceiling(self) -> str | None:
        """The tightest ceiling in this chain that completed work has actually crossed.

        Strictly crossed for cost, because spending exactly the ceiling refuses nothing.
        """
        ...
```

The second form needs the additional contract text because `reserve` reads as one call per operation but is called once per attempt. A caller that assumes otherwise can configure a limit that does not hold.

---

## Comments

Write a comment only when the *why* is non-obvious, such as a hidden constraint, subtle invariant, or workaround for external behaviour. Do not describe what the code does; a well-named identifier already does that.

```python
# correct — explains a non-obvious constraint
# Bound per directory so a per-call cache_dir override gets its own cache
# instead of silently reusing the first one opened.
cache = self._caches.get(cache_dir)

# incorrect — restates the code
# get the cache for this directory
cache = self._caches.get(cache_dir)
```

---

## Error Handling

A `LlmSourceError` base exception is defined in `types.py`. Every custom exception inherits from it.

```python
class LlmSourceError(Exception): ...


class ConfigError(LlmSourceError): ...


class QueryError(LlmSourceError): ...


class TransportError(LlmSourceError): ...


class ParseError(LlmSourceError): ...


class BudgetExceeded(LlmSourceError): ...
```

An exception that a caller may need to act on carries the data required to act. `TransportError` carries a code and the original provider error; `ParseError` carries the raw response text; `BudgetExceeded` carries the name of the axis that fired.

- Do not catch and swallow exceptions silently.
- Let exceptions propagate to interface boundaries, where each interface handles them: MCP tool handlers return the message as structured error content, and CLI handlers render it and exit non-zero.
- Catch specific exception types. Never write a bare `except:`.

**Eight stated exceptions to the last two rules**, each with its reason written at the site:

- Usage accounting converts an unreadable token count to zero instead of failing a call that already succeeded. Accounting is total by contract.
- Cost extraction from a provider response reports an unreadable cost as unknown rather than failing the call, and the reservation's estimate remains charged. Unknown is not zero: converting an accounting failure into a free call is how a ceiling comes to admit every remaining attempt while the running total stays where it started.
- The client resolves its reservation on any failure before re-raising and converts an unrecognised provider failure into a `TransportError` carrying the original. These rules cover every failure path: an outstanding reservation permanently removes headroom from the budget, and an unknown provider exception is not part of this library's contract. If the provider was contacted, a failure resolves at the estimate rather than releasing it; otherwise every failed retry would be free.
- `FileCache` treats an entry it cannot read, write, or serialise as a miss and returns. A cache that can fail the request it was meant to make cheaper is worse than no cache. The catch names the error types, never catching broadly, and a partially written file is removed before the error leaves.
- The client's cache *write* swallows errors raised by the store. By then, the provider has been contacted and billed, so a cache error should not discard the answer. The read is not guarded: it happens before provider contact, so nothing has been lost. A caller whose store is broken should hear about it rather than silently pay full price for every request.
- Closing a cache backend swallows whatever close raises. A store that cannot shut down cleanly is not the caller's problem at that point.
- `LlmSource` wraps unexpected exceptions from `fetch`, `afetch`, or `estimate` in `LlmSourceError` and chains the original. This broad catch enforces the promised error hierarchy, which was previously maintained by per-site handling and missed in some cases. `BaseException` is not caught, so cancellations and interrupts still propagate.
- Deciding whether a client attests to an axis treats any failure as "does not attest". A client that raises when asked what it can be held to has not declared anything, and refusing the ceiling is the safe reading.

Anywhere else, a broad catch is a defect.
