"""Every prompt this package sends, in one module.

One text per prompt, with no second copy elsewhere. A prompt that exists twice drifts in
wording and in variable names, and a change to one of the copies is invisible; with one text
a change is visible as a change, and the recorded cassettes move with it.

Substitution is a single ``str.format_map`` over a fixed set of named fields, and is
deliberately not a template engine. A query is inserted as a value and never re-read, so a
``${...}`` or ``{{...}}`` a user happens to type stays literal instead of being expanded.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

from blue_data_llm_source.types import Attr, Constraint, OneOf

# The two prompt contracts a leaf task can declare, named for what the prompt states: the
# keyed one gives the model the ordered primary key and may give it a row estimate, and the
# other gives neither. Only the keyed one is produced; the other is accepted on a
# wire-version-1 task.
LEGACY_LEAF_FETCH_PROMPT_VERSION: Final = "leaf-fetch-1"
KEYED_LEAF_FETCH_PROMPT_VERSION: Final = "keyed-leaf-fetch-1"

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


@dataclass(frozen=True)
class Slot:
    """One value on its way into a prompt, carrying how it is written.

    :func:`fill` refuses anything that is not a ``Slot``, so every current builder using that
    helper must choose between :meth:`encoded` and :meth:`raw`. The package's prompt builders
    route their substitutions through ``fill``, and behavioral tests exercise their live slots.
    Public ``Slot`` construction and alternative interpolation paths remain possible, so this
    helper does not prove that every future prompt value will be classified.

    :meth:`raw` is the deliberate exception, and it is named so it can be counted. Every use
    is a place where text reaches a model unencoded, and there are only two: what the caller
    typed, and what the caller asked for. An architecture test pins that list, so adding a
    third is a visible act rather than an omission.
    """

    text: str

    @staticmethod
    def encoded(value: object) -> Slot:
        """*value* as a JSON literal — one line, whole, and unable to open another."""
        return Slot(_json(value))

    @staticmethod
    def raw(text: str) -> Slot:
        """*text* exactly as it is. Only for what the caller supplied about their own request.

        The caller is the principal. Encoding their own words protects them from nothing, and
        the sections built from their attributes and their context read better unencoded.
        Nothing a model produced may come through here.
        """
        return Slot(text)

    @staticmethod
    def lines(rendered: list[str]) -> Slot:
        """Several already-built lines as one block.

        The lines come from the renderers below, each of which encodes its own values, so this
        joins rather than converts — and the assertion is what keeps that true. Handed raw text
        this would be a third way to open an instruction line, past both the type and the
        counted raw exception.
        """
        for line in rendered:
            # `splitlines` decides, not a pair of named characters. The oracle counts lines
            # the same way, so a value this accepts cannot add one there.
            if len(f"a{line}b".splitlines()) > 1:
                raise ValueError(
                    "Slot.lines joins already-rendered lines and cannot accept one that "
                    "breaks; encode the value through Slot.encoded first"
                )
        return Slot("\n".join(rendered))


def fill(template: str, **slots: Slot) -> str:
    """Substitute *slots* into *template*, refusing anything that is not a :class:`Slot`.

    One ``str.format_map`` over a fixed set of named fields, applied once. Deliberately not a
    template engine: the implementation this replaces ran Template substitution followed by
    Jinja2 until the string stopped changing, which expanded any ``${...}`` or ``{{...}}`` a
    caller happened to type.
    """
    for name, slot in slots.items():
        # Belt and braces, deliberately. The annotation catches a caller that type-checks;
        # this catches one that does not, and the whole point of the type is that "was this
        # encoded" stops depending on anyone remembering. A checker calling the test
        # redundant is agreeing that the static half works, not that the runtime half is
        # unnecessary.
        if not isinstance(slot, Slot):  # pyright: ignore[reportUnnecessaryIsInstance]
            raise TypeError(
                f"prompt field {name!r} must be a Slot saying how it is written, "
                f"got {type(slot).__name__}"
            )
    return template.format_map({name: slot.text for name, slot in slots.items()})


# Every code point Python counts as ending a line. Taken
# from `str.splitlines` rather than listed by hand, because the differential oracle counts
# lines with `splitlines` and a shorter list here would mean the encoder and the test disagree
# about what a line is. Naming `\n` and `\r` and stopping there is exactly that disagreement:
# U+2028 survives `json.dumps(ensure_ascii=False)` intact, and `splitlines` treats it as a
# break, so model text could open its own line while passing every guard.
LINE_BREAKS: Final = tuple(
    sorted({c for c in map(chr, range(0x110000)) if len(f"a{c}b".splitlines()) > 1})
)


def _escape_breaks(text: str) -> str:
    """Replace every line break with its JSON escape, whatever Python calls a line break."""
    for char in LINE_BREAKS:
        text = text.replace(char, f"\\u{ord(char):04x}")
    return text


def _json(value: object) -> str:
    """*value* as JSON that cannot span a line.

    ``ensure_ascii=False`` because the reader is a model: escaped, a non-Latin name costs
    several times the tokens and tells it less. That flag is also what leaves U+2028 and its
    neighbours literal, so the break class is escaped explicitly afterwards. Everything that
    has to be escaped for safety — the quote, the control characters — is escaped either way.
    """
    return _escape_breaks(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str))


def render_held_entries(shown: Sequence[Any]) -> list[str]:
    """Render the complete prompt lines used for held entries."""
    return [f"- {_json(entry)}" for entry in shown]


def rendered_held_entries_size(shown: Sequence[Any]) -> int:
    """Byte size of the exact held-entry block, including every line separator."""
    return len("".join(f"{line}\n" for line in render_held_entries(shown)).encode("utf-8"))


def _encoded(value: object) -> str:
    """*value* as a JSON string: one line, whole, and readable."""
    return _json(value)


def render_constraints(constraints: Mapping[str, Constraint]) -> list[str]:
    """One line per filter, with the operator said rather than implied.

    Values go through :func:`_encoded`, so the extent of each one is visible and a value
    holding the separator is not mistaken for two. The four words carry what a bare JSON
    array leaves to inference: a model handed ``"region": ["A", "B"]`` has to guess whether
    that is a disjunction, a conjunction, or a field whose value is a list.
    """
    lines: list[str] = []
    for name, constraint in constraints.items():
        label = _encoded(str(name))
        if isinstance(constraint, OneOf):
            alternatives = _json(list(constraint.values))
            lines.append(f"- field {label} must equal one of {alternatives}")
        else:
            lines.append(f"- field {label} must equal {_encoded(constraint.value)}")
    return lines


def render_held(shown: Sequence[Any], total: int) -> list[str]:
    """What this leaf already holds, named back to the model so it is not sent again.

    ``shown`` may be shorter than ``total``: the exclusion list is capped, because it grows
    with every answer while the ask does not, and an uncapped one eventually becomes the
    request. The count is sent even when the entries are not, and the closing sentence is
    what the cap costs — a truncated list read as the whole set invites the model to
    re-send everything above the cut, which is the repetition the list exists to prevent.
    """
    if not total:
        return []
    lines = [
        f"Already returned: {total} records, the {len(shown)} most recent being:"
        if shown
        else f"Already returned: {total} records."
    ]
    # As JSON values, not as JSON strings. An entry is either the value that identifies a
    # record or the record itself, and encoding the record as text would escape every quote
    # inside it — noise for the model and no more unambiguous than the object it came from.
    # Either way the entry is one line and cannot open a second.
    lines.extend(render_held_entries(shown))
    lines.append(
        "Return only records missing from that set. Do not repeat one that is listed, "
        "or one that has already been returned but is not listed."
    )
    return lines


def build_fetch_prompt(
    query: str,
    attrs: list[Attr],
    *,
    caller_context: str = "",
    constraints: Mapping[str, Constraint] | None = None,
    held: Sequence[Any] = (),
    held_total: int = 0,
    primary_keys: Sequence[str] = (),
    estimate: object = None,
) -> str:
    """Build the fetch prompt from its parts, rather than from an assembled string.

    Structured arguments keep encoding centralized: every model-derived value reaches a
    prompt through this function, and a test refuses prompt sections written elsewhere.

    The query is encoded like any other value. Its provenance changes at runtime rather
    than by call site — it is the caller's own text on a flat plan and the analysis rewrite
    on every other — so no rule that depends on which it is can be attached here. Encoding
    it always is what keeps the invariant checkable. It is not a protection: the caller is
    the principal and supplies ``caller_context`` raw, so encoding their own words guards
    them against nothing.
    """
    sections: list[list[str]] = []
    supplied = caller_context.strip()
    if supplied:
        # The caller's own context first. A partitioned plan adds constraints of its own,
        # and dropping what the caller supplied would answer a different question.
        sections.append([supplied])
    if constraints:
        # Constraints have to be here. A partitioned plan gives every leaf the same query
        # and separates them only by constraint, so omitting them sends one identical
        # prompt per partition and the answers are one partition's rows repeated.
        sections.append(["Constraints:", *render_constraints(constraints)])
    if primary_keys:
        sections.append([f"Ordered primary key: {_json(list(primary_keys))}"])
    if estimate is not None:
        sections.append([f"Estimated rows for this leaf: {_json(estimate)}"])
    held_lines = render_held(held, held_total)
    if held_lines:
        sections.append(held_lines)

    context = "\n\n".join("\n".join(section) for section in sections)
    return fill(
        FETCH,
        query=Slot.encoded(query),
        # Raw: the attribute names are the caller's own, and a JSON blob here reads worse to
        # a model than the list does.
        attributes=Slot.raw(render_attributes(attrs)),
        # Raw as a block, but every model-derived value inside it arrived through a renderer
        # that encoded it. `Slot.lines` joins; it does not convert.
        context=Slot.raw(context or "(none)"),
    )


LEAF_FETCH = """\
Your task is to answer a query from your own knowledge and return the result as JSON.

