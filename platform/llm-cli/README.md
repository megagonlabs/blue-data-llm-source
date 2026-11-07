# blue-data-llm-cli

`blue-llmdb` is the command-line interface for `blue-data-llm-source`. It manages profiles, translates command options into library settings, and formats query results.

## Install

From the checkout root, with `blue-data` beside it, use the root `.venv`:

```bash
python3 -m venv .venv
source .venv/bin/activate
./platform/llm-cli/scripts/install.sh
```

If `.venv` already exists, activate it and run only the installer. It installs `blue-data`, the library's `dev` and `llm` extras, and the CLI's `dev` extra. Alternatively, use Conda from the same checkout root:

The [installation guide](../../docs/installation.md) is the reference for these Conda steps.

Run these commands from the `blue-data-llm-source` checkout with `blue-data` beside it. Create the environment once:

```bash
ls -d ../blue-data/lib &&
conda create --name blue-llmdb python=3.12 pip
```

Then activate it and install in dependency order:

```bash
conda activate blue-llmdb &&
python -m pip install -e ../blue-data/lib &&
python -m pip install -e "./lib[llm]" &&
python -m pip install -e ./platform/llm-cli &&
blue-llmdb --version
```

If the environment already exists, start at `conda activate blue-llmdb`. The install is editable. Neither this CLI package nor its `blue-data` dependency is available from the public package index, so install from the checkouts. You can also run `platform/llm-cli/scripts/install.sh` in an active environment; it installs the development extras and accepts `BLUE_DATA_PATH` for a non-sibling checkout. [Installation](../../docs/installation.md) covers `venv` and the component-local `.venv` used by Pyright.

## Commands

```bash
blue-llmdb --help
```

```bash
blue-llmdb --version
```

The CLI provides twelve commands over four components, using the form `blue-llmdb <component> <action> <parameters>`:

```
blue-llmdb [--version] [-h|--help] [--profile NAME]
├── profile
│   ├── add NAME
│   ├── remove NAME [--confirm]
│   ├── list [--format]
│   ├── config [PARAM [VALUE]] [--profile NAME]
│   ├── inspect [NAME] [--format]
│   └── select NAME
├── data
│   ├── query QUERY [query options] [--format]
│   └── estimate QUERY [estimate options] [--format]
├── model
│   ├── list [--format]
│   ├── inspect NAME [--format]
│   └── select NAME
└── metadata
    └── inspect [--format]
```

Retrieval runs through `data query`; `--attr` declares result fields, and `metadata inspect` reports the source protocol, variant, and library version. Search and query are the same operation for this source. See the [CLI guide](../../docs/cli.md) for the reasons behind its profile, output, and error conventions.

```bash
blue-llmdb metadata inspect
```

Prints the protocol, the variant, and the version of the library underneath:

```text
┏━━━━━━━━━━┳━━━━━━━━┓
┃ key      ┃ value  ┃
┡━━━━━━━━━━╇━━━━━━━━┩
│ protocol │ llm    │
│ variant  │ openai │
│ version  │ 1.0.0  │
└──────────┴────────┘
```

Every command that returns a result takes `--format`, including this one:

```bash
blue-llmdb metadata inspect --format json
```

```text
{
  "protocol": "llm",
  "variant": "openai",
  "version": "1.0.0"
}
```

## Configuration

Settings live in profiles under `~/.blue-llmdb`. Every command reads them, and the order is the same everywhere:

**command option > environment variable > profile > built-in default.**

```bash
blue-llmdb profile list
```

```bash
blue-llmdb profile config BLUE_DATA_LLM_MODEL gpt-4o
```

`profile config` has three forms: with no argument, it prompts for the parameters requested on a first run; with a parameter name, it prompts for that parameter; with a name and value, it sets the value directly. If the profile named by `--profile` does not exist, the write creates it. A refused parameter or value leaves no profile behind.

Put `--` before a negative positional value so Click passes it to the command; limit parameters then reject the negative number.

