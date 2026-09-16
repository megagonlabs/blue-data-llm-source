"""The contract test that covers what inheritance does not.

``LlmSource`` inherits ``DataSource``, so most divergence from the upstream contract is a
type error. Two kinds are not.

A signature can drift while still type-checking, because the base methods are largely
untyped: an argument renamed or a default moved upstream leaves a subclass that satisfies
the checker and breaks a caller passing that argument by name. And most ``DataSource``
methods return an empty result, so one resolving to the base rather than to this class
answers nothing instead of failing — the quietest way a source can be
broken. These tests compare signatures against the real base class and assert resolution.

``blue-data`` is a core dependency, so there is nothing to skip. Were it absent the suite
would fail at collection, because the source module imports it unconditionally.
"""

from __future__ import annotations

import inspect
import re
import types
from collections.abc import Callable
from typing import Union, get_args, get_origin

import pytest
from blue_data.source.source import DataSource  # pyright: ignore[reportMissingTypeStubs]

from blue_data_llm_source import LlmSource

# Methods a registry or a planner may call on any source. If upstream adds one, this list
# is what needs updating, and the test below says so.
CONTRACT = [
    "execute_query",
    "execute_query_nl",
    "get_nl_tools",
    "execute_nl_tool",
    "execute_query_nl_tools",
    "translate_nl_to_native_query",
    "fetch_metadata",
    "fetch_schema",
    "fetch_databases",
    "fetch_database_metadata",
    "fetch_database_schema",
    "fetch_database_collections",
    "fetch_database_collection_metadata",
    "fetch_database_collection_entities",
    "fetch_database_collection_relations",
    "fetch_source_stats",
    "fetch_database_stats",
    "fetch_collection_stats",
    "fetch_entity_stats",
    "fetch_property_stats",
    "create_database",
    "create_database_collection",
    "create_database_collection_entity",
    "create_database_collection_relation",
    "import_data",
]


def test_report_which_blue_data_is_being_checked() -> None:
    """Surface the provenance of the blue-data being compared against.

    A contract test is only as good as the copy it checks. An installed blue-data can
    be older than a local checkout, in which case this suite would happily confirm us
    against a version nobody runs. This test always passes; it exists so the path is in
    the output and a stale install is diagnosable in one look rather than three.
    """
    import blue_data  # pyright: ignore[reportMissingTypeStubs]

    print(f"\ncontract checked against: {blue_data.__file__}")


def _erase(annotation: object) -> str | None:
    """Render an annotation for comparison, with generic parameters erased.

    ``dict[str, Any]`` and ``dict`` compare equal, and so do ``list[dict[str, Any]]``
    and ``list[dict]``. Being more precise than upstream is a refinement a caller cannot
    trip over. Changing ``str`` to ``int`` is drift a caller would, and that still fails.
    """
    if annotation is inspect.Signature.empty:
        return None
    origin = get_origin(annotation)
    if origin is types.UnionType or origin is Union:
        members: list[str] = []
        for member in get_args(annotation):
            erased = "None" if member is type(None) else _erase(member)
            if erased is None:
                raise AssertionError(f"union member has no annotation: {member!r}")
            members.append(erased)
        return "|".join(members)
    if origin is not None:
        annotation = origin
    text = (
        annotation
        if isinstance(annotation, str)
        else getattr(annotation, "__name__", str(annotation))
    )
    text = str(text).replace("'", "").replace('"', "").replace(" ", "")
    # Erase every [...] group, innermost first.
    while "[" in text:
        new = re.sub(r"\[[^\[\]]*\]", "", text)
        if new == text:
            break
        text = new
    return text


@pytest.mark.parametrize(
    ("annotation", "expected"),
    [
        (str | None, "str|None"),
        (dict[str, object], "dict"),
        (list[dict[str, object]], "list"),
    ],
)
def test_annotation_erasure_is_interpreter_stable(annotation: object, expected: str) -> None:
    assert _erase(annotation) == expected