Requirements:
- Return a JSON array of objects. Each object is one record.
- Every record must use the same keys.
- Return only the JSON array. No explanation, commentary, or code fences.
- Respect the original request, retrieval wording, schema, context, and filters below.

Prompt contract: {prompt_version}

Original request: {original_query}
Retrieval wording: {retrieval_query}
Schema: {schema}
Ordered primary key: {primary_keys}
{estimate_section}
Caller context: {context}
Filters: {constraints}
Output:
"""


def build_leaf_fetch_prompt(
    *,
    original_query: str,
    retrieval_query: str,
    attrs: Sequence[Attr],
    caller_context: str,
    constraints: Mapping[str, Constraint],
    primary_keys: Sequence[str] = (),
    estimate: object = None,
) -> str:
    """Build the versioned leaf prompt used by planning and by non-flat execution."""
    schema = [{"name": attr.name, "type": attr.type} for attr in attrs]
    filters = {
        name: (
            {"kind": "one_of", "values": list(value.values)}
            if isinstance(value, OneOf)
            else {"kind": "exact", "value": value.value}
        )
        for name, value in constraints.items()
    }
    estimate_lines = (
        [] if estimate is None else [f"Estimated rows for this leaf: {_json(estimate)}"]
    )
    return fill(
        LEAF_FETCH,
        prompt_version=Slot.encoded(KEYED_LEAF_FETCH_PROMPT_VERSION),
        original_query=Slot.encoded(original_query),
        retrieval_query=Slot.encoded(retrieval_query),
        schema=Slot.encoded(schema),
        primary_keys=Slot.encoded(list(primary_keys)),
        estimate_section=Slot.lines(estimate_lines),
        context=Slot.encoded(caller_context),
        constraints=Slot.encoded(filters),
    )


ANALYSE = """\
Your task is to prepare a data request for retrieval. Do not answer it.

