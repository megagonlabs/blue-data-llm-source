"""The option translation, tested without going through Click."""

from __future__ import annotations

import pytest
from blue_data_llm_source import (
    Attr,
    BudgetExceeded,
    ConfigError,
    ExecMeta,
    Limits,
    LlmSourceError,
    ParseError,
    QueryError,
    TransportError,
)

from blue_data_llm_cli import config


def test_parse_attr_name_only() -> None:
    assert config.parse_attr("country") == Attr(name="country", type=None)


def test_parse_attr_name_and_type() -> None:
    assert config.parse_attr("accession_year:int") == Attr(name="accession_year", type="int")


def test_parse_attr_trims_whitespace() -> None:
    assert config.parse_attr(" country : string ") == Attr(name="country", type="string")


def test_parse_attr_without_a_name_is_rejected() -> None:
    with pytest.raises(ValueError, match="no attribute name"):
        config.parse_attr(":int")


def test_build_attrs_keeps_the_order_given() -> None:
    attrs = config.build_attrs(["country", "accession_year:int"])
    assert [a.name for a in attrs] == ["country", "accession_year"]
    assert [a.type for a in attrs] == [None, "int"]


def test_build_attrs_of_nothing_is_empty() -> None:
    assert config.build_attrs([]) == []


def test_build_limits_carries_the_three_exposed_axes() -> None:
    limits = config.build_limits(limit_cost=0.5, limit_seconds=30.0, limit_calls=2)
    assert limits == Limits(max_cost=0.5, max_seconds=30.0, max_calls=2)


def test_limit_properties_only_names_what_was_set() -> None:
    assert config.limit_properties(Limits(max_cost=0.5)) == {"limits.max_cost": 0.5}
    assert config.limit_properties(Limits()) == {}


def test_limit_properties_covers_every_axis() -> None:
    limits = Limits(max_rows=10, max_seconds=30.0, max_cost=0.5, max_calls=2)
    assert config.limit_properties(limits) == {
        "limits.max_rows": 10,
        "limits.max_seconds": 30.0,
        "limits.max_cost": 0.5,
        "limits.max_calls": 2,
    }


def test_build_properties_adds_the_model() -> None:
    properties = config.build_properties("gpt-4o", Limits(max_calls=2))
    assert properties == {"limits.max_calls": 2, "llm.model": "gpt-4o"}


def test_build_properties_without_a_model_is_limits_only() -> None:
    assert config.build_properties(None, Limits()) == {}


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (QueryError("bad"), config.EXIT_QUERY),
        (ConfigError("bad"), config.EXIT_CONFIG),
        (TransportError("bad"), config.EXIT_TRANSPORT),
        (ParseError("bad"), config.EXIT_PARSE),
        (BudgetExceeded("bad", limit="max_cost"), config.EXIT_BUDGET),
        (LlmSourceError("bad"), config.EXIT_ERROR),
    ],
)
def test_exit_code_for(error: LlmSourceError, code: int) -> None:
    assert config.exit_code_for(error) == code


def test_exit_codes_are_distinct() -> None:
    codes = [
        config.EXIT_OK,
        config.EXIT_ERROR,
        config.EXIT_USAGE,
        config.EXIT_QUERY,
        config.EXIT_CONFIG,
        config.EXIT_TRANSPORT,
        config.EXIT_PARSE,
        config.EXIT_BUDGET,
    ]
    assert len(set(codes)) == len(codes)


def test_meta_summary_is_compact_by_default() -> None:
    meta = ExecMeta(model="gpt-4o", calls=1, prompt_tokens=12, completion_tokens=34)
    meta.unmapped_keys = ["capital"]
    meta.stopped_by = "max_rows"
    summary = config.meta_summary(meta)
    assert summary["model"] == "gpt-4o"
    assert summary["calls"] == 1
    assert "unmapped_keys" not in summary
    assert "stopped_by" not in summary


def test_meta_summary_verbose_adds_the_three_extras() -> None:
    meta = ExecMeta()
    meta.unmapped_keys = ["capital"]
    meta.remapped_keys = {"Country": "country"}
    meta.stopped_by = "max_rows"
    summary = config.meta_summary(meta, verbose=True)
    assert summary["unmapped_keys"] == ["capital"]
    assert summary["remapped_keys"] == {"Country": "country"}
    assert summary["stopped_by"] == "max_rows"


def test_meta_summary_verbose_omits_extras_that_are_unset() -> None:
    summary = config.meta_summary(ExecMeta(), verbose=True)
    assert "unmapped_keys" not in summary
    assert "remapped_keys" not in summary
    assert "stopped_by" not in summary


def test_the_published_install_brings_the_provider_transport() -> None:
    """A documented `pip install` must produce a package that can fetch.

    The declared dependency asked for the library without its `llm` extra, so a clean
    resolver had no reason to install the provider transport: the info command worked and
    the first model-backed fetch raised `ConfigError: the llm provider is not installed`.

    Read from the shipped `pyproject.toml` rather than from installed metadata, because an
    editable install in this environment carries whatever it was built with.
    """
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    text = (root / "pyproject.toml").read_text()
    declared = re.findall(r'"(blue-data-llm-source[^"]*)"', text)

    assert declared, "the library is not declared as a dependency at all"
    assert all("[llm]" in d for d in declared), (
        f"the published install does not request the provider extra: {declared}"
    )


def test_an_uncheckable_declared_type_reaches_the_caller() -> None:
    """The library reports it once; both adapters used to throw it away.

    A declared type nothing can check is a fact about the request, not the answer, so it is
    not gated behind --verbose. Discarding it here restored exactly the silence the field was
    added to end: a misspelled type checking nothing while the caller believes otherwise.
    """
    meta = ExecMeta(outcome="ok", unchecked_types=["money"])
    assert config.meta_summary(meta)["unchecked_types"] == ["money"]
    assert config.meta_summary(meta, verbose=True)["unchecked_types"] == ["money"]

    # Absent when there is nothing to say, so an ordinary result is unchanged.
    assert "unchecked_types" not in config.meta_summary(ExecMeta(outcome="ok"))