# Places where this source is deliberately more or less specific than upstream, each with
# the reason. Anything not listed here must match after generic erasure.
INTENDED_VARIANCE = {
    ("execute_query", "query"): (
        "Any",
        "upstream leaves query unannotated; here it is Any because this source accepts "
        "either a natural-language string or an op dict",
    ),
    ("execute_query_nl", "llm_fn"): (
        "Any",
        "upstream leaves llm_fn unannotated; here it is Any because any callable or "
        "client-shaped object is accepted",
    ),
    ("translate_nl_to_native_query", "llm_fn"): ("Any", "same as execute_query_nl"),
    ("execute_nl_tool", "->"): (
        "Any",
        "upstream leaves the return unannotated because each tool returns its own shape, "
        "and the four served here return three different ones",
    ),
    ("execute_query_nl_tools", "llm_fn"): ("Any", "same as execute_query_nl"),
    ("execute_query_nl_tools", "extra_tool_fn"): (
        "Any",
        "upstream leaves it unannotated; this source refuses this method, so the annotation "
        "exists to satisfy strict typing rather than to describe a contract",
    ),
    ("translate_nl_to_native_query", "->"): (
        "str|None",
        "upstream leaves the return unannotated because each source returns its own "
        "dialect; this source's native dialect is natural language, so it is str | None",
    ),
    ("fetch_collection_stats", "entities"): (
        "Any",
        "upstream leaves it unannotated; annotated Any here rather than guessing a shape "
        "this source never inspects",
    ),
    ("fetch_collection_stats", "relations"): ("Any", "same as entities"),
    ("import_data", "source"): (
        "Any",
        "upstream annotates the concrete DataSource; kept Any here so this method accepts "
        "any source-shaped object rather than only that class",
    ),
}
"""Each entry is the annotation this source is expected to carry, and why it differs.

The expected value is asserted, not just skipped. Storing only a reason exempted the
position from every check, so the local annotation could drift to anything — `source: Any`
to `source: int` passed the real contract test — and the guard stayed green."""


def _params(func: Callable[..., object]) -> list[tuple[str, object, object]]:
    """Name, kind, and default for every parameter. The breaking-change surface."""
    signature = inspect.signature(func)
    return [
        (name, p.kind, p.default)
        for name, p in signature.parameters.items()
        if name not in {"self", "cls"}
    ]


def _annotations(func: Callable[..., object]) -> dict[str, str | None]:
    signature = inspect.signature(func)
    out = {
        name: _erase(p.annotation)
        for name, p in signature.parameters.items()
        if name not in {"self", "cls"}
    }
    out["->"] = _erase(signature.return_annotation)
    return out


@pytest.mark.parametrize("method", CONTRACT)
def test_sync_and_async_match_blue_data(method: str) -> None:
    """A method that became a coroutine is a breaking change a signature cannot show.

    ``inspect.signature`` is identical for a function and its async twin, so an in-memory
    async replacement for a sync contract method passed every other check here.
    """
    ours = getattr(LlmSource, method, None)
    upstream = getattr(DataSource, method, None)
    assert ours is not None and upstream is not None, method
    assert inspect.iscoroutinefunction(ours) == inspect.iscoroutinefunction(upstream), (
        f"{method} is {'async' if inspect.iscoroutinefunction(ours) else 'sync'} here and "
        f"{'async' if inspect.iscoroutinefunction(upstream) else 'sync'} in blue-data. "
        "A caller awaiting one and calling the other breaks either way."
    )


