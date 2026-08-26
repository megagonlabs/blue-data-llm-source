# blue-data-llm-cli

The `llm` command. It asks a language model a question and prints the answer as records, which is what `blue-data-llm-source` does; this package only parses arguments and prints results.

## Install

```bash
scripts/install.sh
```

That installs `blue-data`, then the library from `../../lib`, then this package, all editable, plus the dev extras. Set `BLUE_DATA_PATH` if `blue-data` does not sit alongside this repository.

`pip install blue-data-llm-cli` works only where `blue-data` is resolvable, which means an internal index: it is a core dependency of the library underneath and is not on the public one.

Both packages point `pyright` at a `.venv` in their own directory, matching the sibling subrepos. Create and activate one before installing, or the type gate reports every import as unresolved instead of reporting real typing errors:

```bash
python -m venv .venv && source .venv/bin/activate
```

## Commands

```bash
llm --help
```

```bash
llm info
```

Prints the protocol, the variant, and the version of the library underneath:

```text
protocol  llm
variant   openai
version   1.1.1
```

```bash
llm fetch --help
```

### fetch

The query is the one required argument.

```bash
llm fetch "the member states of the European Union"
```

Name the attributes you want and you get exactly those columns. `NAME:TYPE` declares a type, and values that can be coerced to it are coerced; `int`, `float`, `bool`, and `date` are understood and converted. `string` (also `str`, `text`) is checked rather than converted, so a declared string that arrives as a number is reported rather than stringified. Any other name is accepted, checks nothing, and is listed once under `unchecked_types`.

```bash
llm fetch "the member states of the European Union" \
    --attr country --attr accession_year:int
```

```text
┏━━━━━━━━━┳━━━━━━━━━━━━━━━━┓
┃ country ┃ accession_year ┃
┡━━━━━━━━━╇━━━━━━━━━━━━━━━━┩
│ France  │ 1958           │
│ Germany │ 1958           │
└─────────┴────────────────┘
2 records | model gpt-4o | 1 call | 46 tokens | $0.0012 | 0.31s
```

`--context` adds text that clarifies the ask, usually what an attribute name means:

```bash
llm fetch "the largest lakes in Africa" --context "rank by surface area, not volume"
```

`--max-records` caps the result:

```bash
llm fetch "the member states of the European Union" --max-records 5
```

`--format json` prints records and metadata as one JSON object:

```bash
llm fetch "the member states of the European Union" --format json
```

```text
{
  "records": [
    {
      "country": "France",
      "accession_year": 1958
    }
  ],
  "meta": {
    "outcome": "ok",
    "model": "gpt-4o",
    "calls": 1,
    "prompt_tokens": 12,
    "completion_tokens": 34,
    "cost": 0.0012,
    "elapsed_seconds": 0.31
  }
}
```

Careful with that pipe. The provider library writes its own coloured banners to standard output on some failure paths, and this package has no hook to suppress them, so a JSON reader on the other end of the pipe can choke on lines the CLI did not print. Redirecting standard error does not help, because the banners are on standard output. Anything scripted should read the accounting through the library rather than out of a pipe.

The model and the spending limits are options, not configuration files. All three are claimed before a call, so a request that would cross one is refused rather than reported afterwards. `--limit-cost` is claimed against an upper bound on what the call can cost rather than an estimate of what it is likely to cost, and that bound is reconciled against the real figure once the call returns. See [The cost bound](../../lib/README.md#the-cost-bound) for how it is calculated and how to size one, and `--max-tokens` below for lowering it. A request refused this way prints no records and exits 7, the code for a run a ceiling ended; `--verbose` names it as `stopped by: max_cost`.

```bash
llm fetch "the member states of the European Union" \
    --model gpt-4o --limit-cost 0.50 --limit-seconds 30 --limit-calls 2
```

`--limit-cost 0.50` is above the floor for that model, which is what makes the command above work. The floor is the whole output allowance at the model's output rate and does not depend on the prompt: for `gpt-4o` with the library's default of 4096 output tokens it is about $0.041, so `--limit-cost 0.01` would refuse every call before making one. Ask for a smaller answer with `--max-tokens` rather than a larger ceiling if the floor is too high, since the output allowance is what the floor is made of. A model this package carries no published price for is refused a cost ceiling outright, with a `ConfigError` naming what is missing and exit code 4, rather than being bounded against a guessed rate.

### --verbose

`--verbose` is global, so it goes before the subcommand. It adds the outcome, which is always printed, plus six things reported only when there is something to say: keys the model returned that no requested attribute matched, keys it renamed on the way in, the limit that stopped the run, values that did not match a declared attribute type, values dropped because two returned keys answered the same attribute, and declared type names this library cannot check.

```bash
llm --verbose fetch "the member states of the European Union" \
    --attr country --max-records 20
```

```text
unmapped keys: capital
remapped keys: Country -> country
outcome: truncated
stopped by: max_rows
```

Every line except `outcome` appears only when there is something to report. Under `--format json` the same keys land in the `meta` object, where `outcome` is present with or without `--verbose`.

Nothing here exposes a cost, time, or call ceiling as a persistent setting: the three `--limit-*` options apply to the one command that carries them, since the CLI builds a fresh source per invocation and reads no configuration file.

That is the same shape the library has: a ceiling passed with a request bounds that request and does not leak into the next.

There is no `llm estimate`. The library can report what a request would cost before running it, and this command does not expose it, so a person sizing a request ahead of time uses the library.

## Exit codes

Every error the library raises is caught and printed to stderr as one line. No traceback reaches the terminal.

| Code | Cause |
|---|---|
| 0 | Success |
| 1 | An `LlmSourceError` outside the five classified kinds |
| 2 | Usage error: unknown option, bad value, missing argument (Click's own code) |
| 3 | `QueryError` — the query or an argument was malformed |
| 4 | `ConfigError` — a setting this package checked and rejected |
| 5 | `TransportError` — the provider failed or answered with something unusable |
| 6 | `ParseError` — the model's answer was not a record list |
| 7 | A ceiling ended the run. `meta.stopped_by` names the axis. A row cap stays 0, since it leaves a usable result |

```text
llm: ParseError: model response is not valid JSON: Expecting value: line 1 column 1
```

A missing `OPENAI_API_KEY` exits 5, not 4. The library never reads a provider credential, so it cannot report one as missing; the provider SDK reports it as a call that failed, which becomes a `TransportError`:

```text
llm: TransportError: litellm.InternalServerError: OpenAIException - Missing credentials.
```

**Exit 2 is Click's, not this package's.** An unknown or syntactically wrong option — a misspelled `--limit-cst`, a `--limit-cost` that is not a number, a `--max-tokens` below one — never reaches the library, so it exits 2 with Click's own message and suggestion.

Code 4 covers a value that parsed and the library then rejected: a negative or non-finite ceiling, an unknown `llm.*` override, a setting deferred to v2.0, a cost ceiling whose bound cannot be established, and a missing optional extra.

A cost, time, or call ceiling that stops a run exits 7. Whatever records were collected are printed first and the run is reported in `stopped_by`, so nothing is hidden, but the code is what a script branches on and a stopped run must not look like a completed one. The row cap is the exception: it is the one ceiling that leaves a usable result, so `--max-records` reports `outcome: truncated` and exits 0.

## Tests

```bash
python3 -m pytest tests/unit -q
python3 -m ruff format --check . && python3 -m ruff check .
```

Every `llm` command shown above is run by `tests/unit/test_readme.py`, which reads this file, extracts the `bash` blocks, and invokes each one against a fake model client. A command that stops working here fails the suite.
