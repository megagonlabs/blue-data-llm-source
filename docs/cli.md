# CLI

`blue-llmdb` provides twelve commands across four components for configuration, model inspection, cost estimation, and record queries.

This page mirrors [`platform/llm-cli/README.md`](../platform/llm-cli/README.md), which owns the command-surface contract. [installation.md](installation.md) is the reference for the Conda steps repeated below.

## Upgrading from `llm`

Internal build 0.2.0.0 replaced the `llm` command. `llm fetch` is now `blue-llmdb data query`, and `llm info` is now `blue-llmdb metadata inspect`. The old entry point has no alias, so existing scripts must be updated. That release also added profiles, a configuration precedence chain, the `model` and `metadata` components, `data estimate`, four output formats, and coded errors. See [`CHANGELOG.md`](../CHANGELOG.md) for the complete migration notes.

## Install

From the checkout root, with `blue-data` beside it, use the root `.venv`:

```bash
python3 -m venv .venv
source .venv/bin/activate
./platform/llm-cli/scripts/install.sh
```

If `.venv` already exists, activate it and run only the installer. It installs `blue-data`, the library's `dev` and `llm` extras, and the CLI's `dev` extra. Alternatively, use Conda from the same checkout root:

Place the separate `blue-data` checkout beside `blue-data-llm-source`, under the same parent directory. From the `blue-data-llm-source` root, `../blue-data/lib` must exist. Create a Conda environment once:

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

If the environment already exists, start at `conda activate blue-llmdb`. You can also run `platform/llm-cli/scripts/install.sh` in an active environment; it looks for `blue-data` at `../blue-data/lib` and accepts `BLUE_DATA_PATH` for another location. [Installation](installation.md) covers both routes and `venv`.

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

`data query` is the only command that reaches a model, so it is the only one that needs a credential. Everything else runs against an empty profile on a machine with no key. On first use, commands normally create `.profiles` and `.selected.profile` under `~/.blue-llmdb/` (or `BLUE_DATA_LLM_SOURCE_HOME`). If that directory cannot be created, `metadata inspect`, `model list`, `model inspect`, and `data estimate` use empty in-memory defaults. Queries, profile writes, and commands with an explicit profile still require the profile home.

```bash
blue-llmdb --version
```

```text
blue-llmdb 1.0.0
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

This source queries a model's own knowledge and has no stored dataset, so it has no `database`, `schema`, import, or export commands. Use `--attr` to declare the fields in a query result and `--format csv` or `--format json` to write records to standard output. `metadata inspect` reports this source's protocol, variant, and library version; for example, `blue-userdb metadata` manages fields attached to stored user data.

## Configuration

Settings live in profiles under `~/.blue-llmdb`. Every command reads them, and the order is the same everywhere:

**command option > environment variable > profile > built-in default.** `BLUE_DATA_CLI_FORMAT` supplies the default when `--format` is omitted, and an explicit `--format` takes precedence.

`profile inspect` reports the winning tier as `environment`, `profile:<name>`, `default`, or `unset`. It cannot report `flag`, because a command option never reaches the resolver; it is applied on top of whatever the resolver returned.

```bash
blue-llmdb profile config BLUE_DATA_LLM_MODEL gpt-4o
```

```text
Set BLUE_DATA_LLM_MODEL in profile default
```

`profile config` has three forms. With no argument it walks the parameters a first run is asked for; with a parameter name it prompts for that one; with a name and a value it sets it outright. A new profile named with `profile config --profile NAME` is created only when a valid value is saved, so a refused parameter or a value that will not store leaves nothing behind.

To enter a negative value as the positional `VALUE`, put `--` before it so Click does not parse it as an option. Limits reject negative values after parsing.

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
│ default │ *        │ 3          │
│ staging │          │ 1          │
└─────────┴──────────┴────────────┘
```

`Configured` counts the parameters that profile sets, and `*` marks the selected one.

```bash
blue-llmdb profile select staging
blue-llmdb profile select default
blue-llmdb profile remove staging --confirm
```

`--confirm` skips the prompt. Answering no leaves the store unchanged and exits 0 because the removal was canceled without an error. Removing a profile deletes only that profile; it does not remove caches or other profiles. If the removed profile was selected, selection moves to `default` when available or to the first remaining profile, and the command reports the change. The last profile cannot be removed because selection must always name an existing profile.

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

Nothing blocks on a parameter being unset. "Asked on a first run" means `blue-llmdb profile config` with no argument walks those two, and nothing more. A missing credential surfaces only when `data query` reaches the provider. With no answered extraction response and no retained records, it normally raises a transport error; a cost or time ceiling can stop a retry after the failed attempt. If an extraction response has already been parsed, the result is partial with a gap for the failed part. A ceiling refusal before the first attempt is a stopped result.

