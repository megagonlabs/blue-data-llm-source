# CLI

`blue-llmdb` provides twelve commands across four components for configuration, model inspection, cost estimation, and record queries.

This page mirrors [`platform/llm-cli/README.md`](../platform/llm-cli/README.md), which owns the command-surface contract and also covers package installation and development.

## Upgrading from `llm`

Version 2.0.0 replaces the `llm` command. `llm fetch` is now `blue-llmdb data query`, and `llm info` is now `blue-llmdb metadata inspect`. The old entry point has no alias, so existing scripts must be updated. Version 2.0.0 also adds profiles, a configuration precedence chain, the `model` and `metadata` components, `data estimate`, four output formats, and coded errors. See [`CHANGELOG.md`](../CHANGELOG.md) for the complete migration notes.

## Install

```bash
platform/llm-cli/scripts/install.sh
```

That installs `blue-data` from a checkout, then the library from `lib/`, then the CLI, all editable. Set `BLUE_DATA_PATH` if `blue-data` does not sit alongside this repository. [installation.md](installation.md) covers every route, the extras, and what each script does.

## Commands

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

Commands use the form `blue-llmdb <component> <action> <parameters>`.

| Command | What it does | Contacts a provider |
|---|---|---|
| `profile add NAME` | Create a profile | no |
| `profile remove NAME [--confirm]` | Delete a profile's section of the profiles file | no |
| `profile list` | Every profile, which one is selected, how many parameters each sets | no |
| `profile config [PARAM [VALUE]]` | Walk the first-run parameters, prompt for one, or set one outright | no |
| `profile inspect [NAME]` | Every parameter, its value, and the tier that supplied it | no |
| `profile select NAME` | Make a profile the selected one | no |
| `data query QUERY` | Ask for records | **yes** |
| `data estimate QUERY` | Bound what one flat query could cost | no |
| `model list` | The models this package carries a published price for | no |
| `model inspect NAME` | One model's rates and whether a cost ceiling can be bounded against it | no |
| `model select NAME` | Store a model as the profile's `BLUE_DATA_LLM_MODEL` | no |
| `metadata inspect` | The protocol, the variant, and the library version | no |

`data query` is the only command that reaches a model, so it is the only one that needs a credential. Everything else runs against an empty profile on a machine with no key.

```bash
blue-llmdb --version
```

```text
blue-llmdb 2.0.0
```

```bash
blue-llmdb metadata inspect
```

```text
┏━━━━━━━━━━┳━━━━━━━━┓
┃ key      ┃ value  ┃
┡━━━━━━━━━━╇━━━━━━━━┩
│ protocol │ llm    │
│ variant  │ openai │
│ version  │ 2.0.0  │
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
  "version": "2.0.0"
}
```

## Unsupported command groups

This source has no backing store, so several command groups used by storage-backed sources do not apply.

| Not built | Why |
|---|---|
| `database` component | A source that answers from a model's own knowledge has one synthetic database. There is nothing to create, select, or configure. |
| `schema` component | Nothing is stored, so no schema is stored either: `fetch_schema()` returns `{}`. The schema of a request is the `--attr` list you pass to `data query`. |
| `cache` component | The response cache has no introspection API to build a management screen on. `--cache`/`--no-cache` and `--cache-dir` on the request are its whole control surface. |
| `data search` | For this source, searching and querying are one operation, and one operation gets one name. |
| `data import` / `data export` | There is no stored dataset to move in or out. `--format json` or `--format csv` on `data query` writes the result to standard output, which is the export path. |
| A batch command, and the partial-success rule that goes with one | No command here takes a list of inputs and reports an outcome per item, so there is no rule to define about some of several succeeding. `PARTIAL` is about one request that was interrupted after answering, which is a different claim. |

### Differences from other Blue data CLIs

`blue-kb` and `blue-userdb` manage stored data, so they include `database`, `schema`, `data search`, and import/export commands. This CLI instead provides three capabilities specific to model-backed queries.