```bash
blue-llmdb profile config
```

```bash
blue-llmdb profile config OPENAI_API_KEY
```

`profile inspect` shows every parameter, its value, and the tier that supplied it: `environment`, `profile:<name>`, `default`, or `unset`. It cannot report `flag`: command options do not reach the resolver and are applied on top of its result.

```bash
blue-llmdb profile inspect
```

Profiles are independent, and `--profile` picks one for a single command:

```bash
blue-llmdb profile add staging
```

```bash
blue-llmdb profile config --profile staging BLUE_DATA_LLM_SOURCE_LIMIT_COST 0.50
```

```bash
blue-llmdb profile select staging
```

```bash
blue-llmdb profile select default
```

```bash
blue-llmdb profile remove staging --confirm
```

`--confirm` skips the prompt. Without it, answering no leaves the store unchanged and exits 0. Removing a profile deletes only that profile; it does not remove caches or other profiles. If the removed profile was selected, selection moves to `default` when available or to the first remaining profile, and the command reports the change. The last profile cannot be removed because selection must always name an existing profile.

### Parameters

| Parameter | Cast | Default | Asked on a first run | Reaches |
|---|---|---|---|---|
| `OPENAI_API_KEY` | secret | — | yes | the provider SDK, through the environment |
| `BLUE_DATA_LLM_MODEL` | str | `gpt-6-luna` | yes | `llm.model` |
| `BLUE_DATA_CLI_FORMAT` | str | `table` | | the default for `--format` |
| `BLUE_DATA_CLI_LOG_LIMIT` | int | `80` | | the default for `--log-limit` |
| `BLUE_DATA_LLM_SOURCE_MAX_RECORDS` | int | — | | the default for `--max-records` |
| `BLUE_DATA_LLM_SOURCE_MAX_TOKENS` | int | — | | `llm.max_tokens` |
| `BLUE_DATA_LLM_SOURCE_REASONING_EFFORT` | str | — | | `llm.reasoning_effort` |
| `BLUE_DATA_LLM_SOURCE_TEMPERATURE` | float | — | | `llm.temperature` |
| `BLUE_DATA_LLM_SOURCE_TIMEOUT` | float | — | | `llm.timeout` |
| `BLUE_DATA_LLM_SOURCE_MAX_PARALLEL` | int | — | | `llm.max_parallel` |
| `BLUE_DATA_LLM_SOURCE_CACHE` | bool | — | | `llm.cache` |
| `BLUE_DATA_LLM_SOURCE_CACHE_DIR` | directory | `<home>/cache` | | `llm.cache_dir` |
| `BLUE_DATA_LLM_SOURCE_PRICE_PER_INPUT_TOKEN` | float | — | | `llm.price_per_input_token` |
| `BLUE_DATA_LLM_SOURCE_PRICE_PER_OUTPUT_TOKEN` | float | — | | `llm.price_per_output_token` |
| `BLUE_DATA_LLM_SOURCE_VARIANT` | str | — | | `connection.variant` |
| `BLUE_DATA_LLM_SOURCE_LIMIT_COST` | float | — | | `limits.max_cost` |
| `BLUE_DATA_LLM_SOURCE_LIMIT_SECONDS` | float | — | | `limits.max_seconds` |
| `BLUE_DATA_LLM_SOURCE_LIMIT_CALLS` | int | — | | `limits.max_calls` |
| `BLUE_DATA_LLM_SOURCE_MAX_RETRIES` | int | — | | `limits.max_retries` |
| `BLUE_DATA_LLM_SOURCE_EFFORT` | str | — | | `plan.max_continuations` |

A profile leaves `BLUE_DATA_LLM_SOURCE_EFFORT` unset unless you set it. When `data query` has no `--effort` or configured effort, it uses `low` (zero continuations), except in `partitioned` mode, which allows three.