Two settings come from the environment, not from a profile. `BLUE_DATA_LLM_SOURCE_HOME` moves the CLI home; an empty exported value is refused. `BLUE_DATA_PROFILE` picks the profile, and the root `--profile` overrides it. Either selector must name a profile in `.profiles`. When neither is set, the CLI reads `.selected.profile`, or uses `default` when that file is absent or empty.

Data, model, and metadata commands refuse a missing stored selection or implicit `default` with `ERROR: PROFILE` and exit 4 before running. The error names the missing profile and selection source. Profile commands remain available for recovery: run `profile list`, then `profile select NAME`, or create a profile with `profile add NAME` or a valid `profile config` write. This prevents missing profiles from silently dropping their configured limits.

```text
ERROR: PROFILE no such profile: 'ghost'. Run `blue-llmdb profile list` to see them, or `blue-llmdb profile add ghost` to make it.
```

### Storage and secrets

```
~/.blue-llmdb/            directory mode 0700
├── .profiles             INI, one [section] per profile, file mode 0600
├── .selected.profile     the selected profile name, mode 0600
└── cache/                the default llm.cache_dir
```

Every write goes to a temporary file in the same directory and is moved into place, so an interrupted run cannot leave a profiles file the next run refuses to parse. A complete write cannot either: a value holding a line break is refused, because the file is line-oriented and one pasted from a CRLF file would be read back as a separate setting. A path inside the home that is a symlink out of it is refused rather than followed. A home directory at 0755 is reported once and left as it is. A home that group or others can write to is refused, and so is a settings file that group or others can read or write: every command then exits 4 with `ERROR: PROFILE` naming the path and the mode it needs, until you fix it with `chmod 700` on the directory or `chmod 600` on the file. The CLI never changes an existing mode.

A secret is never accepted as a value on the command line, where it would land in shell history. `blue-llmdb profile config OPENAI_API_KEY` prompts with the input hidden. Enter `$MY_KEY` at that prompt to store a reference to an environment variable instead of the secret itself; `profile inspect` then shows `$MY_KEY (set)` or `$MY_KEY (unset)`, so you can see what to export.

The CLI redacts known credential values from errors, logs, reported model names, settings values, and result metadata. These fields can contain library or provider text. Redaction replaces an environment value held under a credential name, or text matching a credential shape, with `***`. A model name that is only a model name is unchanged. Malformed profile errors identify the error type and line number without quoting file contents.

Returned records remain intact, including values that resemble credentials. Handle records as model output. A cache path containing credential-shaped text can appear partly masked in the settings report.

A directly entered secret is stored as base64 behind an `enc:` prefix. **This is obfuscation, not encryption.** Anyone who can read the profile, including a copy in a backup or sync service, can decode the secret. On POSIX systems, the `0600` file mode restricts local access; copies need their own access controls. If the profile may be copied, enter a `$MY_KEY` reference to keep the secret out of it. Profile commands do not print stored secret values. Error and log output use the scoped redaction described above.

## data query

`data query` is the one command that reaches a model, so every example in this section needs a credential the provider SDK can read.

A blank or whitespace-only query exits 0 with `outcome: empty_query` and no provider call. Scripts that require records should check the outcome or record count as well as the exit code.

```bash
blue-llmdb data query "the member states of the European Union"
```

In the default flat mode, named attributes define the output columns. Non-flat planning with schema enrichment may add inferred columns. `NAME:TYPE` declares a type, and values that can be coerced to it are coerced; `int`, `float`, `bool`, and `date` are understood and converted. `string` (also `str`, `text`) is checked but not converted, so a declared string that arrives as a number is reported rather than stringified. Any other name is accepted, checks nothing, and is listed once under `unchecked_types`.

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
27 records | model gpt-6-luna | 1 call | 638 tokens | row estimate unknown | $0.0002 | 5.21s
settings: llm.cache_dir=~/.blue-llmdb/cache (default), llm.model=gpt-6-luna (default), plan.max_continuations=0 (default)
```

The footer under the rows is the accounting line, and the `settings:` line under it names the values this invocation resolved and the tier each came from.

Without a cost ceiling, a known provider charge may be reported. Under a cost ceiling, the ledger keeps at least each contacted attempt's reservation; when accounting is unknown, the footer's amount is a conservative ledger value, not measured spend. In JSON, check `meta.accounting_known` before using `meta.cost`.

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

`--model`, `--max-tokens`, `--reasoning-effort`, `--temperature`, `--timeout`, `--max-parallel`, `--cache`/`--no-cache`, `--cache-dir`, `--price-in` and `--price-out` set the library's `llm.*` settings of the same meaning, and `--variant` sets the source variant within the `llm` protocol. Each has a profile parameter, so a value you use every time belongs in the profile and a value you use once belongs on the command line. Each model the package knows has its own reasoning efforts, default effort and temperature rule, listed under [Supported models](../lib/README.md#supported-models). GPT-6 Luna defaults to medium reasoning and accepts `--temperature` only with `--reasoning-effort none`; GPT-4o sends temperature 0 and accepts no reasoning effort. A combination the model rules out is refused before anything is spent.

```bash
blue-llmdb data query "the member states of the European Union" \
    --model gpt-4o --max-tokens 512 --temperature 0 --no-cache
