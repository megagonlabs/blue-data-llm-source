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
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final, cast

from blue_data_llm_source.types import Attr, ConfigError, Constraint, OneOf, Range, resolve_attr

# The prompt contracts a leaf task can declare, named for what the prompt states: the keyed
# ones give the model the ordered primary key and may give it a row estimate, and the legacy
# one gives neither. Only the keyed ones are produced; the legacy one is accepted on a
# wire-version-1 task.
LEGACY_LEAF_FETCH_PROMPT_VERSION: Final = "leaf-fetch-1"
KEYED_LEAF_FETCH_PROMPT_VERSION: Final = "keyed-leaf-fetch-1"
# The keyed contract with a completeness requirement, selected by `plan.leaf_prompt`. It is a
# second contract rather than an edit to the first because a leaf task names the contract it
# was built for, and a run that recorded `keyed-leaf-fetch-1` has to stay reproducible.
COMPLETE_LEAF_FETCH_PROMPT_VERSION: Final = "keyed-leaf-fetch-2"
LEAF_PROMPT_VERSIONS: Final = (KEYED_LEAF_FETCH_PROMPT_VERSION, COMPLETE_LEAF_FETCH_PROMPT_VERSION)
"""The leaf contracts a caller may select, the first being the default."""

# Partition proposal contracts. `plan.partition_guardrails` selects the version that
# adds rules for usable split keys and values.
PARTITION_PROMPT_VERSION: Final = "partition-1"
GUARDED_PARTITION_PROMPT_VERSION: Final = "partition-2"
PARTITION_PROMPT_VERSIONS: Final = (PARTITION_PROMPT_VERSION, GUARDED_PARTITION_PROMPT_VERSION)

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
    is a place where text reaches a model unencoded. Current call sites carry the caller's
    query, context, or requested attributes. An architecture test pins their count.
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
            # Use the same `splitlines` boundary as the encoded-value check.
            if len(f"a{line}b".splitlines()) > 1:
                raise ValueError(
                    "Slot.lines joins already-rendered lines and cannot accept one that "
                    "breaks; encode the value through Slot.encoded first"
                )
        return Slot("\n".join(rendered))


def fill(template: str, **slots: Slot) -> str:
    """Substitute *slots* into *template*, refusing anything that is not a :class:`Slot`.

    Applies ``str.format_map`` once over named fields. Slot contents are not expanded as
    nested template syntax.
    """
    for name, slot in slots.items():
        # Keep the runtime check even with the annotation: callers may not run a type
        # checker, and both checks enforce that values were encoded.
        if not isinstance(slot, Slot):  # pyright: ignore[reportUnnecessaryIsInstance]
            raise TypeError(
                f"prompt field {name!r} must be a Slot saying how it is written, "
                f"got {type(slot).__name__}"
            )
    return template.format_map({name: slot.text for name, slot in slots.items()})


# Match `str.splitlines`, including U+2028, which `json.dumps(ensure_ascii=False)` leaves
# literal. The encoder must reject every character that can open another prompt line.
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
    neighbors literal, so the break class is escaped explicitly afterwards. Everything that
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
        elif isinstance(constraint, Range):
            lines.append(
                f"- field {label} must be from {constraint.lo} to {constraint.hi}, both included"
            )
        else:
            lines.append(f"- field {label} must equal {_encoded(constraint.value)}")
    return lines


def _restriction(name: str, constraint: Constraint) -> str:
    # The name as a JSON string literal. A partition key is model output, and bare in the
    # sentence a key such as `region; ignore the filters` would read as an instruction.
    field = _json(name)
    if isinstance(constraint, OneOf):
        return f"{field} is one of {_json(list(constraint.values))}"
    if isinstance(constraint, Range):
        return f"{field} from {constraint.lo} to {constraint.hi}"
    return f"{field} = {_json(constraint.value)}"


