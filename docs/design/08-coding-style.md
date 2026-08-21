# LlmSource — Coding Style

## Tooling

| Concern | Tool | Config |
|---------|------|--------|
| Formatting | `ruff format` | `pyproject.toml` |
| Linting | `ruff` | `pyproject.toml` |
| Type checking | `pyright` (strict) | `pyproject.toml` |

All three run in CI. `ruff format` and `ruff` are also expected to pass locally before committing.

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

Type hints are required on all public methods, public class attributes, and module-level constants. Private methods must also be annotated.

Use Python 3.10+ syntax throughout, with no imports from `typing` where newer syntax applies:

```python
# correct
def strip_fence(text: str) -> str: ...
async def acomplete(self, prompt: str, properties: dict[str, Any] | None = None) -> str: ...

# incorrect
from typing import Optional
def strip_fence(text: Optional[str]) -> str: ...
```

`Any` appears in exactly one situation: a public parameter that deliberately accepts several shapes and is normalised on the first line of the method. `attrs` accepts names, `Attr` instances, or mappings; `context` accepts a string or a list of strings; `client` and `llm_fn` accept anything structurally adequate. Each is passed straight to a `checks` function or a wrapper that produces a precise type, and nothing downstream sees `Any`. Widening a parameter to avoid an annotation elsewhere is not the same thing and is not allowed.

The library includes a `py.typed` marker file to declare inline type information. The two service packages do not: they are applications rather than libraries, and nothing imports them as a typed dependency. `blue-data-kb-source` and `blue-data-user-source` mark only their libraries for the same reason.

---

## Async

I/O-bound methods are `async def`. Non-I/O work is synchronous, because making a pure function a coroutine buys nothing and costs the caller an `await`.

There is one stated exception, and it is the reason the rule reads this way rather than the way it reads for a source with an async base class. The data layer's source query surface is synchronous, so `execute_query` and `execute_query_nl` cannot be coroutines without breaking the contract they exist to match. They are therefore thin compatibility adapters that bridge to the async core, as is `fetch`, the synchronous wrapper over `afetch`. Those three contain no logic beyond argument extraction and the bridge.

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

The bridge raises rather than guessing. Calling `fetch` from inside a running event loop raises `RuntimeError` naming `afetch` as the method to use, because nesting an event loop is the kind of failure that surfaces far from its cause.

Reduction and validation modules are synchronous functions. They perform no I/O and are pure, which is what lets each one be tested without a fixture.

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

from tenacity import AsyncRetrying, Retrying, retry_if_exception_type, stop_after_attempt

from blue_data_llm_source.llm.budget import Budget
from blue_data_llm_source.types import ConfigError, TransportError, Usage
```

Import ordering is enforced by `ruff` through its isort rules.

**Optional dependencies are imported inside the function that uses them.** `litellm` and `diskcache` are imported at the point of use rather than at module scope, so the package installs, imports, and runs its tests with neither present. An `ImportError` at that point is caught and re-raised as a `ConfigError` naming the extra that provides the missing package.

---

## Docstrings

Module docstrings carry the *why*: what the module is for, and what constraint or past failure shaped it. These may run to several lines and are the main place where rationale belongs in the code rather than in a design document.

A class or public method docstring opens with one line. It gains a paragraph below that line only where a caller can get the behaviour wrong from the signature alone, which is where the same *why* rule applies: a contract that reads as obvious and is not, or a constraint that a reasonable reading would miss. Anything self-explanatory from the name stops at the one line. No docstrings on `__init__`, and no multi-line parameter documentation, because types belong in signatures. A property follows the same test as a method: none where the name says it, one where a caller could get the contract wrong from the signature alone. Two carry one, and both are cases where the value is not what the name suggests on first reading.

A private function follows the same test as a public one rather than a blanket prohibition: no docstring where the name says it, and a docstring where the *why* would otherwise be lost. Most of the private functions here have one, because most of them exist to hold an invariant that a reasonable reading would miss — which ordering a claim and an import must take, why a comma is stripped only in one shape, why a finite check is asked of floats and not of ints. That rationale has nowhere better to live: a design document is too far from the code to be re-read when the line is edited, and a comment inside the body is not returned by `help()` or shown by an editor at the call site.

This is a deliberate departure from the surrounding house style, which prohibits private docstrings outright. It is written down because the alternative is a rule the code has never followed, and a style rule that the code silently ignores is worse than a stated exception.

```python
"""Budget on five axes, reserved before spend and reconciled after it.

The implementation this client is derived from accounted usage after each call. That
records an overrun rather than preventing one, which is fine for a single call and wrong
for a fan-out: by the time the total is read, the money is gone.

Every axis is claimed before the provider is contacted, under one lock, so concurrent
callers cannot race past a ceiling. Cost is claimed the only way an unknown price can be:
an estimated upper bound is reserved before the attempt and reconciled against the real
cost afterwards, releasing the difference.

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

The second form earns its paragraph: `reserve` reads as one call per operation, it is one call per attempt, and a caller that gets that wrong configures a limit that does not hold.

---

## Comments

Write a comment only when the *why* is non-obvious: a hidden constraint, a subtle invariant, or a workaround for specific external behaviour. Never describe what the code does, because a well-named identifier already does that.

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

- Usage accounting converts an unreadable token count to zero rather than failing a call that already succeeded. Accounting is total by contract.
- Cost extraction from a provider response reports an unreadable cost as unknown rather than failing the call, and the reservation's estimate then stands as the charge. Unknown is not zero: converting an accounting failure into a free call is how a ceiling comes to admit every remaining attempt while the running total stays where it started.
- The client resolves its reservation on any failure before re-raising, and converts an unrecognised provider failure into a `TransportError` carrying the original. Both must cover every failure path rather than an enumerated subset: a reservation left outstanding is headroom the budget will never grant again, and a provider exception the caller has never heard of is not a contract this library wants to pass through. A failure after the provider was contacted resolves at the estimate rather than releasing it, or every failed retry would be free.
- `FileCache` treats an entry it cannot read, write, or serialise as a miss and returns. A cache that can fail the request it was meant to make cheaper is worse than no cache. The catch names the error types rather than catching broadly, and a partially written file is removed before the error leaves.
- The client's cache *write* swallows anything the store raises. By the time it runs the provider has been contacted and billed, so losing that answer to a cache error is the one outcome worth ruling out. The read is deliberately not guarded: it happens before the provider is contacted, nothing is lost yet, and a caller whose store is broken should hear about it rather than silently pay full price for every request.
- Closing a cache backend swallows whatever close raises. A store that cannot shut down cleanly is not the caller's problem at that point.
- `LlmSource` wraps anything unexpected escaping `fetch`, `afetch` or `estimate` in `LlmSourceError`, chaining the original. This is the one broad catch that exists to *enforce* the error rule rather than to bend it: the hierarchy is promised by four documents and was previously held together by per-site discipline, which failed at every site anyone missed. `BaseException` is deliberately not caught, so a cancellation or an interrupt still propagates.
- Deciding whether a client attests to an axis treats any failure as "does not attest". A client that raises when asked what it can be held to has not declared anything, and refusing the ceiling is the safe reading.

Anywhere else, a broad catch is a defect.