```

`llm.stream` has no option here. Streaming has no meaning for a command whose result is a parsed record set. The library refuses `llm.rate_limit` by name, and `--rate-limit` is refused here as `ERROR: USAGE` carrying the library's own reason.

To see a response-cache hit, run the same query twice with `--cache` in the same environment and cache directory:

```bash
blue-llmdb data query "the member states of the European Union" --attr country --cache
blue-llmdb data query "the member states of the European Union" --attr country --cache
```

The first run needs a credential and contacts the provider. When the second run hits the cache, its table footer reports `0 calls | 0 tokens | $0.0000`; JSON also reports `meta.cache_hits`. A cache hit still returns rows and must pass the row and time limits.

### Spending, time and call ceilings

A ceiling applies to one request. A profile supplies the default for each request. Every run prints which ceilings applied and where each came from, so neither fact has to be inferred.

`--limit-cost` is a dollar ceiling, `--limit-seconds` refuses a call past that age, `--limit-calls` counts retries, and `--max-retries` bounds the attempts one ask may make.

```bash
blue-llmdb data query "the member states of the European Union" \
    --model gpt-4o --limit-cost 0.50 --limit-seconds 30 --limit-calls 2 --max-retries 1
```

`--limit-cost` is claimed against a bound at the package or configured rates rather than an average-case estimate. The bound is reconciled once the call returns: each contacted attempt keeps at least its reservation, and a higher readable figure raises the charge as [the cost bound](../lib/README.md#the-cost-bound) explains. The reported charge is ledger accounting, not necessarily provider spend: check `meta.accounting_known` in JSON before treating `meta.cost` as measured. A request refused by this ceiling prints no records and exits 7, with `outcome: stopped_by_limit` and `stopped by: max_cost` under the table.

There is a floor a cost ceiling has to clear. The output-only floor is the whole allowance at the model's output rate, with any long-context output multiplier and regional uplift, and does not depend on the prompt. GPT-4o's default allowance is 4,096 output tokens, for a floor of about $0.041. The GPT-6 Luna default is 25,000 tokens, for an output-only floor of about $0.020625 including the long-context output multiplier and OpenAI's 10% regional-processing uplift. Ask for a smaller answer with `--max-tokens` rather than raising the ceiling, since the output allowance is what the floor is made of. A model this package carries no published price for needs both `--price-in` and `--price-out` before a cost ceiling can be used. Without those rates, the command exits 4 with a `ConfigError` naming what is missing.

### Decomposition

One model call answers a request by default. A large ask can be split into parts, each asked separately and merged on the way back, which costs a call per part and is never chosen for you.

`--plan-mode` selects the shape: `flat` asks once and is the default, `partitioned` asks the whole request first and splits it, recursively, only when that answer falls short, and `row_by_row` asks per entity. `--plan-key` names the attribute that identifies a record, so parts returning the same record merge rather than duplicating it; it has to be one of the attributes you asked for.

```bash
blue-llmdb data query "the member states of the European Union" \
    --attr name --attr capital --plan-key name --plan-continuations 1