A parameter name is also its environment-variable name, so exporting `BLUE_DATA_LLM_MODEL` overrides the profile for one shell without editing it. An empty exported value is refused instead of treated as unset. `BLUE_DATA_LLM_MODEL=''` is still an explicit value; falling through to the profile would hide a configuration error. Use `unset BLUE_DATA_LLM_MODEL` to remove the environment tier. Profile storage also refuses blank values because they would otherwise be written but skipped during resolution. A blank or undecodable value edited into the profile file by hand does not block `profile list`, `add`, `select`, `remove` or `config`, so the profile can be repaired; data, model and metadata commands refuse it with ``KEY is blank in profile 'NAME' in <home>/.profiles; it would be read as unset. Set it with `blue-llmdb profile config KEY VALUE --profile NAME`, or delete that line.``

Nothing blocks on a parameter being unset. "Asked on a first run" means `blue-llmdb profile config` with no argument walks those two, and nothing more. `profile`, `model`, `metadata` and `data estimate` contact no provider and run against an empty profile. A missing credential surfaces only when `data query` reaches the provider. With no answered extraction response and no retained records, it normally raises a transport error; a cost or time ceiling can stop a retry after the failed attempt. If an extraction response has already been parsed, the result is partial with a gap for the failed part. A ceiling refusal before the first attempt is a stopped result.

Two settings come from the environment rather than a profile. `BLUE_DATA_LLM_SOURCE_HOME` moves the CLI home, where profiles are stored. An empty value is refused because an empty path resolves to the working directory. `BLUE_DATA_PROFILE` selects the profile, and the root `--profile` option overrides it. If either names a nonexistent profile, the CLI refuses to run with an empty profile; otherwise, a misspelled name could silently omit a configured ceiling.

### Storage and secrets

```
~/.blue-llmdb/            directory mode 0700
├── .profiles             INI, one [section] per profile, file mode 0600
├── .selected.profile     the selected profile name, mode 0600
└── cache/                the default llm.cache_dir
```

Every write first goes to a temporary file in the same directory, then moves into place. An interrupted write therefore cannot leave a profiles file that the next run cannot parse. Values containing line breaks are refused because the file is line-oriented; otherwise, a value pasted from a CRLF file could be read as a separate setting. A symlink from inside the home to a path outside it is refused. A home directory at 0755 is reported once and left as it is. A home that group or others can write to is refused, and so is a settings file that group or others can read or write: every command then exits 4 with `ERROR: PROFILE` naming the path and the mode it needs, until you fix it with `chmod 700` on the directory or `chmod 600` on the file. The CLI never changes an existing mode.

A secret is never accepted as a value on the command line, where it would land in shell history. `blue-llmdb profile config OPENAI_API_KEY` prompts with the input hidden. Enter `$MY_KEY` at that prompt to store a reference to an environment variable instead of the secret itself; `profile inspect` then shows `$MY_KEY (set)` or `$MY_KEY (unset)`, so you can see what to export.

A directly entered secret is stored as base64 behind an `enc:` prefix. **This is obfuscation, not encryption.** Anyone who can read the profile, including a copy in a backup or sync service, can decode the secret. On POSIX systems, the `0600` file mode restricts local access; copies need their own access controls. If the profile may be copied, enter a `$MY_KEY` reference to keep the secret out of it. Commands do not print secret values in profile output, logs, or error messages.

Errors and `--log` output can include library or provider text. The CLI masks credential-shaped strings and values held in credential environment variables in errors, logs, settings values, and result metadata. It also masks malformed profile errors without quoting the profile file. Ordinary model names remain visible. A cache path can appear partly masked if it contains credential-shaped text. Returned records remain intact, including values that resemble credentials.

## data query

```bash
blue-llmdb data query "the member states of the European Union"
```

In the default flat mode, named attributes define the output columns. Non-flat planning with schema enrichment may add inferred columns. `NAME:TYPE` declares a type, and values that can be coerced to it are coerced; `int`, `float`, `bool`, and `date` are understood and converted. `string` (also `str`, `text`) is checked but not converted, so a declared string that arrives as a number is reported rather than stringified. Any other name is accepted, checks nothing, and is listed once under `unchecked_types`.