Return a single JSON object with exactly these keys:
- "ask": the request restated for precise retrieval. Resolve pronouns and vague
  references, make implicit qualifiers explicit, and keep the caller's meaning. Do not
  broaden or narrow what was asked.
- "constraints": an object of filters the request implies, as name to value. A value may
  be a finite JSON scalar, or a list of finite JSON scalars when the request names several.
  Strings must be non-empty. Use {{}} if none.

Return only the JSON object. No explanation, no commentary, no code fences.

Request:
{query}
{context}
Output:
"""


def build_analyse_prompt(query: str, context: str = "") -> str:
    """Build the analysis prompt. User text is inserted, never interpreted.

    The caller's ``context`` belongs here as much as it belongs on a fetch. This step is the
    one instructed to resolve pronouns and vague references, and it rewrites the ask every
    leaf then runs — so the sentence supplied to say what "there" means has to reach the
    step doing the resolving, or the whole request is planned around a guess the caller
    already corrected.
    """
    supplied = context.strip()
    return fill(
        ANALYSE,
        # Both slots are the caller's own words on this path: the analysis call is what turns
        # them into something retrievable, so nothing here has been through a model yet.
        query=Slot.raw(query),
        context=Slot.raw(f"\nContext:\n{supplied}\n" if supplied else ""),
    )


SCHEMA = """\
Resolve the output schema for a structured retrieval request. Do not answer the request.