```

`--plan-depth` bounds recursion, `--plan-partitions` bounds children per node, and `--plan-target-rows` supplies the uncertain row-count threshold for descendant splits. `--plan-leaves` caps the number of leaf parts; a split that would exceed it is refused and its part is asked whole. `--plan-partition-key` guides only the root proposal. `--plan-schema-mode` takes `preserve`, which keeps caller attributes, or `enrich`, which lets non-flat planning append inferred ones.

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

When the library detects a limit or interrupted part, the CLI reports it: an `outcome:` line whenever the outcome is anything but `ok`, a `gap:` line per diagnostic, and the same facts under `outcome` and `gaps` in the machine formats. The outcome is the general statement (`empty_query`, `model_returned_none` and `filtered_to_empty` are three different empty results), and the gaps and stop reasons refine it. An apparently short answer may have no diagnostic. No gap means that no supported operational diagnostic fired; it does not establish completeness.

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

`data estimate` prices a request without contacting a provider, so it runs with no credential present. On a profile with no ceiling and no ceiling set in the environment, the output is:

```bash
blue-llmdb data estimate "the member states of the European Union" --model gpt-4o --format nl
```

```text
One flat fetch of this request is bounded at $0.1293 against the configured rates for gpt-4o, across 3 attempt(s). Non-flat requests may make planning and extraction calls with different prompts; this figure does not bound their total cost. No ceiling was set.
```

If you followed the Configuration steps above, the default profile still has its $0.50 ceiling, so the same command reports `At this ceiling 3 of 3 attempts fit.` and ends with `Ceilings applied: max_cost 0.5.` Add `--limit-cost` to set that ceiling explicitly:

```bash
blue-llmdb data estimate "the member states of the European Union" --model gpt-4o --limit-cost 0.50 --format nl
```

```text
One flat fetch of this request is bounded at $0.1293 against the configured rates for gpt-4o, across 3 attempt(s). At this ceiling 3 of 3 attempts fit. Non-flat requests may make planning and extraction calls with different prompts; this figure does not bound their total cost. Ceilings applied: max_cost 0.5.
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
| `attempts_within_ceiling` | Attempts that fit under a cost ceiling at the per-attempt bound; `null` without a ceiling |
| `settings` | Resolved settings and where each came from |

An unpriced model still exits successfully with `bounded: false` and a `reason`; estimating does not contact the provider. Neither cost is rounded. Bounds use the package or configured rates and assume the provider honors the configured token allowance; they are not invoice predictions.

```bash
blue-llmdb data estimate "the member states of the European Union" \
    --model gpt-4o --max-tokens 512 --limit-calls 2 --max-retries 1
```