| Divergence | What the siblings do | What this CLI does | Why |
|---|---|---|---|
| The `model` component | A model name is a profile parameter and a `--model` flag. `model` is not in the family's component vocabulary, and nothing lists or inspects one. | `model list`, `model inspect NAME`, `model select NAME`. | A cost ceiling on this source can only be bounded against a model whose published rates this package carries, and a caller has to be able to find out which those are before a query is refused for it. `model` is a noun coined here rather than one taken from the family list. |
| Four output formats | `table` and `json`, with `nl` added on the query and answer commands. | `table`, `json`, `nl` and `csv`, on every command that returns a result. | `csv` exists because a record set with a declared schema is the shape a spreadsheet takes, which is not true of a graph or a session. Putting all four on every command that returns a result removes the question of which command has which. |
| A coded error vocabulary | `Error: <message>` on standard error, exit `0`, `1` or `2`, with no error codes and no JSON error envelope. | `ERROR: <CODE> <message>`, nine codes over exit statuses 1 to 8, and an error object on standard output under `--format json`. | A run that reaches a provider fails in ways that need different responses: retry, fix the request, raise the ceiling, supply a credential. One exit status of `1` cannot tell a script which. See [Errors and exit codes](#errors-and-exit-codes). |

## Configuration

Settings live in profiles under `~/.blue-llmdb`. Every command reads them, and the order is the same everywhere:

**command option > environment variable > profile > built-in default.**

`profile inspect` reports the winning tier as `environment`, `profile:<name>`, `default`, or `unset`. It cannot report `flag`, because a command option never reaches the resolver; it is applied on top of whatever the resolver returned.

```bash
blue-llmdb profile config BLUE_DATA_LLM_MODEL gpt-4o
```

```text
Set BLUE_DATA_LLM_MODEL in profile default
```

`profile config` has three forms. With no argument it walks the parameters a first run is asked for; with a parameter name it prompts for that one; with a name and a value it sets it outright. A profile named with `--profile` that does not exist yet is created by the write that needs it, so a refused parameter or a value that will not store leaves nothing behind.

```bash
blue-llmdb profile config
blue-llmdb profile config OPENAI_API_KEY
blue-llmdb profile config BLUE_DATA_LLM_SOURCE_LIMIT_COST 0.50
```

Profiles are independent, and `--profile` picks one for a single command.

```bash
blue-llmdb profile add staging
blue-llmdb profile config --profile staging BLUE_DATA_LLM_SOURCE_LIMIT_COST 0.50
blue-llmdb profile list
```

```text
┏━━━━━━━━━┳━━━━━━━━━━┳━━━━━━━━━━━━┓
┃ Profile ┃ Selected ┃ Configured ┃
┡━━━━━━━━━╇━━━━━━━━━━╇━━━━━━━━━━━━┩
│ default │ *        │ 1          │
│ staging │          │ 1          │
└─────────┴──────────┴────────────┘
```

`Configured` counts the parameters that profile sets, and `*` marks the selected one.

```bash
blue-llmdb profile select staging
blue-llmdb profile select default
blue-llmdb profile remove staging --confirm
```

`--confirm` skips the prompt. Without it, answering no leaves the store unchanged and exits 0. Removing a profile deletes only that profile; it does not remove caches or other profiles. If the removed profile was selected, selection moves to `default` when available or to the first remaining profile, and the command reports the change. The last profile cannot be removed because selection must always name an existing profile.

### Parameters

| Parameter | Cast | Default | Asked on a first run | Reaches |
|---|---|---|---|---|
| `OPENAI_API_KEY` | secret | — | yes | the provider SDK, through the environment |
| `BLUE_DATA_LLM_MODEL` | str | `gpt-4o` | yes | `llm.model` |
| `BLUE_DATA_CLI_FORMAT` | str | `table` | | the default for `--format` |
| `BLUE_DATA_CLI_LOG_LIMIT` | int | `80` | | the default for `--log-limit` |
| `BLUE_DATA_LLM_SOURCE_MAX_RECORDS` | int | — | | the default for `--max-records` |
| `BLUE_DATA_LLM_SOURCE_MAX_TOKENS` | int | — | | `llm.max_tokens` |
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
| `BLUE_DATA_LLM_SOURCE_EFFORT` | str | `low` | | `plan.max_continuations` |

A parameter name is also its environment-variable name, so exporting `BLUE_DATA_LLM_MODEL` overrides the profile for one shell without editing it. An empty exported value is refused instead of treated as unset. `BLUE_DATA_LLM_MODEL=''` is still an explicit value; falling through to the profile would hide a configuration error. Use `unset BLUE_DATA_LLM_MODEL` to remove the environment tier. Profile storage also refuses blank values because they would otherwise be written but skipped during resolution.

Nothing blocks on a parameter being unset. "Asked on a first run" means `blue-llmdb profile config` with no argument walks those two, and nothing more. A missing credential surfaces only when `data query` reaches the provider, as `ERROR: TRANSPORT`.

Two settings come from the environment, not from a profile. `BLUE_DATA_LLM_SOURCE_HOME` moves the CLI home, because it is the directory the profiles are read from; exported empty it is refused, since an empty path is the working directory and the store would follow you around. `BLUE_DATA_PROFILE` picks the profile, and the root `--profile` overrides it. Either one naming a profile that does not exist is refused rather than run as an empty one:

```text
ERROR: PROFILE no such profile: 'ghost'. Run `blue-llmdb profile list` to see them, or `blue-llmdb profile add ghost` to make it.
```

The point of putting a ceiling in a profile is that it applies, and a misspelled name would drop it without saying so.

### Storage and secrets

```
~/.blue-llmdb/            directory mode 0700
├── .profiles             INI, one [section] per profile, file mode 0600
├── .selected.profile     the selected profile name, mode 0600
└── cache/                the default llm.cache_dir
```

Every write goes to a temporary file in the same directory and is moved into place, so an interrupted run cannot leave a profiles file the next run refuses to parse. A complete write cannot either: a value holding a line break is refused, because the file is line-oriented and one pasted from a CRLF file would be read back as a separate setting. A path inside the home that is a symlink out of it is refused rather than followed. A path found with wider permissions is reported once; the directory is left as you set it, and a file goes back to 0600 on the next write.

A secret is never accepted as a value on the command line, where it would land in shell history. `blue-llmdb profile config OPENAI_API_KEY` prompts with the input hidden. Enter `$MY_KEY` at that prompt to store a reference to an environment variable instead of the secret itself; `profile inspect` then shows `$MY_KEY (set)` or `$MY_KEY (unset)`, so you can see what to export.

Nor does any command print a credential that reached it another way. An error line, a JSON error object, a log line and the traceback `--log` prints all carry text this CLI did not write: a library message quoting what you typed, or a provider SDK exception that is free to include a request header or a fragment of a key. All of them pass through the library's `redact`, which replaces `***` for a value the environment holds under a credential's name, and for anything written in a credential's shape. So does the model name a run reports, in every format, and the `llm.model` entry in the `settings` envelope: a deployment that pastes a credential into that setting would otherwise get it echoed back in the most visible line this CLI prints. A model name that is only a model name is unchanged.

Two things are deliberately not masked. The rest of the `settings` envelope is not, because a cache directory is a path and a path carries enough entropy to read as a key shape, so masking it would report `***` for a directory you chose. Records are not, because they are model output, and a record value that happens to read like a token is the answer you asked for.

A directly entered secret is stored as base64 behind an `enc:` prefix. **This is obfuscation, not encryption.** Anyone who can read the file can decode it; the file permissions provide the protection. Commands do not print secret values in profile output, logs, or error messages.

## data query

`data query` is the one command that reaches a model, so every example in this section needs a credential the provider SDK can read.

```bash
blue-llmdb data query "the member states of the European Union"
```

Name the attributes you want and you get exactly those columns. `NAME:TYPE` declares a type, and values that can be coerced to it are coerced; `int`, `float`, `bool`, and `date` are understood and converted. `string` (also `str`, `text`) is checked but not converted, so a declared string that arrives as a number is reported rather than stringified. Any other name is accepted, checks nothing, and is listed once under `unchecked_types`.

```bash
blue-llmdb data query "the member states of the European Union" \
    --attr country --attr accession_year:int
```

```text
┏━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━┓
┃ country        ┃ accession_year ┃
┡━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━┩
│ Austria        │ 1995           │
│ Belgium        │ 1958           │
│ Bulgaria       │ 2007           │
│ Croatia        │ 2013           │
│ Cyprus         │ 2004           │
│ Czech Republic │ 2004           │
│ Denmark        │ 1973           │
│ Estonia        │ 2004           │
│ Finland        │ 1995           │
│ France         │ 1958           │
│ Germany        │ 1958           │
│ Greece         │ 1981           │
│ Hungary        │ 2004           │
│ Ireland        │ 1973           │
│ Italy          │ 1958           │
│ Latvia         │ 2004           │
│ Lithuania      │ 2004           │
│ Luxembourg     │ 1958           │
│ Malta          │ 2004           │
│ Netherlands    │ 1958           │
│ Poland         │ 2004           │
│ Portugal       │ 1986           │
│ Romania        │ 2007           │
│ Slovakia       │ 2004           │
│ Slovenia       │ 2004           │
│ Spain          │ 1986           │
│ Sweden         │ 1995           │
└────────────────┴────────────────┘
27 records | model gpt-4o-2024-08-06 | 1 call | 638 tokens | row estimate unknown | $0.0051 | 5.21s
settings: llm.cache_dir=~/.blue-llmdb/cache (default), llm.model=gpt-4o (default), plan.max_continuations=0 (default)
```

The footer under the rows is the accounting line, and the `settings:` line under it names the values this invocation resolved and the tier each came from.

`--context` adds text that clarifies the ask, usually what an attribute name means:

```bash
blue-llmdb data query "the largest lakes in Africa" --context "rank by surface area, not volume"
```

`--max-records` is the row ceiling. It is the only flag for that knob: `fetch` assigns it to the library's `limits.max_rows`, overwriting anything configured.

```bash
blue-llmdb data query "the member states of the European Union" --max-records 5
```

`--effort` is a short form for the continuation allowance. `low`, `medium` and `high` allow up to one, two or three logical extraction asks per part when a primary key is available. Later asks request keys the earlier answers missed, so they can improve coverage. The allowance bounds asks, not billed attempts; transport retries can add provider attempts. If the last allowed ask under `medium` or `high` still adds new keys, the records gathered so far are printed, the result reports `stopped by: plan.max_continuations`, and the command exits 7. That means the effort allowance bounded the result, not that the provider failed. In flat mode, `medium` and `high` need a requested `--plan-key` or `--plan-keys`; the CLI refuses a missing key before constructing the source. Non-flat planning can infer one. Use `--plan-continuations` when you need an exact allowance; it overrides `--effort`.

```bash
blue-llmdb data query "the member states of the European Union" \
    --attr country --plan-key country --effort medium
```

### Model and adapter options

`--model`, `--max-tokens`, `--temperature`, `--timeout`, `--max-parallel`, `--cache`/`--no-cache`, `--cache-dir`, `--price-in` and `--price-out` set the library's `llm.*` settings of the same meaning, and `--variant` sets the source variant within the `llm` protocol. Each has a profile parameter, so a value you use every time belongs in the profile and a value you use once belongs on the command line.

```bash
blue-llmdb data query "the member states of the European Union" \
    --model gpt-4o --max-tokens 512 --temperature 0 --no-cache
```

`llm.stream` has no option here. Streaming has no meaning for a command whose result is a parsed record set. The library refuses `llm.rate_limit` by name, and `--rate-limit` is refused here as `ERROR: USAGE` carrying the library's own reason.

### Spending, time and call ceilings

A ceiling applies to one request. A profile supplies the default for each request. Every run prints which ceilings applied and where each came from, so neither fact has to be inferred.

`--limit-cost` is a dollar ceiling, `--limit-seconds` refuses a call past that age, `--limit-calls` counts retries, and `--max-retries` bounds the attempts one ask may make.

```bash
blue-llmdb data query "the member states of the European Union" \
    --model gpt-4o --limit-cost 0.50 --limit-seconds 30 --limit-calls 2 --max-retries 1
```

`--limit-cost` is claimed against an upper bound on what the call can cost rather than an estimate of what it is likely to cost, and that bound is reconciled against the real figure once the call returns. A request refused this way prints no records and exits 7, with `outcome: stopped_by_limit` and `stopped by: max_cost` under the table. See [The cost bound](../lib/README.md#the-cost-bound) for how it is calculated and how to size one.

There is a floor a cost ceiling has to clear. The floor is the whole output allowance at the model's output rate and does not depend on the prompt: for `gpt-4o` with the library's default of 4096 output tokens it is about $0.041, so `--limit-cost 0.01` would refuse every call before making one. Ask for a smaller answer with `--max-tokens` rather than raising the ceiling, since the output allowance is what the floor is made of. A model this package carries no published price for is refused a cost ceiling outright, with a `ConfigError` naming what is missing and exit code 4, instead of being bounded against a guessed rate.

### Decomposition

One model call answers a request by default. A large ask can be split into parts, each asked separately and merged on the way back, which costs a call per part and is never chosen for you.

`--plan-mode` selects the shape: `flat` asks once and is the default, `partitioned` splits the ask recursively, and `row_by_row` asks per entity. `--plan-key` names the attribute that identifies a record, so parts returning the same record merge rather than duplicating it; it has to be one of the attributes you asked for.

```bash
blue-llmdb data query "the member states of the European Union" \
    --attr name --attr capital --plan-key name --plan-continuations 1
```

`--plan-depth` bounds recursion, `--plan-partitions` bounds children per node, and `--plan-target-rows` supplies the uncertain row-count threshold for descendant splits. `--plan-leaves` bounds the parts a split may run, grouping values past it several to a part. `--plan-partition-key` guides only the root proposal. `--plan-schema-mode` takes `preserve`, which keeps caller attributes, or `enrich`, which lets non-flat planning append inferred ones.

The remaining standard controls are `--plan-keys` for ordered composite identity, `--plan-exact-rows` for a hard target when the plan has one leaf, `--plan-exclusion-bytes` for continuation-prompt size, `--plan-planning-order` for one of the five schedules, and `--plan-expose-estimate`/`--no-plan-expose-estimate` for extraction-prompt context. The CLI, MCP, and Python APIs share these 14 controls. Rule-backed distribution controls and the optional planning receipt are available only through direct Python calls. The two locked schedules need a non-empty caller schema and ordered `--plan-keys`. A multi-leaf plan does not apply `--plan-exact-rows` and adds `exact_rows_not_leaf_bound` to the result's gaps.

None of the `--plan-*` controls has a profile parameter, so none of them is in the precedence chain: each is read from the command line and nowhere else. A plan setting describes one ask, not a deployment, and a partition key or a row target left in a profile would apply itself to the next, unrelated query. `--effort` is the one planning-adjacent option that does read configuration, as `BLUE_DATA_LLM_SOURCE_EFFORT`, because a continuation allowance is an appetite for spending rather than a shape.

```bash
blue-llmdb data query "the largest lakes in Africa" \
    --plan-mode partitioned --plan-depth 2 --plan-partitions 4 --plan-target-rows 50
```

When an answer is short, the reason is printed with it rather than left to be inferred from the row count: an `outcome:` line whenever the outcome is anything but `ok`, a `gap:` line per shortfall, and the same two facts under `outcome` and `gaps` in the machine formats. The outcome is the general statement (`empty_query`, `model_returned_none` and `filtered_to_empty` are three different empty results), and the gaps and the stop reasons refine it. No gap means that no supported operational diagnostic fired. It is not a completeness result.

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

`data estimate` prices a request without contacting a provider, so it runs with no credential present.

```bash
blue-llmdb data estimate "the member states of the European Union" --format nl
```

```text
One flat fetch of this request costs at most $0.1293 against gpt-4o, across 3 attempt(s). A split request costs that figure once per call it makes. No ceiling was set.
```

The same figures in the machine formats:

| Field | Meaning |
|---|---|
| `bounded` | Whether a bound could be established at all |
| `max_cost` | The bound on the whole flat fetch: `max_cost_per_attempt` accumulated `max_attempts` times |
| `max_cost_per_attempt` | One attempt's bound, with the entire output allowance reserved at the model's output rate |
| `max_attempts` | The attempts a fetch is allowed, which is what `--limit-calls` and `--max-retries` move |
| `reason` | What is missing, when `bounded` is false |
| `model` | The model the figure was computed against |
| `prompt_bytes` | The encoded length of the prompt this source would send |
| `max_output_tokens` | The output allowance the bound reserves |

Neither cost is rounded, because a figure a run can exceed is not a bound. Both are computed from this package's static price table rather than from anything the provider reports, so they agree with what a run reserves and not necessarily with an invoice.

```bash
blue-llmdb data estimate "the member states of the European Union" \
    --model gpt-4o --max-tokens 512 --limit-calls 2 --max-retries 1
```

It prices **one flat fetch**, so a split request costs the published figure once per call it makes.

Every option that appears on both `data query` and `data estimate` has the same name, type, default and meaning on both. `data estimate` refuses `--max-records`, `--effort` and every `--plan-*` option, because `estimate` reads no plan setting, and a setting accepted and ignored is exactly what the library's closed key set exists to prevent. The refusal names the flag and points at `data query`:

```text
ERROR: USAGE No such option '--max-records'. (Did you mean one of: '--limit-seconds', '--max-retries', '--max-tokens'?) `estimate` prices one flat fetch and reads no plan setting, so --max-records would change nothing here; it belongs to `blue-llmdb data query`.
```

## model

```bash
blue-llmdb model list
```

```text
┏━━━━━━━━━━━━━━━━━━━┓
┃ model             ┃
┡━━━━━━━━━━━━━━━━━━━┩
│ claude-3-5-haiku  │
│ claude-3-5-sonnet │
│ claude-3-opus     │
│ gpt-3.5-turbo     │
│ gpt-4             │
│ gpt-4-turbo       │
│ gpt-4o            │
│ gpt-4o-mini       │
│ o1                │
│ o3-mini           │
└───────────────────┘
```

Those are the models this package carries a published price for. Any model name the provider accepts can be used; these are the ones a cost ceiling can be bounded against without supplying rates of your own.

```bash
blue-llmdb model inspect gpt-4o
```

```text
┏━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━┓
┃ key                    ┃ value   ┃
┡━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━┩
│ model                  │ gpt-4o  │
│ input_price_per_token  │ 2.5e-06 │
│ output_price_per_token │ 1e-05   │
│ max_tokens             │ 4096    │
│ can_bound              │ True    │
└────────────────────────┴─────────┘
```

```bash
blue-llmdb model select gpt-4o
```

```text
Selected model: gpt-4o (profile default)
```

`model select` stores the name as the profile's `BLUE_DATA_LLM_MODEL`, so `--model` still overrides it for one request and `profile inspect` shows where it came from. Selecting a model with no published price warns and names the two price parameters, because a cost ceiling cannot be bounded against it until you supply them.

## Output formats

`--format table|json|nl|csv` is on every command that returns a result, and the default comes from `BLUE_DATA_CLI_FORMAT` when a profile sets one.

| Format | What it is | Use it for |
|---|---|---|
| `table` | A Rich table, with the accounting line and a `settings:` footer under it | Reading at a terminal |
| `json` | One object on standard output and nothing else | Piping into another program |
| `nl` | A short paragraph: how many records, from which model, at what cost, and whether anything stopped the run early | A glance, or a log line |
| `csv` | RFC 4180 with the same columns | A spreadsheet |

A CSV cell beginning `=`, `+`, `-` or `@` is quoted, because a spreadsheet reads those as formulas and these cells hold model output. Every format strips terminal control characters from every cell, for the same reason.

```bash
blue-llmdb data query "the member states of the European Union" --format json
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
    "elapsed_seconds": 0.31,
    "row_estimate": null,
    "cardinality_estimate": null,
    "primary_keys": [],
    "observed_key_count": 0,
    "marginal_key_yields": [],
    "incomplete_key_records": 0,
    "gaps": [],
    "stop_reasons": [],
    "requested_attrs": ["country", "accession_year"]
  },
  "settings": {
    "applied": {"llm.model": "gpt-4o", "plan.max_continuations": 0},
    "source": {"llm.model": "default", "plan.max_continuations": "default"}
  }
}
```

Three keys, and the same three every time.

`meta` always carries `outcome`, the accounting fields, `gaps`, `stop_reasons`, `requested_attrs`, `row_estimate`, `cardinality_estimate`, `primary_keys`, `observed_key_count`, `marginal_key_yields` and `incomplete_key_records`, even when the list is empty: a reader branches on what they say rather than on whether they arrived. `normalization_collisions`, `unchecked_types`, `record_origins`, `asked`, `stopped_by`, `truncated_to` and `cache_hits` appear when there is something to report, and `accounting_known` appears only when it is false. `--verbose` adds `unmapped_keys`, `remapped_keys`, `type_mismatches` and `key_collisions`, which carry arbitrary model output.

`settings` is this CLI's own envelope, and its scope is exactly what the CLI can know: the values this invocation resolved and passed to the library, and the tier each came from. It is not a report of the library's effective configuration. Keys are the library's own dotted names, so the same spelling appears here, in the profile file and in the MCP schema. A property this run did not set appears in neither map.

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

**Exit 2 belongs to Click's parser.** A misspelled option or a value of the wrong type is refused before any command body runs, which means before the CLI knows what `--format` you asked for. Those errors are always plain text on standard error, and they are the only ones that are. Everything the CLI refuses after the parse is, under `--format json`, also written to standard output as `{"error": {"code": ..., "message": ...}}`, the startup work included: an unknown `--profile`, a profiles file that cannot be read, a profile value that will not cast. One residue: if `BLUE_DATA_CLI_FORMAT` itself holds something that is not a format, the failure *is* the lookup that would have named the format, so with no `--format` on the command line there is nothing left to publish in and it reports as text.

A missing `OPENAI_API_KEY` exits 5, not 4. The library never reads a provider credential, so it cannot report one as missing; the provider SDK reports it as a call that failed, which becomes a `TransportError`. The message names the command that sets one:

```text
ERROR: TRANSPORT TransportError: litellm.AuthenticationError - Missing credentials. Set the key with `blue-llmdb profile config OPENAI_API_KEY`.
```

## Where to go next

- [quickstart.md](quickstart.md): one path through the CLI in under five minutes.
- [mcp.md](mcp.md): the same operations as MCP tools, for an agent rather than a shell.
- [guide.md](guide.md): the Python library the CLI is a thin adapter over.
- [`platform/llm-cli/README.md`](../platform/llm-cli/README.md): package-level usage and development notes.
