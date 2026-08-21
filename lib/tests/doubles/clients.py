"""Client doubles for the model boundary.

Injection is structural, so a double needs ``complete`` and ``acomplete``. These also carry
the ``usages`` list the real client exposes, for the token detail in ExecMeta, and a
``budget`` they claim against, which is what makes them *budget-aware* — a client whose
provider calls the source can actually bound.

A client that omits the budget is *opaque*: the source cannot see its provider calls and
refuses to pretend a ceiling binds it. The double for that case is local to
``tests/unit/test_source_budget.py``, since it exists only to drive the refusal path.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, cast

from blue_data_llm_source.llm.budget import Budget
from blue_data_llm_source.types import Capability, Usage


class ScriptedClient:
    """Returns queued responses in order and remembers what it was asked."""

    def __init__(
        self,
        responses: Sequence[str] | None = None,
        *,
        model: str = "scripted",
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        cost: float = 0.0,
        budget: Budget | None = None,
    ) -> None:
        self.budget = budget or Budget()
        self.responses: list[str] = list(responses or [])
        self.model = model
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.cost = cost
        self.prompts: list[str] = []
        self.seen_properties: list[dict[str, Any] | None] = []
        self.usages: list[Usage] = []
        self.last_usage: Usage | None = None

    def queue(self, response: str) -> None:
        self.responses.append(response)

    def budget_capabilities(self) -> frozenset[str]:
        """Every axis: this double claims against the budget it is handed, like the real client."""
        return Capability.ALL

    def complete(self, prompt: str, properties: dict[str, Any] | None = None) -> str:
        return self._respond(prompt, properties)

    async def acomplete(self, prompt: str, properties: dict[str, Any] | None = None) -> str:
        return self._respond(prompt, properties)

    def _respond(self, prompt: str, properties: dict[str, Any] | None) -> str:
        budget = (properties or {}).get("budget")
        # `attempt`, like the real client: its cleanup is registered before the claim
        # is taken, so this double models the lifecycle it claims to model.
        with (budget if isinstance(budget, Budget) else self.budget).attempt(
            self.cost
        ) as reservation:
            self.prompts.append(prompt)
            self.seen_properties.append(properties)
            if not self.responses:
                reservation.keep_unpriced()
                raise AssertionError(
                    f"ScriptedClient was asked for response {len(self.prompts)} "
                    "but nothing was queued"
                )
            reservation.settle(self.cost)
            usage = Usage(
                model=self.model,
                prompt_tokens=self.prompt_tokens,
                completion_tokens=self.completion_tokens,
                cost=self.cost,
            )
            self.usages.append(usage)
            sink = (properties or {}).get("usage_sink")
            if isinstance(sink, list):
                cast("list[Usage]", sink).append(usage)
            self.last_usage = usage
            return self.responses.pop(0)


class FailingClient:
    """Raises the exception it was built with, on either surface."""

    def __init__(self, error: Exception) -> None:
        self.error = error
        self.prompts: list[str] = []
        self.usages: list[Usage] = []
        self.last_usage: Usage | None = None

    def complete(self, prompt: str, properties: dict[str, Any] | None = None) -> str:
        self.prompts.append(prompt)
        raise self.error

    async def acomplete(self, prompt: str, properties: dict[str, Any] | None = None) -> str:
        self.prompts.append(prompt)
        raise self.error