@pytest.mark.parametrize("method", CONTRACT)
def test_signature_matches_blue_data(method: str) -> None:
    upstream = getattr(DataSource, method, None)
    if upstream is None:
        pytest.skip(f"blue-data has no {method}; nothing to match")
    ours = getattr(LlmSource, method, None)
    assert ours is not None, (
        f"blue-data's DataSource defines {method} and this source does not. "
        "Shape matching means every method a caller may reach must exist here."
    )
    import blue_data  # pyright: ignore[reportMissingTypeStubs]

    mine, theirs = _annotations(ours), _annotations(upstream)
    for name, upstream_type in theirs.items():
        if (method, name) in INTENDED_VARIANCE:
            # Assert the local side too. Skipping the position outright exempted it from
            # every check, so this source's own annotation could drift to anything —
            # `source: Any` to `source: int` passed — and the guard stayed green.
            expected, reason = INTENDED_VARIANCE[(method, name)]
            assert mine.get(name) == expected, (
                f"{method} annotation for {name!r} is an intended variance from blue-data, "
                f"but the local side has drifted from what that variance claims.\n"
                f"  expected here: {expected}\n"
                f"  found here:    {mine.get(name)}\n"
                f"  the variance:  {reason}"
            )
            continue
        assert mine.get(name) == upstream_type, (
            f"{method} annotation for {name!r} has drifted from blue-data.\n"
            f"  blue-data: {upstream_type}\n"
            f"  here:      {mine.get(name)}\n"
            "Generic parameters are erased before comparing, so this is a real type "
            "change rather than a precision difference. Either match it, or add an "
            "entry to INTENDED_VARIANCE saying why the difference is deliberate."
        )

    assert _params(ours) == _params(upstream), (
        f"{method} signature has drifted from blue-data.\n"
        f"  blue-data: {_params(upstream)}\n"
        f"  here:      {_params(ours)}\n"
        f"  compared against: {blue_data.__file__}\n"
        "If that path is an installed copy rather than a checkout, confirm it is current "
        "before changing this source to match it."
    )


# Methods `blue-data`'s DataSource has that this source deliberately does not override, each
# with the reason. Being listed here is a claim that inheriting the base behaviour is correct,
# not that the method was overlooked.
OUT_OF_SCOPE = {
    "begin_transaction": "upstream refuses it by name; nothing here is transactional",
    "commit_transaction": "as begin_transaction",
    "rollback_transaction": "as begin_transaction",
    "transaction": "the context-manager form of the same refusal",
    "list_transactions": "there are none to list",
    "get_transaction_operations": "there are none to report",
}


def test_out_of_scope_methods_still_exist_upstream() -> None:
    """A recorded exemption for a method upstream has dropped is a stale claim.

    Without this the list only ever grows, and an exemption outlives the thing it exempts.
    """
    gone = [name for name in OUT_OF_SCOPE if not hasattr(DataSource, name)]
    assert not gone, f"exempted but no longer upstream, so the entry should go: {sorted(gone)}"


def test_the_transactional_surface_refuses_rather_than_returning_empty() -> None:
    """The reason the exemptions above are safe, asserted rather than assumed.

    An inherited method that quietly returned an empty result would let a caller believe a
    transaction had begun. Upstream refuses instead, and names this source when it does.
    """
    source = LlmSource()
    with pytest.raises(NotImplementedError):
        source.begin_transaction("db")
    with pytest.raises(NotImplementedError):
        source.list_transactions("db")


def test_no_upstream_method_is_missing() -> None:
    """Catch a method added upstream that CONTRACT does not yet list."""
    public = {
        name
        for name, _ in inspect.getmembers(DataSource, inspect.isfunction)
        if not name.startswith("_")
    }
    unknown = public - set(CONTRACT) - set(OUT_OF_SCOPE)
    assert not unknown, (
        f"blue-data's DataSource has public methods this contract does not cover: "
        f"{sorted(unknown)}. Decide whether each is part of the surface this source "
        "must present, then add it to CONTRACT or record why it is out of scope in "
        "OUT_OF_SCOPE above. This is a product decision, not a test to silence: the "
        "inherited implementation is what a caller gets until someone overrides it."
    )


def test_the_alias_is_the_source_itself() -> None:
    """The older adapter name, kept so an existing import keeps working.

    It was a subclass when blue-data was optional. There is nothing left for a subclass to
    add, and a second class would be a second method resolution order to get wrong.
    """
    from blue_data_llm_source.compat.blue_data import BlueDataLlmSource

    assert BlueDataLlmSource is LlmSource


def test_every_contract_method_resolves_to_this_class() -> None:
    """The base class answers empty, so an unoverridden method is a silent no-answer."""
    unresolved = [
        method
        for method in CONTRACT
        if hasattr(LlmSource, method)
        and getattr(LlmSource, method) is getattr(DataSource, method, None)
    ]
    assert unresolved == [], (
        f"{unresolved} resolve to DataSource, whose implementations return empty results, "
        "so these would answer nothing rather than fail."
    )