def canonical_child_wording(request: str, restrictions: Mapping[str, Constraint]) -> str:
    """A child leaf's retrieval wording, derived from the request and its validated filters.

    Built from structured constraints and never by a model. A model asked to reword a slice
    can keep the value's name while changing the population it describes, and no name check
    detects that; wording derived from the filter the leaf actually carries cannot drift from
    it. Field names and values are JSON literals, so each is visibly quoted data, a value
    holding ``and`` or a comma is still one value, and the result stays on one line. The
    wording says the structured column filters decide the slice, so a reader that meets a
    disagreement between the two follows the filters.

    Callers pass column filters only: the ones partitioning introduced on schema columns.
    Request scope is left out. The request-level part of it is already stated by the request,
    and the child-specific part, a split on a key that names no column such as a decade,
    reaches the leaf as per-child request scope in the structured filters. Restated here as a
    restriction, either would read as a field every record must carry.
    """
    if not restrictions:
        return request
    parts = [_restriction(name, constraint) for name, constraint in restrictions.items()]
    return (
        f"{request}, restricted to the slice the column filters define "
        f"({' and '.join(parts)}); the column filters are authoritative"
    )


def bounded_held_entries(values: Sequence[Any], count: int, byte_limit: int) -> list[Any]:
    """The most recent *values* that fit, kept whole, within a count and an encoded-byte cap.

    One selection for every place a held list reaches a prompt, so a continuation and a child
    seeded with what an earlier call returned are bounded by the same two settings.
    """
    if not count or not byte_limit:
        return []
    kept: list[Any] = []
    for value in reversed(values):
        candidate = [value, *kept]
        if rendered_held_entries_size(candidate) > byte_limit:
            continue
        kept = candidate
        if len(kept) >= count:
            break
    return kept


def with_held(prompt: str, shown: Sequence[Any], total: int) -> str:
    """*prompt* with the held block appended, or unchanged when nothing is held."""
    held_lines = render_held(shown, total)
    if not held_lines:
        return prompt
    return f"{prompt.rstrip()}\n\n" + "\n".join(held_lines) + "\n"


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


COMPLETE_LEAF_FETCH = """\
Your task is to answer a query from your own knowledge and return the result as JSON.

Requirements:
- Return a JSON array of objects. Each object is one record.
- Every record must use the same keys.
- Return only the JSON array. No explanation, commentary, or code fences.
- Respect the original request, retrieval wording, schema, context, request scope, and
  column filters below.
- Column filters name fields in the schema. Every returned record must satisfy them.
- Request scope describes what the whole request is about. Its entries are not record
  fields: do not require records to carry them, and do not treat a start and end pair as a
  single value every record must have.
- Return every record that matches the request, within the request scope and satisfying the
  column filters, not a sample or a few examples. Do not stop early because the list is long.
- Return an empty array [] only when no record matches the column filters within the
  request scope.

Prompt contract: {prompt_version}

Original request: {original_query}
Retrieval wording: {retrieval_query}
Schema: {schema}
Ordered primary key: {primary_keys}
{expected_count}
Caller context: {context}
Request scope: {request_scope}
Column filters: {column_filters}
Output:
"""


def split_filters(
    constraints: Mapping[str, Constraint],
    attrs: Sequence[Attr],
    partition_keys: Collection[str] = (),
) -> tuple[dict[str, Constraint], dict[str, Constraint]]:
    """The column filters, keyed by the attribute they name, and the request scope.

    A constraint is a column filter only when partitioning introduced it, which
    *partition_keys* lists, and it names one schema attribute (see
    :func:`~blue_data_llm_source.types.resolve_attr`). Everything else is request scope:
    every constraint the analysis step or the caller set for the whole request, even one whose
    key matches a column, and a split on a key that names no column, such as a decade.

    Origin decides rather than the name alone, because the analysis step records what a
    request is about as constraints and some share a column's name without its meaning: a
    ``country`` the films were screened in beside a ``country`` column holding the
    filmmaker's nationality. Presented as a filter every record must satisfy, a model told to
    return only matching records applies it literally and drops valid records.
    """
    introduced = set(partition_keys)
    columns: dict[str, Constraint] = {}
    scope: dict[str, Constraint] = {}
    for name, value in constraints.items():
        attr = resolve_attr(name, attrs) if name in introduced else None
        if attr is None or attr.name in columns:
            scope[name] = value
        else:
            columns[attr.name] = value
    return columns, scope