It prices **one flat fetch**, including retries. Non-flat runs may use different prompts for planning and extraction, so the estimate does not bound their total cost.

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
│ gpt-3.5-turbo     │
│ gpt-4             │
│ gpt-4-turbo       │
│ gpt-4.1           │
│ gpt-4.1-mini      │
│ gpt-4.1-nano      │
│ gpt-4o            │
│ gpt-4o-2024-05-13 │
│ gpt-4o-mini       │
│ gpt-5             │
│ gpt-5-mini        │
│ gpt-5-nano        │
│ gpt-5.4           │
│ gpt-5.4-mini      │
│ gpt-5.4-nano      │
│ gpt-5.5           │
│ gpt-5.6           │
│ gpt-5.6-luna      │
│ gpt-5.6-sol       │
│ gpt-5.6-terra     │
│ gpt-6-astra       │
│ gpt-6-luna        │
│ gpt-6-sol         │
│ o1                │
│ o3                │
│ o3-mini           │
│ o4-mini           │
└───────────────────┘
```

Those are the models this package carries a published price for. Any model name the provider accepts can be used; these are the ones a cost ceiling can be bounded against without supplying rates of your own. [Supported models](../lib/README.md#supported-models) gives each one's reasoning efforts and prices.

```bash
blue-llmdb model inspect gpt-4o
```

```text
┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━┓
┃ key                                              ┃ value      ┃
┡━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━┩
│ model                                            │ gpt-4o     │
│ input_price_per_token                            │ 2.5e-06    │
│ output_price_per_token                           │ 1e-05      │
│ max_tokens                                       │ 4096       │
│ can_bound                                        │ True       │
│ published_defaults.reasoning_efforts             │            │
│ published_defaults.default_reasoning_effort      │            │
│ published_defaults.temperature                   │ always     │
│ published_defaults.output_token_parameter        │ max_tokens │
│ published_defaults.cached_input_price_per_token  │ 1.25e-06   │
│ published_defaults.cache_write_price_per_token   │            │
│ published_defaults.long_context_threshold_tokens │            │
└──────────────────────────────────────────────────┴────────────┘
```

The `published_defaults.*` rows are the model's entry in the package's table: the reasoning efforts it accepts and its default, when it accepts a temperature (`always`, `effort_none_only` or `never`), the output-token parameter, the cached-input and cache-write rates, and the long-context threshold. They describe the model; a profile setting or a flag overrides them for a query. The `nl` format reports a rounded output-only floor using the published rates, long-context output multiplier and regional uplift. Prompt tokens and higher configured rates can raise the bound.

```bash
blue-llmdb model select gpt-4o
```

```text
Selected model: gpt-4o (profile default)
```

`model select` stores the name as the profile's `BLUE_DATA_LLM_MODEL`, so `--model` still overrides it for one request and `profile inspect` shows where it came from. Selecting a model with no published price warns and names the two price parameters, because a cost ceiling cannot be bounded against it until you supply them.

## Output formats

`--format table|json|nl|csv` is on every command that returns a result. The default comes from `BLUE_DATA_CLI_FORMAT`; an explicit option wins. These formats suit this source's results: CSV writes record rows, while `nl` summarizes the answer and its accounting details.

| Format | What it is | Use it for |
|---|---|---|
| `table` | A Rich table, with the accounting line and a `settings:` footer under it | Reading at a terminal |
| `json` | One object on standard output and nothing else | Piping into another program |
| `nl` | A short paragraph: how many records, from which model, at what cost, and whether anything stopped the run early | A glance, or a log line |
| `csv` | RFC 4180 with the same columns | A spreadsheet |

A CSV cell beginning `=`, `+`, `-` or `@` receives a leading apostrophe before CSV encoding because a spreadsheet may read it as a formula. Every format strips terminal control characters from every cell.

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

Three keys, and the same three every time.

`meta` always carries `outcome`, the accounting fields, `gaps`, `stop_reasons`, `requested_attrs`, `row_estimate`, `cardinality_estimate`, `primary_keys`, `observed_key_count`, `marginal_key_yields`, `incomplete_key_records` and `filter_contradictions`, even when the list is empty: a reader branches on what they say rather than on whether they arrived. `normalization_collisions`, `repaired_numbers`, `split_decision`, `unchecked_types`, `record_origins`, `asked`, `stopped_by`, `truncated_to` and `cache_hits` appear when there is something to report, and `accounting_known` appears only when it is false. `--verbose` adds `unmapped_keys`, `remapped_keys`, `type_mismatches` and `key_collisions`, which carry arbitrary model output.

In a planned call with no `--attr`, `requested_attrs` remains empty even if the planner inferred columns. Inspect the returned records and diagnostics; this field records the caller's request, not the resolved schema.

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

`CONFIG` and `PROFILE` share exit 4 and are told apart by the code token. Separate statuses for query, provider, parsing, budget, and partial-result outcomes let scripts decide whether to correct input, retry, or use returned records. Empty and truncated results exit 0 because both are valid results; their details are in the output metadata.

**Exit 2 belongs to Click's parser.** A misspelled option, bad value, or missing argument exits 2. On a command that accepts `--format`, `--format json` or a valid `BLUE_DATA_CLI_FORMAT=json` default also writes `{"error": {"code": "USAGE", "message": ...}}` to standard output if parsing fails. The last explicit `--format` flag wins over the default; a flag after `--` is treated as an argument. The standard error line remains. A profile default is available only after the root callback exports it. Root-option parse failures can use an already exported environment default, but cannot use a profile-only default; pass `--format json` on the selected command when you need JSON for those failures. Errors after parsing use the same JSON envelope when the selected format is already known, including an unknown `--profile`, an unreadable profiles file, or a profile value that will not cast. Before a profile can be read, startup errors use an explicit format flag or an already exported environment default. Commands without `--format` report errors only on standard error. If `BLUE_DATA_CLI_FORMAT` itself holds an invalid value and no format flag is given, that lookup also reports only on standard error.

The provider SDK detects a missing `OPENAI_API_KEY` during a call; the library masks credential-shaped diagnostic text. With no answered extraction response and no retained records, exhausted retries or a `max_calls` retry refusal produce `TRANSPORT` with exit 5. If `max_cost` or `max_seconds` refuses the retry, the command reports `stopped_by_limit` and exits 7. A ceiling refusal before the first attempt also exits 7. If an extraction response has already been parsed, the result is partial with a gap for the failed part. The SDK-specific error prefix can vary by LiteLLM version. Set the key with `blue-llmdb profile config OPENAI_API_KEY` or export `OPENAI_API_KEY` in the process environment. With `--limit-calls 1`, the fixed retry-stop text for a request with no answered extraction is:

```text
ERROR: TRANSPORT TransportError: Provider request failed; max_calls stopped retries.; limits also stopped work: max_calls
```

## Where to go next

- [quickstart.md](quickstart.md): a guided path through the CLI.
- [mcp.md](mcp.md): the same operations as MCP tools, for an agent rather than a shell.
- [guide.md](guide.md): the Python library the CLI is a thin adapter over.
- [`platform/llm-cli/README.md`](../platform/llm-cli/README.md): package-level usage and development notes.
