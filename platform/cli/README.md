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
version   1.2.0
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

`--effort` is a short form for the continuation allowance. `low`, `medium`, and `high` allow up to one,
two, or three logical extraction asks per part when a primary key is available. Later asks request
keys the earlier answers missed, so they can improve coverage. The allowance bounds asks, not billed
attempts; transport retries can add provider attempts. If the last allowed ask under `medium` or
`high` still adds new keys, the records gathered so far are printed, the result reports
`stopped by: plan.max_continuations`, and the command exits 7. That means the effort allowance
bounded the result, not that the provider failed. In flat mode, `medium` and `high` require a
requested `--plan-key` or `--plan-keys`; the CLI rejects a missing key before constructing the
source. Non-flat planning can infer a key. The default is `low`. Use
`--plan-continuations` when you need an exact allowance; that option overrides `--effort`.

```bash
llm fetch "the member states of the European Union" \
    --attr country --plan-key country --effort medium
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
    "gaps": [],
    "stop_reasons": [],
    "requested_attrs": ["country", "accession_year"],
    "row_estimate": null,
    "cardinality_estimate": null,
    "primary_keys": [],
    "observed_key_count": 0,
    "marginal_key_yields": [],
    "incomplete_key_records": 0,
    "cost": 0.0012,
    "elapsed_seconds": 0.31
  }
}
```

During a fetch, the CLI redirects interpreter-level writes through `sys.stdout` to standard error.
That covers the known provider banners: on success, normal `--format json` output is one JSON object,
while provider diagnostics and command errors remain visible on standard error. A dependency that
writes directly to file descriptor 1 or writes after the fetch returns is outside this redirect.