Return one JSON object with exactly these keys:
- "attrs": an ordered list of objects with "name" and optional "type"
- "primary_keys": an ordered list of fields that jointly identify one record

Types may be string, integer, number, boolean, or null. Declared attributes and types must
remain unchanged. Schema mode is {schema_mode}. Return only the JSON object.

Original request: {original_query}
Retrieval wording: {retrieval_query}
Caller context: {context}
Accepted constraints: {constraints}
Declared schema: {schema}
Earlier cardinality: {cardinality}
Output:
"""


def build_schema_prompt(
    *,
    original_query: str,
    retrieval_query: str,
    caller_context: str,
    constraints: object,
    declared_schema: object,
    schema_mode: str,
    cardinality: object = None,
) -> str:
    """Build the independent schema-stage request."""
    return fill(
        SCHEMA,
        original_query=Slot.encoded(original_query),
        retrieval_query=Slot.encoded(retrieval_query),
        context=Slot.encoded(caller_context),
        constraints=Slot.encoded(constraints),
        schema=Slot.encoded(declared_schema),
        schema_mode=Slot.encoded(schema_mode),
        cardinality=Slot.encoded(cardinality),
    )


CARDINALITY = """\
Estimate the number of records for the accepted row unit defined by the request, accepted
constraints, and schema available at this stage. Do not answer the request.

Return one JSON object with exactly these keys:
- "expected": a best-effort non-null non-negative integer point estimate for the accepted row unit
- "lower": a rough plausible non-negative integer lower bound, or null if it cannot be defended
- "upper": a rough plausible non-negative integer upper bound, or null if it cannot be defended
- "row_unit": a non-empty string describing one record

Express uncertainty by widening the lower and upper bounds rather than suppressing the point
estimate. The estimate is planning guidance only. It does not assert completeness or correctness
and does not set a hard stopping condition. Return only the JSON object. Do not return prose or
code fences.

When the request contains numbers, ignore values that do not define how many rows to return,
including unrelated years, thresholds, percentages, IDs, and attribute values. Use
explicit result limits and count the members of row-defining ranges or bounded sets when they
constrain the row count.

Original request: {original_query}
Retrieval wording: {retrieval_query}
Caller context: {context}
Accepted constraints: {constraints}
Schema available at this stage: {schema}
Output:
"""


def build_cardinality_prompt(
    *,
    original_query: str,
    retrieval_query: str,
    caller_context: str,
    constraints: object,
    schema: object,
) -> str:
    """Build the independent structured-cardinality request."""
    return fill(
        CARDINALITY,
        original_query=Slot.encoded(original_query),
        retrieval_query=Slot.encoded(retrieval_query),
        context=Slot.encoded(caller_context),
        constraints=Slot.encoded(constraints),
        schema=Slot.encoded(schema),
    )


BOOTSTRAP = """\
Prepare a structured data request for retrieval. Do not answer the request.

Return one JSON object with exactly these keys:
- "ask": a precise retrieval wording that preserves the original meaning
- "constraints": filters as finite JSON scalars or lists of finite JSON scalars; strings
  must be non-empty
- "row_estimate": an object with expected, lower, upper, and row_unit; counts are
  non-negative integers or null, and row_unit is a non-empty string
- "schema": an object with "attrs" and "primary_keys"; attrs contain name and optional type

Schema mode is {schema_mode}. Declared attributes and their types must remain unchanged.
Return only the JSON object.

Original request: {query}
Caller context: {context}
Declared schema: {schema}
Output:
"""


def build_bootstrap_prompt(
    query: str,
    *,
    caller_context: str,
    declared_attrs: Sequence[Attr],
    declared_key: str | None,
    declared_keys: Sequence[str] = (),
    schema_mode: str,
) -> str:
    """Build the combined rewrite, filter, estimate and schema prompt."""
    schema = {
        "attrs": [{"name": attr.name, "type": attr.type} for attr in declared_attrs],
        "primary_keys": list(declared_keys or ((declared_key,) if declared_key else ())),
    }
    return fill(
        BOOTSTRAP,
        schema_mode=Slot.encoded(schema_mode),
        query=Slot.encoded(query),
        context=Slot.encoded(caller_context),
        schema=Slot.encoded(schema),
    )


PARTITION = """\
Propose one useful partition for this data slice. Do not answer the request.

Return one JSON object with exactly these keys:
- "key": one output or latent dimension
- "values": two or more distinct non-empty strings, at most {max_partitions}
- "ambiguity": a number from 0 to 1
- "child_estimates": a JSON array with one non-negative integer or null per value

