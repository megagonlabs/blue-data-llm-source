# Quick Start

Records out of a language model, from a shell, in under five minutes. See [installation.md](installation.md) if you have not installed anything yet.

```bash
lib/scripts/install.sh
platform/llm-cli/scripts/install.sh
```

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
│ version  │ 2.0.0  │
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

## 3. Choose one

```bash
blue-llmdb model select gpt-4o
```

```text
Selected model: gpt-4o (profile default)
```

## 4. Store the provider key, out of shell history

The prompt hides what you type and writes it to `~/.blue-llmdb/.profiles` at mode `0600`. Enter `$OPENAI_API_KEY` instead of the key to store a reference to the environment variable.

```bash
blue-llmdb profile config OPENAI_API_KEY
```

## 5. Check what is set, and where each value came from

```bash
blue-llmdb profile inspect
```

## 6. Price the request before you run it

`data estimate` contacts nothing, so this costs nothing and works without a key.

```bash
blue-llmdb data estimate "the member states of the European Union" --format nl
```

```text
One flat fetch of this request costs at most $0.1293 against gpt-4o, across 3 attempt(s). A split request costs that figure once per call it makes. No ceiling was set.
```

## 7. Ask for records (needs `OPENAI_API_KEY`)

Name the attributes you want and you get those columns; `NAME:TYPE` declares a type that values are checked against.

```bash
blue-llmdb data query "the member states of the European Union" \
    --attr country --attr accession_year:int --limit-cost 0.50
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
27 records | model gpt-4o-2024-08-06 | 1 call | 638 tokens | row estimate unknown | $0.0051 | 5.06s
settings: limits.max_cost=0.5 (flag), llm.cache_dir=~/.blue-llmdb/cache (default), llm.model=gpt-4o (default), plan.max_continuations=0 (default)
```

The settings footer names every value this run resolved and the tier it came from. `llm.cache_dir` is your own path; the rest is what you passed or what the package defaults to.

## 8. Take it somewhere else (needs `OPENAI_API_KEY`)

```bash
blue-llmdb data query "the member states of the European Union" --attr country --format json
blue-llmdb data query "the member states of the European Union" --attr country --format csv > eu.csv
```

---

## Using the library directly

The command line is a thin adapter over the Python library. `llm_fn` is any `(prompt) -> str` callable, so the same request runs offline against a fixed answer; omit it to use the configured model and a real provider.

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

```bash
platform/llm-mcp/scripts/install.sh
```

```json
{
  "mcpServers": {
    "blue-data-llm": {
      "command": "blue-data-llm-mcp",
      "env": {"OPENAI_API_KEY": "<YOUR_PROVIDER_KEY>"}
    }
  }
}
```

`<YOUR_PROVIDER_KEY>` is a placeholder. See [mcp.md](mcp.md) for the five tools and what a per-call ceiling does and does not cover.

---

## Next steps

- [features.md](features.md): everything the library does, one short example each.
- [cli.md](cli.md): every `blue-llmdb` command, profiles, formats, and exit codes.
- [architecture.md](architecture.md): where the money goes and which component holds what.
- [installation.md](installation.md): every install route and the optional extras.
