"""Turn a model's text response into records."""

from __future__ import annotations

import json
import re
from typing import Any, Final, cast

from blue_data_llm_source.types import ParseError

# Anchored fence stripping. Deleting the substring "json" wherever it appears would take
# it out of any data value carrying it too, so this matches an actual surrounding fence and
# leaves everything between the markers alone.
_FENCE = re.compile(r"^\s*```[a-zA-Z0-9_-]*\s*\n(.*?)\n?\s*```\s*$", re.DOTALL)


def strip_fence(text: str) -> str:
    """Remove one surrounding code fence, if present."""
    match = _FENCE.match(text)
    return match.group(1) if match else text.strip()


# A fence whose closing marker never arrived, which is what a response cut off at the
# output ceiling leaves behind. `_FENCE` requires both markers and so matches nothing here,
# and the leftover ```` ```json ```` line would sit in front of the document being salvaged.
_OPENING_FENCE = re.compile(r"^\s*```[a-zA-Z0-9_-]*\s*\n")


def _unfenced(text: str) -> str:
    """The document inside a fence, whether or not the fence was ever closed."""
    return _OPENING_FENCE.sub("", strip_fence(text), count=1)


# Deeper than any real answer, and shallow enough that the recursive walks downstream —
# deduplication canonicalises each record structurally — stay well inside the interpreter's
# stack. Without this the parser accepted a response that later died with RecursionError,
# which is not in the documented error hierarchy and arrived after the call was billed.
_MAX_NESTING = 100


def _refuse_deep_nesting(data: Any, text: str | None) -> None:
    """Raise ParseError if *data* nests deeper than the pipeline can safely walk.

    Iterative on purpose: a recursive depth check would hit the limit it exists to report.
    """
    stack: list[tuple[Any, int]] = [(data, 0)]
    while stack:
        node, depth = stack.pop()
        if depth > _MAX_NESTING:
            raise ParseError(f"model response nests deeper than {_MAX_NESTING} levels", raw=text)
        if isinstance(node, dict):
            stack.extend((v, depth + 1) for v in cast("dict[str, Any]", node).values())
        elif isinstance(node, list):
            stack.extend((v, depth + 1) for v in cast("list[Any]", node))


_WRAPPER_KEYS = ("items", "rows", "data", "results")


def _unwrapped(envelope: dict[str, Any], wanted: list[str] | None) -> list[Any] | None:
    """The records inside *envelope*, or None if it is itself a record.

    Both readings are legitimate and the shapes are identical. ``{"items": [...],
    "count": 2}`` is a wrapper around records with a metadata sibling; ``{"country":
    "France", "data": [...]}`` is one record with a list-valued attribute. Nothing about
    their structure separates them — matching on the key alone threw away `country`, and
    requiring a sole key threw away the records under `items`, each silently reporting that
    the model had returned nothing.

    What separates them is the request. When the caller named attributes, the reading that
    answers more of them is the one the caller meant. With nothing requested there is no
    evidence either way, and the record reading is chosen because it discards nothing.
    """
    candidates: list[tuple[str, list[Any]]] = [
        (key, cast("list[Any]", value))
        for key, value in envelope.items()
        if key in _WRAPPER_KEYS and isinstance(value, list)
    ]
    if len(candidates) != 1:
        # No wrapper key, or two of them and no way to choose between them.
        return None
    key, inner = candidates[0]
    if len(envelope) == 1:
        return inner
    if not wanted:
        return None

    from blue_data_llm_source.reduction.aligner import normalized_key

    asked = {normalized_key(name) for name in wanted}
    outer = {normalized_key(name) for name in envelope if name != key}
    inner_keys: set[str] = set()
    for item in inner:
        if isinstance(item, dict):
            inner_keys |= {normalized_key(name) for name in cast("dict[str, Any]", item)}
    if not inner_keys:
        # A list of scalars answers no attribute, so it cannot be the records.
        return None
    return inner if len(asked & inner_keys) > len(asked & outer) else None


