# Quick Start

Use the command line to inspect the installation, estimate cost, configure a provider, and query for records. `blue-data` and `blue-data-llm-source` are separate checkouts under the same parent directory:

```text
workspace/
├── blue-data/
└── blue-data-llm-source/
```

Run the commands below from `blue-data-llm-source` with Python 3.12; the packages declare Python 3.12 and 3.13 (`>=3.12,<3.14`); `../blue-data/lib` points to the sibling checkout. [installation.md](installation.md) covers the installers, optional extras, and the Conda alternative.

Create a new environment with the explicit Python 3.12 executable. If `.venv` already exists, activate it and check `python --version` before reusing it.

```bash
ls -d ../blue-data/lib &&
test ! -e .venv &&
python3.12 -m venv .venv
```

Activate it and install the packages in dependency order. The chain stops if activation or an install fails.

```bash
source .venv/bin/activate &&
python --version &&
python -m pip install -e ../blue-data/lib &&
python -m pip install -e "./lib[llm]" &&
python -m pip install -e ./platform/llm-cli
```

If the packages are already installed in this environment, activate it and continue at step 1.

## 1. Check what you installed

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

## 2. See which models a cost ceiling can be bounded against

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

Each of these carries its own request contract and prices; [Supported models](../lib/README.md#supported-models) lists them.

## 3. Choose one

```bash
blue-llmdb model select gpt-4o
```

```text
Selected model: gpt-4o (profile default)
```

## 4. Price the request before you run it

`data estimate` contacts nothing, so this costs nothing and works without a key.

```bash
blue-llmdb data estimate "the member states of the European Union" --format nl
```

```text
One flat fetch of this request is bounded at $0.1293 against the configured rates for gpt-4o, across 3 attempt(s). Non-flat requests may make planning and extraction calls with different prompts; this figure does not bound their total cost. No ceiling was set.
```

## 5. Store the provider key, out of shell history

The prompt hides what you type and writes it to `~/.blue-llmdb/.profiles` at mode `0600`. Enter `$OPENAI_API_KEY` instead of the key to store a reference to the environment variable.

```bash
blue-llmdb profile config OPENAI_API_KEY
```

## 6. Check what is set, and where each value came from

```bash
blue-llmdb profile inspect
```

## 7. Ask for records (needs `OPENAI_API_KEY`)

Name the attributes you want and you get those columns; `NAME:TYPE` declares a type that values are checked against.

```bash
blue-llmdb data query "the member states of the European Union" \
    --attr country --attr accession_year:int --limit-cost 0.50
```

This example shows the output format. Records, token use, charge, and elapsed time vary by run.

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
27 records | model gpt-4o-2024-08-06 | 1 call | 638 tokens | row estimate unknown | $0.0431 (unknown) | 7.56s
settings: limits.max_cost=0.5 (flag), llm.cache_dir=~/.blue-llmdb/cache (default), llm.model=gpt-4o (profile:default), plan.max_continuations=0 (default)
```

The settings footer lists each resolved value and its source. `llm.cache_dir` is your own path; the other values came from your settings or the package defaults.
`unknown` means the charge is a conservative ledger value, at least the per-attempt reservation under a cost ceiling. It is not measured spend or an invoice amount; the provider's invoice may differ. The same rule applies to the JSON result's `meta.cost` when `meta.accounting_known` is `false`.

## 8. Take it somewhere else (needs `OPENAI_API_KEY`)

```bash
blue-llmdb data query "the member states of the European Union" --attr country --format json
blue-llmdb data query "the member states of the European Union" --attr country --format csv > eu.csv
```

---

## Using the library directly

The command line is a thin adapter over the Python library. `llm_fn` can be any `(prompt) -> str` callable, allowing the same request to run offline against a fixed answer. Omit it to use the configured model and a real provider.

```python
from blue_data_llm_source import LlmSource

source = LlmSource()
result = source.fetch(
    "the member states of the European Union",
    attrs=[
        {"name": "country", "type": "string"},
        {"name": "accession_year", "type": "int"},
    ],
    llm_fn=lambda prompt: '[{"country": "France", "accession_year": "1958"}]',
)
for record in result.records:
    print(record)
print(result.meta.outcome, result.meta.calls, result.meta.stop_reasons)
```

```text
{'country': 'France', 'accession_year': 1958}
ok 1 []
```

See [guide.md](guide.md) for the library task by task, and [api-reference.md](api-reference.md) for every signature.

---

## Connecting an agent

In the same environment, from the checkout root:

```bash
./platform/llm-mcp/scripts/install.sh
command -v blue-data-llm-mcp
```

Use the printed path as `command` below. Give the server `OPENAI_API_KEY` through the agent client's secret or inherited environment settings. Set an operator ceiling before providing the key:

```json
{
  "mcpServers": {
    "blue-data-llm": {
      "command": "/absolute/path/to/blue-data-llm-mcp",
      "env": {"BLUE_DATA_LLM_SOURCE_LIMIT_COST": "0.50"}
    }
  }
}
```

The operator ceiling applies to each tool call. It does not cap total spend across a sequence of calls. See [mcp.md](mcp.md) for the five tools and their settings.

---

## Next steps

- [features.md](features.md): everything the library does, one short example each.
- [cli.md](cli.md): every `blue-llmdb` command, profiles, formats, and exit codes.
- [architecture.md](architecture.md): where the money goes and which component holds what.
- [installation.md](installation.md): every install route and the optional extras.