```bash
blue-llmdb data query "the member states of the European Union" \
    --attr country --attr accession_year:int
```

```text
┏━━━━━━━━━┳━━━━━━━━━━━━━━━━┓
┃ country ┃ accession_year ┃
┡━━━━━━━━━╇━━━━━━━━━━━━━━━━┩
│ France  │ 1958           │
│ Germany │ 1958           │
└─────────┴────────────────┘
2 records | model gpt-6-luna | 1 call | 46 tokens | row estimate unknown | $0.0000 | 0.31s
settings: llm.cache_dir=~/.blue-llmdb/cache (default), llm.model=gpt-6-luna (default), plan.max_continuations=0 (default)
```

`--context` adds text that clarifies the ask, usually what an attribute name means:

```bash
blue-llmdb data query "the largest lakes in Africa" --context "rank by surface area, not volume"
```

`--max-records` is the row ceiling. It is the only flag for that knob: the library clamps it by any configured `limits.max_rows`.

```bash
blue-llmdb data query "the member states of the European Union" --max-records 5
```

`--effort` is a short form for the continuation allowance. `low`, `medium` and `high` allow up to one, two or three logical extraction asks per part when a primary key is available. Later asks request keys the earlier answers missed, so they can improve coverage. The allowance bounds asks, not billed attempts; transport retries can add provider attempts. If the last allowed ask under `medium` or `high` still adds new keys, the records gathered so far are printed, the result reports `stopped by: plan.max_continuations`, and the command exits 7. That means the effort allowance bounded the result, not that the provider failed. In flat mode, `medium` and `high` need a requested `--plan-key` or `--plan-keys`; the CLI refuses a missing key before constructing the source. Non-flat planning can infer one. Use `--plan-continuations` when you need an exact allowance; it overrides `--effort`. In `partitioned` mode the default without either flag is four asks per part, so any `--effort` level lowers it; `high` gives three.

```bash
blue-llmdb data query "the member states of the European Union" \
    --attr country --plan-key country --effort medium
```

### Model and adapter options

`--model`, `--max-tokens`, `--reasoning-effort`, `--temperature`, `--timeout`, `--max-parallel`, `--cache`/`--no-cache`, `--cache-dir`, `--price-in` and `--price-out` set the library's `llm.*` settings of the same meaning, and `--variant` sets the source variant within the `llm` protocol. Each has a profile parameter, so a value you use every time belongs in the profile and a value you use once belongs on the command line. Each model the package knows has its own reasoning efforts, default effort and temperature rule, listed under [Supported models](../../lib/README.md#supported-models). GPT-6 Luna defaults to medium reasoning and accepts `--temperature` only with `--reasoning-effort none`; GPT-4o sends temperature 0 and accepts no reasoning effort. A combination the model rules out is refused before anything is spent.

```bash
blue-llmdb data query "the member states of the European Union" \
    --model gpt-4o --max-tokens 512 --temperature 0 --no-cache
```

`llm.stream` has no option. Streaming has no meaning for a command whose result is a parsed record set. `llm.rate_limit` is refused by the library by name, and `--rate-limit` is refused here as `ERROR: USAGE` carrying the library's own reason.

### Spending, time and call ceilings

A ceiling applies to one request. A profile supplies the default for each request. Every run prints which ceilings applied and where each came from, so neither fact has to be inferred.

