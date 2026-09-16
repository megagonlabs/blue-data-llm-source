"""What ran, when it was not what the caller typed.

Outside ``flat`` an analysis call restates the request and that restatement becomes the
query every part runs. It has to be the query: a request like ``I don't know how many
countries are there?`` is a statement of ignorance shaped like a question and is not
retrievable as written, and the rewrite is what makes it answerable.

The same step can therefore broaden or narrow what was asked, and the only thing standing
against that is a sentence in the analysis prompt telling the model not to — an instruction,
not a check. Nothing here guards it. A relatedness test cheap enough to be trustworthy would
reject the very rewrite that justifies the feature, since a useful restatement of the
example above shares almost no words with it, and one loose enough to pass it would catch
nothing.

So the substitution is reported. That claims nothing, cannot misfire, and is what lets a
caller see the question that was actually answered.
"""

from __future__ import annotations

import json
from typing import Any

from blue_data_llm_source.source import LlmSource

CALLER_ASKED = "I don't know how many countries are there?"
WHAT_RAN = "the sovereign countries of the world"


def model(ask: str) -> Any:
    analysis = json.dumps(
        {
            "ask": ask,
            "constraints": {},
            "row_estimate": 30,
            "schema": {"attrs": [{"name": "name"}], "key": None},
        }
    )

    def fn(prompt: str, properties: Any = None) -> str:
        if "Prepare a structured data request" in prompt:
            return analysis
        return json.dumps([{"name": "France"}])

    return fn


def fetch(query: str, ask: str, **props: Any) -> Any:
    merged: dict[str, Any] = {
        "plan.mode": "partitioned",
        "plan.planning_order": "planning.combined",
    }
    merged.update(props)
    return LlmSource().fetch(query, attrs=["name"], llm_fn=model(ask), properties=merged)


class TestTheQuestionThatWasAnsweredIsReported:
    def test_a_rewritten_request_is_published_as_what_ran(self) -> None:
        result = fetch(CALLER_ASKED, WHAT_RAN)
        assert result.meta.asked == WHAT_RAN

    def test_a_request_the_analysis_left_alone_reports_nothing(self) -> None:
        # The pair. A field set on every split run is one a reader learns to skip, and the
        # fact worth reporting is that the question changed -- not that a step exists.
        result = fetch(CALLER_ASKED, CALLER_ASKED)
        assert result.meta.asked is None

    def test_whitespace_is_not_a_rewrite(self) -> None:
        result = fetch(CALLER_ASKED, f"  {CALLER_ASKED}\n")
        assert result.meta.asked is None

    def test_the_flat_path_never_rewrites_and_never_reports(self) -> None:
        # Flat makes no analysis call at all, so the caller's words are the query and there
        # is nothing to disclose.
        result = fetch(CALLER_ASKED, WHAT_RAN, **{"plan.mode": "flat"})
        assert result.meta.asked is None

    def test_the_reported_ask_is_the_one_the_parts_were_sent(self) -> None:
        # The claim has to be about what ran, not about what the analysis returned. Those
        # are the same string today, and a report that drifted from the prompt would be a
        # disclosure of something that never happened.
        sent: list[str] = []
        analysis = json.dumps(
            {
                "ask": WHAT_RAN,
                "constraints": {},
                "row_estimate": 30,
                "schema": {"attrs": [{"name": "name"}], "key": None},
            }
        )

        def fn(prompt: str, properties: Any = None) -> str:
            if "Prepare a structured data request" in prompt:
                return analysis
            sent.append(prompt)
            return json.dumps([{"name": "France"}])

        result = LlmSource().fetch(
            CALLER_ASKED,
            attrs=["name"],
            llm_fn=fn,
            properties={
                "plan.mode": "partitioned",
                "plan.planning_order": "planning.combined",
            },
        )
        assert result.meta.asked is not None
        assert sent
        assert all(json.dumps(result.meta.asked) in prompt for prompt in sent)
