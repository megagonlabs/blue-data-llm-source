"""``llm fetch``: what each option does to the call and to the output."""

from __future__ import annotations

import json
from typing import Any

import pytest
from blue_data_llm_source import BudgetExceeded, LlmSource
from click.testing import CliRunner

from blue_data_llm_cli import commands
from blue_data_llm_cli.commands import cli

QUERY = "the member states of the European Union"

# Keys the model chose for itself: one that maps onto a requested attribute after
# normalization, one that maps onto nothing.
MESSY_RECORDS = json.dumps(
    [
        {"Country": "France", "capital": "Paris"},
        {"Country": "Germany", "capital": "Berlin"},
    ]
)


def _json(output: str) -> dict[str, Any]:
    return json.loads(output)


def test_fetch_prints_a_table_and_a_footer(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["fetch", QUERY])
    assert result.exit_code == 0, result.output
    assert "France" in result.stdout
    assert "Germany" in result.stdout
    assert "country" in result.stdout
    assert "2 records" in result.stdout
    assert "model fake-model" in result.stdout
    assert "1 call" in result.stdout
    assert "46 tokens" in result.stdout
    assert "$0.0012" in result.stdout


def test_fetch_without_attrs_keeps_the_keys_the_model_returned(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["fetch", QUERY, "--format", "json"])
    assert result.exit_code == 0, result.output
    records = _json(result.stdout)["records"]
    assert records[0] == {"country": "France", "accession_year": "1958"}


def test_fetch_with_attr_names_only_selects_those_columns(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["fetch", QUERY, "--attr", "country"])
    assert result.exit_code == 0, result.output
    assert "France" in result.stdout
    assert "accession_year" not in result.stdout


def test_fetch_with_attr_types_coerces_values(runner: CliRunner) -> None:
    result = runner.invoke(
        cli,
        [
            "fetch",
            QUERY,
            "--attr",
            "country",
            "--attr",
            "accession_year:int",
            "--format",
            "json",
        ],
    )
    assert result.exit_code == 0, result.output
    records = _json(result.stdout)["records"]
    assert records[0] == {"country": "France", "accession_year": 1958}


def test_fetch_passes_context_to_the_model(runner: CliRunner, default_client: Any) -> None:
    result = runner.invoke(cli, ["fetch", QUERY, "--context", "rank by surface area"])
    assert result.exit_code == 0, result.output
    assert "rank by surface area" in default_client.prompts[0]


def test_fetch_without_context_sends_none(runner: CliRunner, default_client: Any) -> None:
    result = runner.invoke(cli, ["fetch", QUERY])
    assert result.exit_code == 0, result.output
    assert "(none)" in default_client.prompts[0]


def test_fetch_max_records_caps_the_result(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["fetch", QUERY, "--max-records", "1", "--format", "json"])
    assert result.exit_code == 0, result.output
    assert len(_json(result.stdout)["records"]) == 1


def test_fetch_json_output_carries_records_and_meta(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["fetch", QUERY, "--format", "json"])
    assert result.exit_code == 0, result.output
    payload = _json(result.stdout)
    assert set(payload) == {"records", "meta"}
    assert payload["meta"]["calls"] == 1
    assert payload["meta"]["prompt_tokens"] == 12
    assert payload["meta"]["completion_tokens"] == 34
    assert payload["meta"]["cost"] == 0.0012
    assert payload["meta"]["model"] == "fake-model"
    assert "elapsed_seconds" in payload["meta"]


def test_fetch_table_and_json_agree_on_the_records(runner: CliRunner) -> None:
    table = runner.invoke(cli, ["fetch", QUERY, "--attr", "country"])
    as_json = runner.invoke(cli, ["fetch", QUERY, "--attr", "country", "--format", "json"])
    names = [record["country"] for record in _json(as_json.stdout)["records"]]
    assert names == ["France", "Germany"]
    for name in names:
        assert name in table.stdout