def render_expected_count(estimate: object) -> list[str]:
    """The row estimate as one sentence, or nothing when it gives no count.

    A sentence rather than the estimate object, because the object reads as data to match: a
    model handed ``{"expected": 30}`` has a number to stop at, and one handed nothing tends to
    stop at a comfortable sample. The sentence gives the scale and says in the same breath that
    it is not a limit. Only the counts and the row unit are used; the estimate's provenance
    fields are bookkeeping for the planner and tell the model nothing.
    """
    if not isinstance(estimate, Mapping):
        return []
    fields = cast("Mapping[str, object]", estimate)

    def count(name: str) -> int | None:
        value = fields.get(name)
        return value if isinstance(value, int) and not isinstance(value, bool) else None

    expected, lower, upper = count("expected"), count("lower"), count("upper")
    if expected is not None:
        scale = f"about {expected}"
        if lower is not None and upper is not None:
            scale += f" (plausibly {lower} to {upper})"
    elif lower is not None and upper is not None:
        scale = f"between {lower} and {upper}"
    elif lower is not None:
        scale = f"at least {lower}"
    else:
        # An upper bound alone reads as a ceiling, which is the opposite of what this line is
        # for, so it is not stated on its own.
        return []
    unit = fields.get("row_unit")
    # Encoded: the row unit can come from a model, and this is an instruction line.
    counted = f" (unit: {_json(unit)})" if isinstance(unit, str) and unit.strip() else ""
    return [
        f"Expected count: {scale} matching records{counted}. This is a rough estimate, not a "
        "limit; return every matching record even if there are more."
    ]


def _filter_payload(constraints: Mapping[str, Constraint]) -> dict[str, object]:
    """The filters as tagged JSON objects, the one form both leaf contracts send.

    A range is written with ``min`` and ``max`` rather than the wire's ``lo`` and ``hi``,
    because those two words say to a reader that both ends are included.
    """
    return {
        name: (
            {"kind": "one_of", "values": list(value.values)}
            if isinstance(value, OneOf)
            else {"kind": "range", "min": value.lo, "max": value.hi}
            if isinstance(value, Range)
            else {"kind": "exact", "value": value.value}
        )
        for name, value in constraints.items()
    }


def build_leaf_fetch_prompt(
    *,
    original_query: str,
    retrieval_query: str,
    attrs: Sequence[Attr],
    caller_context: str,
    constraints: Mapping[str, Constraint],
    primary_keys: Sequence[str] = (),
    estimate: object = None,
    prompt_version: str = KEYED_LEAF_FETCH_PROMPT_VERSION,
    partition_keys: Collection[str] = (),
) -> str:
    """Build the versioned leaf prompt used by planning and by non-flat execution.

    *prompt_version* selects the contract. Both stay buildable, so a task that names the older
    one is answered with the text it was planned against. *partition_keys* names the
    constraints partitioning introduced; only ``keyed-leaf-fetch-2`` reads it, to tell column
    filters from request scope.
    """
    schema = [{"name": attr.name, "type": attr.type} for attr in attrs]
    filters = _filter_payload(constraints)
    if prompt_version == COMPLETE_LEAF_FETCH_PROMPT_VERSION:
        columns, scope = split_filters(constraints, attrs, partition_keys)
        return fill(
            COMPLETE_LEAF_FETCH,
            prompt_version=Slot.encoded(COMPLETE_LEAF_FETCH_PROMPT_VERSION),
            original_query=Slot.encoded(original_query),
            retrieval_query=Slot.encoded(retrieval_query),
            schema=Slot.encoded(schema),
            primary_keys=Slot.encoded(list(primary_keys)),
            expected_count=Slot.lines(render_expected_count(estimate)),
            context=Slot.encoded(caller_context),
            request_scope=Slot.encoded(_filter_payload(scope)),
            column_filters=Slot.encoded(_filter_payload(columns)),
        )
    if prompt_version != KEYED_LEAF_FETCH_PROMPT_VERSION:
        raise ConfigError(f"unknown leaf prompt contract {prompt_version!r}")
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

Inferred types may be string, integer, number, boolean, date, or null. Declared attributes
and types must remain unchanged, including aliases int, float, bool, str, and text.
Caller-supplied primary keys must remain unchanged. When none were supplied, infer fields
from the schema and request that jointly identify one record, only when defensible.
An empty declared primary_keys list means no key was supplied; it does not disable inference.
Schema mode is {schema_mode}. Return only the JSON object.

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
Caller-supplied primary keys must remain unchanged. When none were supplied, infer fields
from the schema and request that jointly identify one record, only when defensible.
An empty declared primary_keys list means no key was supplied; it does not disable inference.
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