def parse_records(text: str | None, wanted: list[str] | None = None) -> list[dict[str, Any]]:
    """Read a JSON array of objects from *text*, or raise ParseError."""
    # The client boundary is duck-typed: an injected client or an llm_fn can hand back
    # None, so None is accepted here and reported as an empty response rather than
    # becoming an AttributeError inside strip_fence.
    if text is None or not text.strip():
        raise ParseError("model returned an empty response", raw=text)

    body = strip_fence(text)

    def _reject(constant: str) -> Any:
        # json.loads accepts NaN, Infinity and -Infinity by default. They are not JSON,
        # they do not survive a round trip through other languages, and a record
        # carrying one fails later somewhere less obvious than here.
        raise ValueError(f"{constant} is not valid JSON")

    def _finite(number: str) -> float:
        # parse_constant above only sees the bare words NaN, Infinity and -Infinity. A
        # number written in full — 1e309 — is an ordinary JSON literal that overflows to
        # inf in float(), never reaches that hook, and lands in a record. json.dumps then
        # writes it back as `Infinity`, which strict JSON readers reject, so a caller who
        # re-serialises the result produces something they cannot read again.
        value = float(number)
        if value != value or value in (float("inf"), float("-inf")):
            raise ValueError(f"{number} is out of range for a JSON number")
        return value

    def _reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        # Python's decoder keeps the last of two identical keys and leaves no trace that
        # the first existed, which is the opposite of the first-wins-and-report rule
        # alignment applies to keys that merely normalize alike.
        #
        # An earlier version preserved both by renaming the later one with a private
        # marker. That marker was itself legal JSON, reached callers verbatim when no
        # attributes were requested, and marked duplicates nested inside values where
        # alignment never looks. Rejecting is the honest answer: which of two values for
        # one key the model meant is not knowable here, and silently keeping either is a
        # guess the caller cannot see.
        seen: set[str] = set()
        for key, _ in pairs:
            if key in seen:
                raise ValueError(
                    f"the model returned the key {key!r} more than once in one object, "
                    f"so which value it meant cannot be determined"
                )
            seen.add(key)
        return dict(pairs)

    try:
        parsed: Any = json.loads(
            body,
            parse_constant=_reject,
            parse_float=_finite,
            object_pairs_hook=_reject_duplicates,
        )
    except json.JSONDecodeError as e:
        raise ParseError(f"model response is not valid JSON: {e}", raw=text) from e
    except ValueError as e:
        raise ParseError(f"model response is not valid JSON: {e}", raw=text) from e

    # A single object is accepted and wrapped. Models do this when the answer is one
    # record, and rejecting it would fail a correct answer on a formatting detail.
    data: Any = parsed
    if isinstance(parsed, dict):
        envelope = cast("dict[str, Any]", parsed)
        inner = _unwrapped(envelope, wanted)
        data = envelope if inner is None else inner
        if inner is None:
            data = [envelope]

    _refuse_deep_nesting(data, text)

    if not isinstance(data, list):
        raise ParseError(
            f"model response is JSON but not a list of records (got {type(data).__name__})",
            raw=text,
        )

    records: list[dict[str, Any]] = []
    for i, item in enumerate(cast("list[Any]", data)):
        if not isinstance(item, dict):
            raise ParseError(f"record {i} is {type(item).__name__}, expected an object", raw=text)
        records.append(cast("dict[str, Any]", item))
    return records


_OPENERS: Final = {"[": "]", "{": "}"}


def _completed_prefix(body: str) -> str | None:
    """*body* cut back to its last whole record and closed off, or None.

    None means there is nothing to salvage, and it covers three different situations that
    all end the same way: the document closes on its own so it was never truncated, it
    holds no array of records, or the cut landed before the first record finished.

    A character scan rather than a parser. The input is by definition not JSON, so every
    reader in the standard library refuses it, and the one fact needed here — where the
    last complete record ends — is positional. Strings are tracked because a brace inside
    a value is not structure, and an escaped quote does not end the string it sits in;
    without both, a record holding ``{`` or ``\\"`` moves the cut to the wrong place.
    """
    stack: list[str] = []
    array_depth: int | None = None
    last_complete: int | None = None
    closers = ""
    in_string = False
    escaped = False

    for i, ch in enumerate(body):
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch in _OPENERS:
            stack.append(ch)
            if ch == "[" and array_depth is None:
                # The first array is the one holding the records, whether it is the whole
                # document or the value under a wrapper key. Later arrays are values
                # inside a record and closing one does not finish anything.
                array_depth = len(stack)
        elif ch in ("]", "}"):
            if not stack or _OPENERS[stack[-1]] != ch:
                # Mismatched, so the document is malformed rather than cut off. Salvaging
                # from it would return records from a response no reader agrees about.
                return None
            stack.pop()
            if not stack:
                # The document closed. Whatever made it unreadable, it was not truncation,
                # and the error the caller already has is the accurate one.
                return None
            if len(stack) == array_depth:
                last_complete = i + 1
                closers = "".join(_OPENERS[c] for c in reversed(stack))

    if last_complete is None:
        return None
    return body[:last_complete] + closers


def salvage_records(text: str | None, wanted: list[str] | None = None) -> list[dict[str, Any]]:
    """The whole records from a response that stopped mid-document, or raise ParseError.

    A model that reaches its output ceiling stops mid-token. The records it had already
    finished are complete and correct, and the call was billed in full, so rejecting the
    document whole discards rows that are paid for and present — and hands the caller an
    exception instead of the answer it partly has.

    The recovered prefix goes back through :func:`parse_records` rather than being read
    here. Every rule that decides what a record is — the wrapper reading, duplicate keys,
    non-finite numbers, the nesting ceiling — then applies to a salvaged response exactly
    as it does to a whole one. A second reader with its own rules would accept, from a
    truncated response, records the parser beside it rejects.

    Raises rather than returning an empty list when there is nothing to salvage. No records
    and a response that could not be read are different facts, and the caller has to be
    able to keep reporting the second as an error.
    """
    if text is None or not text.strip():
        raise ParseError("model returned an empty response", raw=text)

    prefix = _completed_prefix(_unfenced(text))
    if prefix is None:
        raise ParseError("model response is unreadable and does not look truncated", raw=text)
    return parse_records(prefix, wanted)