def test_model_and_limits_reach_the_call(runner: CliRunner, default_client: Any) -> None:
    result = runner.invoke(
        cli,
        [
            "fetch",
            QUERY,
            "--model",
            "gpt-4o",
            "--limit-cost",
            "0.50",
            "--limit-seconds",
            "30",
            "--limit-calls",
            "2",
        ],
    )
    assert result.exit_code == 0, result.output
    sent = default_client.properties[0]
    assert sent["llm.model"] == "gpt-4o"
    assert sent["limits.max_cost"] == 0.5
    assert sent["limits.max_seconds"] == 30.0
    assert sent["limits.max_calls"] == 2
    assert "model gpt-4o" in result.stdout


def test_a_budget_stop_returns_no_records_and_names_the_limit(
    runner: CliRunner, install_client: Any
) -> None:
    install_client(BudgetExceeded("call limit reached (2)", limit="max_calls"))
    result = runner.invoke(cli, ["--verbose", "fetch", QUERY, "--limit-calls", "2"])
    # Exit 7, the code the interface table has always documented for a budget stop. It was
    # unreachable until now: the library turns a crossed ceiling into an empty result rather
    # than raising, so no BudgetExceeded ever reached the error handler and a run that blew
    # its ceiling exited 0 with nothing to see unless --verbose was asked for.
    assert result.exit_code == 7, result.output
    assert "0 records" in result.stdout
    assert "stopped by: max_calls" in result.stdout


def test_a_row_cap_is_not_a_failure(runner: CliRunner, install_client: Any) -> None:
    """The row cap is the one ceiling that leaves a usable result, so it stays exit 0.

    Exiting non-zero for it would make `--max-records` report failure every time it did
    exactly what was asked of it.
    """
    install_client(MESSY_RECORDS)
    result = runner.invoke(cli, ["--verbose", "fetch", QUERY, "--max-records", "1"])
    assert result.exit_code == 0, result.output
    assert "stopped by: max_rows" in result.stdout


def test_verbose_reports_unmapped_remapped_and_stop_reason(
    runner: CliRunner, install_client: Any
) -> None:
    install_client(MESSY_RECORDS)
    result = runner.invoke(
        cli, ["--verbose", "fetch", QUERY, "--attr", "country", "--max-records", "1"]
    )
    assert result.exit_code == 0, result.output
    assert "unmapped keys: capital" in result.stdout
    assert "remapped keys: Country -> country" in result.stdout
    assert "stopped by: max_rows" in result.stdout


def test_without_verbose_those_three_stay_out(runner: CliRunner, install_client: Any) -> None:
    install_client(MESSY_RECORDS)
    result = runner.invoke(cli, ["fetch", QUERY, "--attr", "country", "--max-records", "1"])
    assert result.exit_code == 0, result.output
    assert "unmapped keys" not in result.stdout
    assert "remapped keys" not in result.stdout
    assert "stopped by" not in result.stdout


def test_verbose_json_adds_the_same_three(runner: CliRunner, install_client: Any) -> None:
    install_client(MESSY_RECORDS)
    result = runner.invoke(
        cli,
        [
            "--verbose",
            "fetch",
            QUERY,
            "--attr",
            "country",
            "--max-records",
            "1",
            "--format",
            "json",
        ],
    )
    assert result.exit_code == 0, result.output
    meta = _json(result.stdout)["meta"]
    assert meta["unmapped_keys"] == ["capital"]
    assert meta["remapped_keys"] == {"Country": "country"}
    assert meta["stopped_by"] == "max_rows"


def test_empty_result_says_so(runner: CliRunner, install_client: Any) -> None:
    install_client("[]")
    result = runner.invoke(cli, ["fetch", QUERY])
    assert result.exit_code == 0, result.output
    assert "(no records)" in result.stdout
    assert "0 records" in result.stdout


def test_values_that_are_not_strings_still_render(runner: CliRunner, install_client: Any) -> None:
    install_client(json.dumps([{"country": "France", "cities": ["Paris"], "founding": True}]))
    result = runner.invoke(cli, ["fetch", QUERY])
    assert result.exit_code == 0, result.output
    assert "Paris" in result.stdout
    assert "True" in result.stdout