Required container shape:
{{"key":"...","values":["..."],"ambiguity":0,"child_estimates":[1]}}
The square-bracket arrays are required. Do not return an object keyed by value.

Do not repeat an ancestor key. If a preferred key is given, use it or return an empty
values list. Return only the JSON object.

Original request: {original_query}
Retrieval wording: {retrieval_query}
Caller context: {context}
Schema: {schema}
Current filters: {constraints}
Ancestor keys: {ancestor_keys}
Preferred key: {preferred_key}
Output:
"""


def build_partition_prompt(
    *,
    original_query: str,
    retrieval_query: str,
    caller_context: str,
    schema: object,
    constraints: object,
    ancestor_keys: Sequence[str],
    preferred_key: str | None,
    max_partitions: int,
) -> str:
    """Build one slice's partition proposal prompt."""
    return fill(
        PARTITION,
        max_partitions=Slot.encoded(max_partitions),
        original_query=Slot.encoded(original_query),
        retrieval_query=Slot.encoded(retrieval_query),
        context=Slot.encoded(caller_context),
        schema=Slot.encoded(schema),
        constraints=Slot.encoded(constraints),
        ancestor_keys=Slot.encoded(list(ancestor_keys)),
        preferred_key=Slot.encoded(preferred_key),
    )


DISTRIBUTION = """\
Estimate the distribution of records across one caller-declared partition domain.
The key and values are fixed. Do not add, remove, rename, merge, or reorder values.

Return one JSON object with exactly these keys:
- "key": the declared partition key
- "partitions": a JSON array with one object per declared value; each object has exactly
  "value" and "estimate"
- "ambiguity": a number from 0 to 1, where 1 is most ambiguous
- "reasoning": a short explanation

Required container shape:
{{"key":"...","partitions":[{{"value":"...","estimate":{{"expected":10,"lower":5,"upper":20}}}}],"ambiguity":0,"reasoning":"..."}}
The square-bracket array is required. Do not return an object keyed by partition value.

Each "estimate" is null when unknown, or an object containing only non-negative integer
"expected", "lower", and "upper" fields. When a reasonable estimate is possible, provide a
non-null "expected"; use null only when no estimate can be made. Estimates are uncertain planning
evidence and must not claim that extraction is complete.

Original request: {original_query}
Retrieval wording: {retrieval_query}
Caller context: {context}
Schema: {schema}
Current filters: {constraints}
Declared partition key: {partition_key}
Declared finite domain: {domain}
Parent estimate: {parent_estimate}
Output:
"""


def build_distribution_prompt(
    *,
    original_query: str,
    retrieval_query: str,
    caller_context: str,
    schema: object,
    constraints: object,
    partition_key: str,
    domain: Sequence[object],
    parent_estimate: object,
) -> str:
    """Ask only for estimates over an immutable, caller-owned finite domain."""
    return fill(
        DISTRIBUTION,
        original_query=Slot.encoded(original_query),
        retrieval_query=Slot.encoded(retrieval_query),
        context=Slot.encoded(caller_context),
        schema=Slot.encoded(schema),
        constraints=Slot.encoded(constraints),
        partition_key=Slot.encoded(partition_key),
        domain=Slot.encoded(list(domain)),
        parent_estimate=Slot.encoded(parent_estimate),
    )


ENUMERATE_ENTITIES = """\
Enumerate every entity for row-by-row retrieval. Do not answer other attributes.

Return one JSON object with exactly these keys:
- "keys": the ordered requested entity-key fields
- "values": the complete list of distinct objects containing every key field

If the entity list cannot be supplied completely, return an empty values list. Return
only the JSON object.

Original request: {original_query}
Retrieval wording: {retrieval_query}
Caller context: {context}
Entity keys: {keys}
Current filters: {constraints}
Output:
"""


def build_entity_prompt(
    *,
    original_query: str,
    retrieval_query: str,
    caller_context: str,
    key: str | None = None,
    keys: Sequence[str] = (),
    constraints: object,
) -> str:
    """Build the bounded entity-enumeration prompt."""
    return fill(
        ENUMERATE_ENTITIES,
        original_query=Slot.encoded(original_query),
        retrieval_query=Slot.encoded(retrieval_query),
        context=Slot.encoded(caller_context),
        keys=Slot.encoded(list(keys or ((key,) if key else ()))),
        constraints=Slot.encoded(constraints),
    )