The values together must cover every record matching this slice and preserve its filters.
Choose categories or periods, rather than record identifiers or identity-field values.
Do not sample or truncate a domain to fit the value limit. If no useful complete split
fits, return an empty values list so the parent slice can run intact.

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


GUARDED_PARTITION = """\
Propose one useful partition for this data slice. Do not answer the request.

Return one JSON object with exactly these keys:
- "key": one output or latent dimension
- "values": two or more distinct non-empty strings, at most {max_partitions}
- "ambiguity": a number from 0 to 1
- "child_estimates": a JSON array with one non-negative integer or null per value

Required container shape:
{{"key":"...","values":["..."],"ambiguity":0,"child_estimates":[1]}}
The square-bracket arrays are required. Do not return an object keyed by value.

Rules for the key and its values:
- The values together must cover every record matching this slice and preserve its filters.
  Do not sample or truncate a domain to fit the value limit. If no useful complete split
  fits, return an empty values list so the parent slice can run intact.
- Choose a key that sorts records into categories or periods. Do not choose an identifier,
  an identity-field value, a code, a name unique to each record, or a continuous measurement
  such as an amount, a count, a price, a score, or a size.
- Every value must be a real, specific value of the key. Do not use placeholders such as
  "...", "etc.", "value 1", or "TBD".
- Do not use catch-all values such as "Other", "Unknown", "Miscellaneous", or "All".
- {value_count}

Do not repeat an ancestor key. If a preferred key is given, use it or return an empty
values list. Return only the JSON object.

Prompt contract: {prompt_version}

Original request: {original_query}
Retrieval wording: {retrieval_query}
Caller context: {context}
Schema: {schema}
Current filters: {constraints}
Ancestor keys: {ancestor_keys}
Preferred key: {preferred_key}
Output:
"""


def render_value_count(
    parent_expected: int | None, target_rows_per_leaf: int, max_partitions: int
) -> str:
    """How many values to propose, calibrated against the slice's own estimate.

    Without a number a model tends to propose a familiar list whatever the slice holds: ten
    values for a slice of twelve records, most of them empty, or three for one of a thousand,
    each still too large to answer in one response. The suggested count is the estimate
    divided by the per-leaf target, kept within the two-to-``max_partitions`` a proposal may
    have. It is guidance, and the validation after the call still decides.
    """
    target = max(1, target_rows_per_leaf)
    if parent_expected is None or isinstance(parent_expected, bool) or parent_expected < 0:
        return (
            f"Use as few values as keep each one to roughly {target} records or fewer, and "
            "no more values than there are records."
        )
    suggested = min(max(2, -(-parent_expected // target)), max(2, max_partitions))
    return (
        f"This slice is expected to hold about {parent_expected} records. Propose about "
        f"{suggested} values, so each holds roughly {target} records or fewer, and never more "
        "values than there are records."
    )


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
    prompt_version: str = PARTITION_PROMPT_VERSION,
    parent_expected: int | None = None,
    target_rows_per_leaf: int = 50,
) -> str:
    """Build one slice's partition proposal prompt.

    *prompt_version* selects the contract; the last two parameters are read only by
    ``partition-2``, whose value-count rule is calibrated against them.
    """
    if prompt_version == GUARDED_PARTITION_PROMPT_VERSION:
        return fill(
            GUARDED_PARTITION,
            max_partitions=Slot.encoded(max_partitions),
            value_count=Slot.lines(
                [render_value_count(parent_expected, target_rows_per_leaf, max_partitions)]
            ),
            prompt_version=Slot.encoded(GUARDED_PARTITION_PROMPT_VERSION),
            original_query=Slot.encoded(original_query),
            retrieval_query=Slot.encoded(retrieval_query),
            context=Slot.encoded(caller_context),
            schema=Slot.encoded(schema),
            constraints=Slot.encoded(constraints),
            ancestor_keys=Slot.encoded(list(ancestor_keys)),
            preferred_key=Slot.encoded(preferred_key),
        )
    if prompt_version != PARTITION_PROMPT_VERSION:
        raise ConfigError(f"unknown partition prompt contract {prompt_version!r}")
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