def test_the_withdrawn_rate_option_is_refused_with_an_alternative() -> None:
    """Declared so the refusal can be useful, not because the option works.

    Left undeclared it produced click's generic "no such option", which tells a caller the
    flag is wrong but not that the ceiling they wanted is unavailable in this build, or
    what to reach for instead.
    """
    for value in ("10", "fast", ""):
        result = CliRunner().invoke(cli, ["fetch", QUERY, "--rate-limit", value])

        assert result.exit_code != 0, value
        assert "--rate-limit" in result.output, value
        # Typed as a float, click rejected a non-numeric value before the handler ran and
        # answered "not a valid float" — true, and not what the caller needed to know.
        assert "v2.0" in result.output, value
        assert "max_parallel" in result.output, value


def test_max_tokens_lowers_the_output_allowance_the_cost_floor_rests_on() -> None:
    """The remedy both READMEs give for a cost floor that is too high.

    They said to lower the output allowance rather than raise the ceiling, and the CLI had
    no option that did it: the documented fix was library-only while the sentence sat in the
    CLI's own guide.
    """
    seen: dict[str, Any] = {}

    class Recording:
        def complete(self, prompt: str, properties: dict[str, Any] | None = None) -> str:
            seen.update(properties or {})
            return "[]"

        async def acomplete(self, prompt: str, properties: dict[str, Any] | None = None) -> str:
            return self.complete(prompt, properties)

    from blue_data_llm_cli import config

    original = config.build_source
    config.build_source = lambda: LlmSource(client=Recording())  # pyright: ignore[reportAttributeAccessIssue]
    try:
        result = CliRunner().invoke(cli, ["fetch", QUERY, "--max-tokens", "128"])
    finally:
        config.build_source = original  # pyright: ignore[reportAttributeAccessIssue]

    assert result.exit_code == 0, result.output
    assert seen.get("llm.max_tokens") == 128


@pytest.mark.parametrize("value", ["0", "-1", "notanumber"])
def test_max_tokens_refuses_an_allowance_a_provider_could_not_use(value: str) -> None:
    """Validated locally rather than sent onward.

    Only the pricing path checks the allowance, and it does not run when no cost ceiling is
    configured, so zero and negative values reached the client untouched.
    """
    result = CliRunner().invoke(cli, ["fetch", QUERY, "--max-tokens", value])
    assert result.exit_code != 0, value
    assert "--max-tokens" in result.output


def test_a_callable_result_marks_its_accounting_unknown_in_both_formats() -> None:
    """The zero this prints must not read as a measured zero.

    A callable may fan out to several providers and spend real money, none of which is
    visible from here, so the library reports its accounting as unknown. Both output formats
    have to carry that through or the CLI re-asserts the confident zero the library withdrew.
    """
    from blue_data_llm_source.types import ExecMeta

    from blue_data_llm_cli import config

    unknown = ExecMeta(calls=1, cost=0.0, accounting_known=False)
    measured = ExecMeta(calls=1, cost=0.25, accounting_known=True)

    assert config.meta_summary(unknown)["accounting_known"] is False
    assert "accounting_known" not in config.meta_summary(measured)

    assert "(unknown)" in commands._footer(1, unknown)  # pyright: ignore[reportPrivateUsage]
    assert "(unknown)" not in commands._footer(1, measured)  # pyright: ignore[reportPrivateUsage]


def test_model_text_cannot_drive_the_terminal() -> None:
    """A record value is data, and the terminal must not execute it.

    Rich's `strip_control_codes` leaves ESC in place, and `markup=False` defends only
    against its own `[bold]` syntax — so a CSI sequence in a model's answer reached the
    terminal and was obeyed. A value could walk the cursor back and erase rows already
    drawn, leaving a screen showing one record under a footer reporting two. The benign case
    needs no adversary: a model quoting a CI log's colour codes corrupts column alignment,
    because rich counts the escape bytes toward the cell width.
    """
    _cell = commands._cell  # pyright: ignore[reportPrivateUsage]  # the renderer under test
    _printable = commands._printable  # pyright: ignore[reportPrivateUsage]

    assert "\x1b" not in _cell("France\x1b[2K\x1b[1A gone")
    assert _cell("France\x1b[2K gone") == "France[2K gone", "only the control byte goes"
    # Tab survives: it is the one control character a table can render.
    assert _cell("a\tb") == "a\tb"
    # Applies to values rich would otherwise render structurally, too.
    assert "\x1b" not in _cell({"k": "v\x1b[31m"})
    assert "\x1b" not in _printable("\x9b31m")  # the 8-bit CSI form
