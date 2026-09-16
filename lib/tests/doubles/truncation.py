"""A model that stops mid-document when its answer overruns the output ceiling.

Every other double in this suite answers in whole documents, so without this one nothing
here produces the response shape a provider produces routinely: valid JSON up to a point,
then nothing, because the token allowance ran out mid-record.

What it delivered is computed while the document is built, not by reading the document
back. Deriving it by scanning the cut text would give the parser under test and its double
the same idea of where a record ends, and a shared mistake would agree with itself.
"""

from __future__ import annotations

import json
from typing import Any

_FENCE_OPEN = "```json\n"
_FENCE_CLOSE = "\n```"


class TruncatingModel:
    """Answers with the records not yet delivered, cut off at *ceiling* characters.

    The cut is positional, so it lands wherever the ceiling falls — between two records,
    inside a value, or partway through an escape sequence. That is the point: a provider
    stops at a token boundary that has nothing to do with the structure of the JSON.

    Continuations are served by position rather than by reading the exclusion list out of
    the prompt. What is being exercised here is the loop's ability to carry on from a
    salvaged answer; a double that re-derived the remainder from the prompt would fail for
    reasons belonging to prompt construction.
    """

    def __init__(
        self,
        records: list[dict[str, Any]],
        *,
        ceiling: int,
        fence: bool = False,
    ) -> None:
        self.records = [dict(record) for record in records]
        self.ceiling = ceiling
        self.fence = fence
        self.prompts: list[str] = []
        self.delivered = 0
        """How many records the caller has been sent in full, across every call."""

        self.truncations = 0
        """How many answers the ceiling cut. Zero means this double never did its job, and
        a test that expects truncation should assert on it rather than assume it."""

    def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        remaining = self.records[self.delivered :]
        if not remaining:
            return "[]"

        parts = [json.dumps(record) for record in remaining]
        body = "[" + ",".join(parts) + "]"

        # Where each record's closing brace falls in the finished document. Recorded here,
        # while the offsets are known, so the count below is arithmetic rather than a
        # second opinion about what a whole record looks like.
        ends: list[int] = []
        at = 1
        for part in parts:
            at += len(part)
            ends.append(at)
            at += 1

        if self.fence:
            body = _FENCE_OPEN + body + _FENCE_CLOSE
            ends = [end + len(_FENCE_OPEN) for end in ends]

        if len(body) <= self.ceiling:
            self.delivered = len(self.records)
            return body

        self.truncations += 1
        self.delivered += sum(1 for end in ends if end <= self.ceiling)
        return body[: self.ceiling]


def cut_inside_record(records: list[dict[str, Any]], *, after: int) -> int:
    """A ceiling that lands partway through the record following *after* whole ones.

    Half a record past the last whole one, so the cut is nowhere near a boundary. Tests
    that need truncation mid-record use this rather than a literal, which drifts into
    landing on a comma the first time a fixture gains a field.
    """
    parts = [json.dumps(record) for record in records]
    if after >= len(parts):
        raise ValueError("no record follows the whole ones, so nothing would be cut")
    whole = 1 + sum(len(part) + 1 for part in parts[:after])
    return whole + max(1, len(parts[after]) // 2)
