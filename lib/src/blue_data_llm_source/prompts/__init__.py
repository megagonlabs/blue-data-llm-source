"""Every prompt this package sends, in one module.

The monolith holds two copies of this prompt with divergent wording and different
variable names, one on the source and one on the operator. Keeping them in one place
means a change is visible as a change, and the recorded cassettes move with it.

Substitution is a single ``str.format_map`` over a fixed set of named fields. It is
deliberately not a template engine: the monolith runs Template substitution followed by
Jinja2, repeatedly, until the string stops changing, which expands any ``${...}`` or
``{{...}}`` that happens to appear in a user's query.
"""

from __future__ import annotations

from blue_data_llm_source.types import Attr

FETCH = """\
Your task is to answer a query from your own knowledge and return the result as JSON.

Requirements:
- Return a JSON array of objects. Each object is one record.
- Every record must use the same keys.
- Return only the JSON array. No explanation, no commentary, no code fences.
- If attributes are specified below, every record must contain exactly those keys.
- If attribute types are specified, values must match those types.
- If no attributes are specified, choose the keys that best represent the answer.
- If context is provided, use it. It may clarify what an attribute name means.
- Prefer returning a non-empty result. If the query is ambiguous, use your best judgement.

Query:
{query}

Attributes:
{attributes}

Context:
{context}

Output:
"""


def render_attributes(attrs: list[Attr]) -> str:
    """Render the requested attributes as prompt text."""
    if not attrs:
        return "(none specified)"
    lines: list[str] = []
    for a in attrs:
        if a.type:
            lines.append(f"- {a.name}: {a.type}")
        else:
            lines.append(f"- {a.name}")
    return "\n".join(lines)


def build_fetch_prompt(query: str, attrs: list[Attr], context: str) -> str:
    """Build the fetch prompt. User text is inserted, never interpreted."""
    return FETCH.format_map(
        {
            "query": query,
            "attributes": render_attributes(attrs),
            "context": context.strip() or "(none)",
        }
    )