The model and the spending limits are options, not configuration files. All three are claimed before a call, so a request that would cross one is refused rather than reported afterwards. `--limit-cost` is claimed against an upper bound on what the call can cost rather than an estimate of what it is likely to cost, and that bound is reconciled against the real figure once the call returns. See [The cost bound](../../lib/README.md#the-cost-bound) for how it is calculated and how to size one, and `--max-tokens` below for lowering it. A request refused this way prints no records and exits 7, the code for a run a ceiling ended; `--verbose` names it as `stopped by: max_cost`.

```bash
llm fetch "the member states of the European Union" \
    --model gpt-4o --limit-cost 0.50 --limit-seconds 30 --limit-calls 2
```

`--limit-cost 0.50` is above the floor for that model, which is what makes the command above work. The floor is the whole output allowance at the model's output rate and does not depend on the prompt: for `gpt-4o` with the library's default of 4096 output tokens it is about $0.041, so `--limit-cost 0.01` would refuse every call before making one. Ask for a smaller answer with `--max-tokens` rather than a larger ceiling if the floor is too high, since the output allowance is what the floor is made of. A model this package carries no published price for is refused a cost ceiling outright, with a `ConfigError` naming what is missing and exit code 4, rather than being bounded against a guessed rate.

### --verbose

`--verbose` (or `-v`) is global, so it goes before the subcommand. Stop reasons and gaps are printed
without it. The flag adds alignment details, type mismatches, and key collisions.

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

Every line appears only when there is something to report. Under `--format json`, `gaps`,
`stop_reasons`, `requested_attrs`, `row_estimate`, `cardinality_estimate`, `primary_keys`,
`observed_key_count`, `marginal_key_yields`, and `incomplete_key_records` are always present.
`row_estimate` is the accepted non-flat bootstrap hint or JSON `null`; the table footer prints its
value or `unknown`. `normalization_collisions` appears when non-empty. `stopped_by` appears when a
stop reason exists and remains the first reason for compatibility.

Nothing here exposes a cost, time, or call ceiling as a persistent setting: the three `--limit-*` options apply to the one command that carries them, since the CLI builds a fresh source per invocation and reads no configuration file.

That is the same shape the library has: a ceiling passed with a request bounds that request and does not leak into the next.

There is no `llm estimate`. The library can report what a request would cost before running it, and this command does not expose it, so a person sizing a request ahead of time uses the library.

## Decomposition

One model call answers a request by default. A large ask can be split into parts, each asked
separately and merged on the way back, which costs a call per part and is never chosen for
you.

`--plan-continuations` lets a part ask again for records the first answer did not include.
It is `0` by default, so each part asks once unless you raise it. It bounds asks per part, not
provider attempts or planning calls: each ask may still be retried up to `limits.max_retries` times,
three attempts by default. Non-flat planning also uses the selected schedule and may add bounded
proposal, distribution, or entity-enumeration calls before leaf extraction.

```bash
llm fetch "the member states of the European Union" --plan-continuations 2
```

`--plan-mode` selects the shape: `flat` asks once and is the default, `partitioned` splits
the ask recursively, and `row_by_row` asks per entity. `--plan-key` names the attribute that
identifies a record, so parts returning the same record merge rather than duplicating it;
it has to be one of the attributes you asked for.

`--plan-depth` bounds recursion, `--plan-partitions` bounds children per node, and
`--plan-target-rows` supplies the uncertain row-count threshold for descendant splits.
`--plan-partition-key` guides only the root proposal. `--plan-schema-mode preserve` keeps caller
attributes; `enrich` lets non-flat planning append inferred attributes.

The remaining standard controls are `--plan-keys` for ordered composite identity,
`--plan-exact-rows` for a hard target when the plan has one leaf, `--plan-exclusion-bytes` for
continuation-prompt size, `--plan-planning-order` for one of the five schedules, and
`--plan-expose-estimate`/`--no-plan-expose-estimate` for extraction-prompt context. Together the CLI,
MCP, and Python surfaces share 14 controls. The rule-backed distribution controls and optional
planning receipt remain direct-Python-only in this release. The two locked schedules require a
non-empty caller schema and ordered `--plan-keys`. A multi-leaf plan does not apply
`--plan-exact-rows` and adds `exact_rows_not_leaf_bound` to the result's gaps.

```bash
llm fetch "the member states of the European Union" \
    --attr name --attr capital --plan-key name --plan-continuations 1
```

When an answer is short, the reason is printed with it rather than left to be inferred from
the row count — a `gap:` line per shortfall, and the same list under `gaps` in
`--format json`. No gap means that no supported operational diagnostic fired; it is not a
completeness result.

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
| 8 | Extraction returned an answered result after some work was interrupted. `meta.outcome` is `partial`; gaps and stop reasons carry the details |

```text
llm: ParseError: model response is not valid JSON: Expecting value: line 1 column 1
```

A missing `OPENAI_API_KEY` exits 5, not 4. The library never reads a provider credential, so it cannot report one as missing; the provider SDK reports it as a call that failed, which becomes a `TransportError`:

```text
llm: TransportError: litellm.InternalServerError: OpenAIException - Missing credentials.
```

**Exit 2 is Click's, not this package's.** An unknown or syntactically wrong option — a misspelled `--limit-cst`, a `--limit-cost` that is not a number, a `--max-tokens` below one — never reaches the library, so it exits 2 with Click's own message and suggestion.

Code 4 covers a value that parsed and the library then rejected: a negative or non-finite ceiling, an unknown `llm.*` override, a setting deferred to v2.0, a cost ceiling whose bound cannot be established, and a missing optional extra.

A cost, time, call, or productive continuation ceiling that stops a run exits 7. The continuation
allowance can come from `--effort medium`/`high` or an explicit `--plan-continuations`. Whatever
records were collected are printed first and the run is reported in `stopped_by`, so nothing is
hidden, but the code is what a script branches on and a stopped run must not look like a completed
one. The row cap is the exception: it is the one ceiling that leaves a usable result, so
`--max-records` reports `outcome: truncated` and exits 0.

## Tests

```bash
python3 -m pytest tests/unit -q
python3 -m ruff format --check . && python3 -m ruff check .
```

Every `llm` command shown above is run by `tests/unit/test_readme.py`, which reads this file, extracts the `bash` blocks, and invokes each one against a fake model client. A command that stops working here fails the suite.