`--limit-cost` is claimed against an upper bound on what the call can cost rather than an estimate of what it is likely to cost. The bound is reconciled once the call returns: each contacted attempt keeps at least its reservation, and a higher readable figure raises the charge as [the cost bound](../../lib/README.md#the-cost-bound) explains. [What `cost` means](../../lib/README.md#what-cost-means) explains the ledger figure. Under a cost ceiling, `meta.cost` can remain at the reservation and `meta.accounting_known` can be false; that amount is not measured provider spend. A request refused this way prints no records and exits 7, with `outcome: stopped_by_limit` and `stopped by: max_cost` under the table.

```bash
blue-llmdb data query "the member states of the European Union" \
    --model gpt-4o --limit-cost 0.50 --limit-seconds 30 --limit-calls 2 --max-retries 1
```

`--limit-cost 0.50` is above the floor for that model, which is what makes the command above work. The output-only floor is the whole allowance at the model's output rate, with any long-context output multiplier and regional uplift, and does not depend on the prompt: GPT-4o's default allowance is 4,096 output tokens, for a floor of about $0.041, so `--limit-cost 0.01` would refuse every call before making one. The GPT-6 Luna default is 25,000 tokens, for an output-only floor of about $0.020625 including the long-context output multiplier and OpenAI's 10% regional-processing uplift. Ask for a smaller answer with `--max-tokens` rather than raising the ceiling, since the output allowance is what the floor is made of. A model this package carries no published price for needs both `--price-in` and `--price-out` before a cost ceiling can be used. Without those rates, the command exits 4 with a `ConfigError` naming what is missing. `blue-llmdb model list` shows the models in the package price table.

### Decomposition

One model call answers a request by default. A large ask can be split into parts, each asked separately and merged on the way back, which costs a call per part and is never chosen for you.

`--plan-mode` selects the shape: `flat` asks once and is the default, `partitioned` asks the whole request first and splits it, recursively, only when that answer falls short, and `row_by_row` asks per entity. `--plan-key` names the attribute that identifies a record, so parts returning the same record merge rather than duplicating it; it has to be one of the attributes you asked for.

```bash
blue-llmdb data query "the member states of the European Union" \
    --attr name --attr capital --plan-key name --plan-continuations 1
```

`--plan-depth` bounds recursion, `--plan-partitions` bounds children per node, and `--plan-target-rows` supplies the uncertain row-count threshold for descendant splits. `--plan-leaves` caps the number of leaf parts; a split that would exceed it is refused and its part is asked whole. `--plan-partition-key` guides only the root proposal. `--plan-schema-mode preserve` keeps caller attributes; `enrich` lets non-flat planning append inferred attributes.

The remaining standard controls are `--plan-keys` for ordered composite identity, `--plan-exact-rows` for a hard target when the plan has one leaf, `--plan-exclusion-bytes` for continuation-prompt size, `--plan-planning-order` for one of the five schedules, and `--plan-expose-estimate`/`--no-plan-expose-estimate` for extraction-prompt context.

Five optional controls change how a split is asked. Each keeps its default behavior unless selected:

- `--plan-leaf-prompt keyed-leaf-fetch-2` asks a leaf for every matching record rather than a sample. It is experimental; check the returned records when using it.
- `--plan-child-wording canonical` builds each child's request from the original request and its own filters rather than the parent's wording.
- `--plan-filter-enforcement drop` removes records that contradict their leaf's filter before merging. The default, `flag`, keeps them. Both count the contradictions in `filter_contradictions`.
- `--plan-range-filters` reads a partition value written as an integer span, such as `1946-1952`, as a range filter. It requires `--plan-leaf-prompt keyed-leaf-fetch-2`; `--no-plan-range-filters` disables it.
- `--plan-partition-guardrails` adds proposal rules against identifier, measurement, placeholder, and catch-all values, and rejects a numeric key bucketed like a measurement. `--no-plan-partition-guardrails` disables it.

These five controls are experimental and add no model calls.

A partitioned plan asks its root first, under the default `--plan-split-policy evidence`, and splits only when that answer shows one request was not enough: it was cut off, was still growing when its continuations ran out, failed, came back empty when records were expected, or came back far short of an estimate above `--plan-split-threshold-rows` (default 120). Otherwise that answer is the result, and `split_decision` reports the choice. `--plan-split-policy always` splits the root without asking it first. The root's asks count in the published bound. With no effort set, the root may ask again three times; `--effort` and `--plan-continuations` set the allowance. An accepted answer does not prove the list is complete.

The CLI, MCP, and Python APIs share these 21 planning controls. Rule-backed distribution controls and the optional planning receipt are available only through direct Python calls. Both locked schedules require a non-empty caller schema and ordered `--plan-keys`. A plan with multiple leaves does not apply `--plan-exact-rows`; it reports `exact_rows_not_leaf_bound` in the result's gaps.

None of the `--plan-*` controls has a profile parameter, so none of them is in the precedence chain: each is read from the command line and nowhere else. A plan setting describes one ask, not a deployment, and a partition key or a row target left in a profile would apply itself to the next, unrelated query. `--effort` is the one planning-adjacent option that does read configuration, as `BLUE_DATA_LLM_SOURCE_EFFORT`, because a continuation allowance is an appetite for spending rather than a shape.

```bash
blue-llmdb data query "the largest lakes in Africa" \
    --plan-mode partitioned --plan-depth 2 --plan-partitions 4 --plan-target-rows 50
```

When a limit or interrupted part is detected, the result reports it: an `outcome:` line appears when the outcome is not `ok`, and `gap:` lines name supported diagnostics. Machine formats carry the same facts under `outcome` and `gaps`. `empty_query`, `model_returned_none`, and `filtered_to_empty` distinguish three kinds of empty result. A short answer can still have outcome `ok` and no gap; neither field measures completeness.

### `--verbose` and `--log`

`--verbose` adds key alignment, type mismatches and collisions. The outcome, the stop reasons and the gaps are printed without it.

```bash
blue-llmdb data query "the member states of the European Union" \
    --attr country --max-records 20 --verbose
```

`--log` writes each step of the run to standard error as its own line, truncated to `--log-limit` characters, and prints a traceback if the run fails. It never writes to standard output, so `--log --format json` is still one JSON object.

```bash
blue-llmdb data query "the member states of the European Union" --log --log-limit 60
```

## data estimate

`data estimate` prices a request without contacting a provider.

```bash
blue-llmdb data estimate "the member states of the European Union"
```

It prices **one flat fetch**, including retries. Non-flat runs may use different prompts for planning and extraction, so the estimate does not bound their total cost. `--limit-calls` and `--max-retries` move the number, because the bound accumulates once per attempt that can actually happen.

```bash
blue-llmdb data estimate "the member states of the European Union" \
    --model gpt-4o --max-tokens 512 --limit-calls 2 --max-retries 1
```

Every option that appears on both `data query` and `data estimate` has the same name, type, default and meaning on both. `data estimate` refuses `--max-records`, `--effort` and every `--plan-*` option, because `estimate` reads no plan setting, and a setting accepted and ignored is exactly what the library's closed key set exists to prevent. The refusal names the flag and points at `data query`.

## model

```bash
blue-llmdb model list
```

The models this package carries a published price for. Any model name the provider accepts can be used; these are the ones a cost ceiling can be bounded against without supplying rates of your own. [Supported models](../../lib/README.md#supported-models) gives each one's reasoning efforts and prices.

```bash
blue-llmdb model inspect gpt-4o
```

The `published_defaults.*` rows are the model's entry in the package's table: the reasoning efforts it accepts and its default, when it accepts a temperature (`always`, `effort_none_only` or `never`), the output-token parameter, the cached-input and cache-write rates, and the long-context threshold. They describe the model; a profile setting or a flag overrides them for a query. The `nl` format adds a sentence naming the floor a cost ceiling has to clear, with the regional uplift included where it applies.

```bash
blue-llmdb model select gpt-4o
```

`model select` stores the name as the profile's `BLUE_DATA_LLM_MODEL`, so `--model` still overrides it for one request and `profile inspect` shows where it came from. Selecting a model with no published price warns and names the two price parameters, because a cost ceiling cannot be bounded against it until you supply them.

## Output formats

`--format table|json|nl|csv` is on every command that returns a result, and the default comes from `BLUE_DATA_CLI_FORMAT` when a profile sets one.

```bash
blue-llmdb data query "the member states of the European Union" --format csv
```

```bash
blue-llmdb data query "the member states of the European Union" --format nl
```

`table` is the Rich table above. `csv` is RFC 4180 with the same columns; a cell beginning `=`, `+`, `-` or `@` receives a leading apostrophe before CSV encoding because a spreadsheet may read it as a formula. `nl` is a short paragraph: how many records, from which model, at what cost, and whether anything stopped the run early. Every format strips terminal control characters from every cell.

`json` is one object on standard output and nothing else:

```bash
blue-llmdb data query "the member states of the European Union" \
    --model gpt-4o --attr country --attr accession_year:int \
    --limit-cost 0.50 --format json
```

Sample output, shortened to one of 27 records and its matching `record_origins`. `<cache-path>` stands for the local cache directory; values vary by run.

```text
{
  "records": [
    {
      "country": "Austria",
      "accession_year": 1995
    }
  ],
  "meta": {
    "outcome": "ok",
    "model": "gpt-4o-2024-08-06",
    "calls": 1,
    "prompt_tokens": 177,
    "completion_tokens": 461,
    "cost": 0.043123,
    "elapsed_seconds": 5.22,
    "row_estimate": null,
    "cardinality_estimate": null,
    "primary_keys": [],
    "observed_key_count": 0,
    "marginal_key_yields": [
      27
    ],
    "incomplete_key_records": 0,
    "filter_contradictions": 0,
    "gaps": [],
    "stop_reasons": [],
    "requested_attrs": [
      "country",
      "accession_year"
    ],
    "record_origins": [
      [
        "0.0"
      ]
    ],
    "accounting_known": false
  },
  "settings": {
    "applied": {
      "llm.model": "gpt-4o",
      "llm.cache_dir": "<cache-path>",
      "limits.max_cost": 0.5,
      "plan.max_continuations": 0
    },
    "source": {
      "llm.model": "flag",
      "llm.cache_dir": "default",
      "limits.max_cost": "flag",
      "plan.max_continuations": "default"
    }
  }
}
```

The CLI JSON result has `records`, `meta`, and `settings`. The MCP tool result has `records` and `meta`.

`meta` always carries `outcome`, the accounting fields, `gaps`, `stop_reasons`, `requested_attrs`, `row_estimate`, `cardinality_estimate`, `primary_keys`, `observed_key_count`, `marginal_key_yields`, `incomplete_key_records` and `filter_contradictions`, even when the list is empty: a reader branches on what they say rather than on whether they arrived. `normalization_collisions`, `repaired_numbers`, `split_decision`, `unchecked_types`, `record_origins`, `asked`, `stopped_by`, `truncated_to` and `cache_hits` appear when there is something to report, and `accounting_known` appears only when it is false. `--verbose` adds `unmapped_keys`, `remapped_keys`, `type_mismatches` and `key_collisions`, which carry arbitrary model output.

In a planned call with no `--attr`, `requested_attrs` remains empty even if the planner inferred columns. Inspect the returned records and diagnostics; this field records the caller's request, not the resolved schema.

`settings` is this CLI's own envelope, and its scope is exactly what the CLI can know: the values this invocation resolved and passed to the library, and the tier each came from. It is not a report of the library's effective configuration. Keys are the library's own dotted names, so the same spelling appears here, in the profile file and in the MCP schema. A property this run did not set appears in neither map. Under `--format table` the same pairs print as a `settings:` footer; under `--format nl` only the ceilings are named.

Progress, logs and errors go to standard error. The result goes to standard output. Every format is safe to pipe, and the spinner is suppressed outright under `json` and `csv` rather than left to terminal detection.

## Errors and exit codes

Errors reach the terminal as one line on standard error, with no traceback unless `--log` is set:

```text
ERROR: PARSE ParseError: model response is not valid JSON: Expecting value: line 1 column 1
```

| Code | Exit | Cause |
|---|---|---|
| — | 0 | Success, including an empty query and a truncated result |
| `INTERNAL` | 1 | An `LlmSourceError` outside the classified kinds |
| `USAGE` | 2 | Usage error: unknown option, bad value, missing argument, an option on the wrong command, a refused deferred setting |
| `QUERY` | 3 | `QueryError`: the query or an argument was malformed |
| `CONFIG` | 4 | `ConfigError`: a library setting the source rejected |
| `PROFILE` | 4 | A profile operation this CLI rejected: unknown profile, unknown parameter, a value that will not cast |
| `TRANSPORT` | 5 | `TransportError`: the provider failed or answered with something unusable |
| `PARSE` | 6 | `ParseError`: the model's answer was not a record list |
| `BUDGET` | 7 | A ceiling ended the run. `meta.stopped_by` names the axis. A row cap stays 0, since it leaves a usable result |
| `PARTIAL` | 8 | Extraction returned an answered result after some work was interrupted. `meta.outcome` is `partial`; gaps and stop reasons carry the details |

`CONFIG` and `PROFILE` share exit 4 and are told apart by the code token. A script branching on "the configuration is wrong" wants one branch; a person reading the line wants to know which half.

No command here is a batch command, so there is no partial-success rule to define beyond `PARTIAL`, which is about one request rather than about several.

The provider SDK detects a missing `OPENAI_API_KEY` during a call; the library masks credential-shaped diagnostic text. With no answered extraction response and no retained records, exhausted retries or a `max_calls` retry refusal produce `TRANSPORT` with exit 5. If `max_cost` or `max_seconds` refuses the retry, the command reports `stopped_by_limit` and exits 7. A ceiling refusal before the first attempt also exits 7. If an extraction response has already been parsed, the result is partial with a gap for the failed part. The SDK-specific error prefix can vary by LiteLLM version. Set the key with `blue-llmdb profile config OPENAI_API_KEY` or export `OPENAI_API_KEY` in the process environment. With `--limit-calls 1`, the fixed retry-stop text for a request with no answered extraction is:

```text
ERROR: TRANSPORT TransportError: Provider request failed; max_calls stopped retries.
```

**Exit 2 belongs to Click's parser.** A misspelled option, bad value, or missing argument exits 2. On a command that accepts `--format`, `--format json` or a valid `BLUE_DATA_CLI_FORMAT=json` default also writes `{"error": {"code": "USAGE", "message": ...}}` to standard output if parsing fails. The last explicit `--format` flag wins over the default; a flag after `--` is treated as an argument. The standard error line remains. A profile default is available only after the root callback exports it. Root-option parse failures can use an already exported environment default, but cannot use a profile-only default; pass `--format json` on the selected command when you need JSON for those failures. Errors after parsing use the same JSON envelope when the selected format is already known, including an unknown `--profile`, an unreadable profiles file, or a profile value that will not cast. Before a profile can be read, startup errors use an explicit format flag or an already exported environment default. Commands without `--format` report errors only on standard error. If `BLUE_DATA_CLI_FORMAT` itself holds an invalid value and no format flag is given, that lookup also reports only on standard error.

## Tests

Run these commands from `platform/llm-cli/` in the active environment.

```bash
python3 -m pytest tests/unit -q
python3 -m ruff format --check . && python3 -m ruff check .
```

For the type check, create a `.venv` in this directory before installing. `pyproject.toml` points `pyright` at it, and without one pyright reports every import as unresolved instead of real typing errors:

```bash
python -m venv .venv && source .venv/bin/activate
```

Every `blue-llmdb` command shown above is run by `tests/unit/test_readme.py`, in the order it appears, against a fake model client and a throwaway CLI home. A command that stops working here fails the suite.
