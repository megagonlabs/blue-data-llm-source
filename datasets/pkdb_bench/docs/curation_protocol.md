# PKDB-Bench Curation Protocol

**Version:** 0.4 (added agent-assisted collection policy; consolidated storage state)
**Purpose:** Define how new ground-truth table instances are collected, validated, and registered
for PKDB-Bench — the benchmark for evaluating LLM parametric knowledge extraction across 15
query-type categories.

This document is self-contained. A reviewer unfamiliar with the project history can start here.

---

## 1. What this benchmark is

**PKDB-Bench** evaluates whether an LLM can reconstruct structured tables from its own parametric
(training-time) knowledge, without access to retrieval or external context at inference time.

Each benchmark instance is a **(query, ground-truth table)** pair, where:

- The **query** is a natural-language description of a table the LLM should produce.
- The **ground-truth table** is a CSV built from an authoritative external source.
- The LLM under evaluation sees only the query. Its output is scored against the ground truth.

The benchmark covers **15 query categories**, each mapped to a SQL/DB analogue, ranging from
simple single-entity lookups to multi-hop reasoning and qualitative filtering.

The existing **10-table seed split** (from `multisources_dataset_v1`) is already part of
PKDB-Bench. This protocol governs the addition of new instances beyond those 10 tables.

---

## 2. Directory layout

```text
datasets/pkdb_bench/
├── cfg.json                  ← metadata registry (one entry per table instance)
└── tables/
    └── <table_id>.csv        ← ground-truth CSV for each registered instance

datasets/build_benchmark/
├── sparql_to_csv.py          ← pipeline for Wikidata SPARQL → CSV
└── sparql_queries/
    └── *.sparql              ← reusable SPARQL query files

datasets/pkdb_bench/docs/
├── README.md                 ← benchmark overview and current status
├── curation_protocol.md      ← this file
├── categories.txt            ← full 15-category taxonomy table
├── calibration_metrics.md    ← metric design (ECE, composite score, AUSF1)
└── papers.md                 ← related-work survey
```

### Current storage state

`datasets/pkdb_bench/` is the authoritative benchmark package.
`cfg.json` and `tables/` are synchronized: every registered entry has a co-located CSV.

The 12 currently registered instances are:

- 10 `trial_*` tables from the `multisources_dataset_v1` seed split
- `landlocked_countries` — Wikidata SPARQL, `set_filter`
- `nobel_counts_per_country` — Wikidata SPARQL, `aggregation`

Four additional SPARQL prototype CSVs exist in `tables/` but are **not yet registered** because
they require remediation before they meet the quality criteria in Section 5:

| File | Issue |
| ---- | ----- |
| `drive_left.csv` | Single column only — no non-key attribute (fails §5.1 table-shape criterion) |
| `large_cities_by_continent.csv` | Severe population-value duplicates from multiple Wikidata sources (e.g., Hanoi appears 7×) |
| `nobel_physics_and_chemistry.csv` | Contains raw Wikidata entity QIDs instead of resolved labels |
| `france_germany_border_non_french_german.csv` | Only 4 rows (below 10-row minimum in §5.2) |

These four CSVs are left in place as construction artifacts. Do not reference them in new
evaluation runs. Fix or re-generate via the SPARQL pipeline before registering them.

---

## 3. The 15 query categories

Each new instance must be assigned exactly one primary category from this list.

| ID | Category | DB Analogue | Answer type |
| -- | -------- | ----------- | ----------- |
| 1 | `factual_lookup` | `SELECT attr WHERE id=X` | Crisp |
| 2 | `set_filter` | `SELECT * WHERE predicate` | Crisp or anchored-fuzzy |
| 3 | `implicit_join` | `A JOIN B ON shared_key` | Crisp |
| 4 | `aggregation` | `COUNT / MAX / GROUP BY` | Crisp |
| 5 | `top_k` | `ORDER BY col DESC LIMIT K` | Crisp |
| 6 | `set_operation` | `INTERSECT / EXCEPT / UNION` | Crisp |
| 7 | `multi_hop` | Recursive CTE / chained joins | Crisp |
| 8 | `qualitative` | Fuzzy `WHERE` | Anchored-fuzzy |
| 9 | `temporal` | `WHERE year BETWEEN x AND y` | Crisp |
| 10 | `geographic` | Spatial predicates | Anchored-fuzzy |
| 11 | `causal` | Derived / computed relation | Anchored-fuzzy |
| 12 | `comparative` | Computed comparison column | Crisp |
| 13 | `hierarchical_decomposition` | Partitioned nested-loop join | Crisp |
| 14 | `multi_attribute_projection` | Multi-column `SELECT` | Crisp |
| 15 | `scaled_enumeration` | Partitioned `TOP K` over large universe | Crisp + partial |

**Crisp:** the ground-truth answer is objectively verifiable from the source.
**Anchored-fuzzy:** the answer is fuzzy in the abstract, but becomes crisp once anchored to a
named authoritative source (e.g., "politically unstable" → Fragile States Index 2024 tier ≥ Alert).

---

## 4. Ground truth source tiers

New instances must use one of four approved source tiers, listed in order of preference.

### Tier A — Wikidata SPARQL

Use the existing `datasets/build_benchmark/sparql_to_csv.py` pipeline. Query the Wikidata
SPARQL endpoint and write a clean CSV.

**Best for:** `aggregation`, `set_filter` (crisp), `top_k`, `multi_hop`, `comparative`,
`factual_lookup`, `temporal` (historical).

**Requirements:**

- Provide the `.sparql` file in `datasets/build_benchmark/sparql_queries/`.
- Run via `sparql_to_csv.py` to ensure consistent normalization.
- Record `gt_construction: "sparql_wikidata"` in `cfg.json`.

**Example:**

```sparql
# countries_landlocked.sparql
SELECT ?countryLabel WHERE {
  ?country wdt:P31 wd:Q6256 .        # instance of: country
  ?country wdt:P361 wd:Q4018990 .    # part of: landlocked countries
  SERVICE wikibase:label { bd:serviceParam wikibase:language "en". }
}
ORDER BY ?countryLabel
```

### Tier B — Wikipedia scraping

Extract a table from one or more Wikipedia articles. This can be done manually (copy-paste +
clean) or via `pandas.read_html()` on the Wikipedia URL. Multi-source tables (JOIN semantics)
require merging from two or more articles.

**Best for:** `implicit_join`, `set_operation`, `multi_attribute_projection`,
`hierarchical_decomposition`, `temporal` (recent years), `scaled_enumeration`.

**Requirements:**

- Record each source Wikipedia URL in `source_urls` in `cfg.json`.
- Normalize values to match the column type conventions in Section 6.
- Record `gt_construction: "manual_wikipedia"`.
- Note the access date in `temporal_note` (Wikipedia articles change).

### Tier C — Web search (authoritative third-party source)

Find an authoritative index, report, or list via web search, then extract the table from that
source. This is the primary method for categories with fuzzy answers, because anchoring to a
named external source makes the answer crisp.

**Best for:** `qualitative`, `geographic`, `causal`, `scaled_enumeration` (citations / indices).

**Requirements:**

- The source must be an identifiable authoritative organization or publication
  (e.g., Fund for Peace, Freedom House, Köppen climate database, US Census Bureau, Numbeo,
  Nobel Prize committee records, Semantic Scholar citation counts).
- Record the source name, URL, and as-of date in `source_urls` and `temporal_note`.
- Record `gt_construction: "web_search_<source_name>"` (e.g., `"web_search_fragile_states_index"`).
- The query string shown to the LLM must **not** name the specific source, so the LLM is tested
  on whether its parametric knowledge agrees with that source — without being told which source
  defines the answer.

**Example — `causal` instance:**

- Source: Fund for Peace Fragile States Index 2024, tiers "Alert" and "High Alert"
- `table_title` (the LLM query): "For each continent, list countries considered to have become
  politically fragile or unstable since 2000."
- Ground truth: the Alert/High-Alert country list from FSI 2024.
- `temporal_note`: "FSI 2024 data; anchor source not disclosed to LLM."

**Example — `geographic` instance:**

- Source: US Census Bureau Pacific Division definition
- `table_title`: "List the US states considered part of the Pacific Northwest."
- Ground truth: {Washington, Oregon, Idaho} per Census Bureau Pacific division (or the broader
  informal definition — pick one and document it).

### Tier D — Human annotation (last resort)

Use only when no authoritative external source exists and the category is important enough to
include. Requires at least 3 independent annotators and majority-vote ground truth.

**Currently approved use:** None. Defer Tier D instances until annotation tooling is in place.

### Agent-assisted collection policy

LLM agents such as Claude Code or Codex **may be used to accelerate ground-truth collection**,
but only when they are acting as source-grounded curators rather than as parametric answer
generators.

| Approach | Allowed? | Conditions |
| ------- | -------- | ---------- |
| Agent uses tools to extract a table from a cited authoritative URL | Yes | Record the source URL and `temporal_note`; human spot-check before registration |
| Agent uses tools to merge or normalize data from multiple cited sources | Yes, with review | Record all source URLs; human verifies merge logic and sampled rows before registration |
| Agent generates the table from parametric knowledge alone, without authoritative external sources | No | This is circular ground-truth generation and is invalid for PKDB-Bench |
| Same model family used both for curation assistance and later evaluation | Yes, but disclose | Allowed only if the curation was source-grounded; note this as a benchmark limitation in papers or reports |

**Rationale:** grounded agentic collection is equivalent to using a fast research assistant who
finds, extracts, and cleans data from real sources. Parametric-only generation is invalid because
it measures whether an LLM agrees with itself rather than whether it agrees with reality.

**Audit trail requirement for agent-assisted collection:**

- Record every authoritative source URL used to build the table.
- Record the access date or snapshot date in `temporal_note`.
- Keep a short note on any non-trivial normalization or merge decisions.
- Human-review the final CSV before adding it to `cfg.json`.

---

## 5. Instance quality criteria

Every new instance must pass all of the following before being registered in `cfg.json`.

### 5.1 Query criteria

- [ ] **Unambiguous scope**: the query clearly defines which entities, attributes, and time
  period are in scope. A reader should be able to decide whether any given row belongs.
- [ ] **Source-agnostic phrasing**: the query does not name the ground truth source (e.g., say
  "countries considered politically unstable" not "countries on the FSI Alert tier").
- [ ] **LLM-knowable**: the LLM's parametric knowledge should plausibly cover the domain. Do
  not create instances about highly niche or post-cutoff topics where the LLM has near-zero
  coverage. (Low coverage is a valid result; zero coverage is a wasted instance.)
- [ ] **Table-shaped output**: the expected output is a rectangular table, not a scalar or a
  free-form list. It must have a clear primary key and at least one non-key attribute.

### 5.2 Ground truth criteria

- [ ] **Source is named and reachable**: the `source_urls` field points to a live, stable URL.
  For reports that may move, archive the URL (e.g., via web.archive.org) and note it.
- [ ] **Snapshot is dated**: `temporal_note` records when the data was collected.
- [ ] **Minimum row count**: at least 10 rows. Tables with fewer rows provide too little signal.
- [ ] **Maximum row count**: no hard cap, but tables over 500 rows should be justified (usually
  only for `hierarchical_decomposition` or `scaled_enumeration`).
- [ ] **Primary key is unique**: no duplicate rows in the primary key column(s).
- [ ] **Values are normalized**: follow the column type conventions in Section 6.

### 5.3 Category assignment

- [ ] **Primary category** is assigned from the 15-category list in Section 3.
- [ ] If the instance illustrates a second category incidentally, record it in a `secondary_category`
  field (optional). Do not use secondary categories to pad coverage counts.

### 5.4 Avoiding overlap with existing instances

- [ ] The new instance does not duplicate an existing table (same entities, same attributes,
  same time period).
- [ ] The new instance adds meaningful diversity: different domain, different structural pattern,
  or different difficulty profile from existing instances in the same category.

---

## 6. Column type conventions

All ground truth CSVs must use consistent value formats so the evaluator
(`experiments/evaluate_multisources_metrics.py`) can match them correctly.

| `column_type` | Format rule | Examples |
| ------------- | ----------- | -------- |
| `key` | Plain string, full name, no abbreviation | `"United States"` not `"US"` |
| `numeric` | Bare number, no commas, no units | `35552` not `"35,552 km²"` |
| `rank` | Integer | `1`, `2`, `3` |
| `categorical` | Consistent label, full name | `"Southern Asia"` not `"S. Asia"` |
| `text` | Free string, stripped of leading/trailing whitespace | `"Avogadro's law"` |
| `date` | ISO 8601 where possible | `"2024-03-15"` or `"2024"` for year-only |

For `key` columns that represent countries or territories: use the `country_converter` library
short name (same library used in the evaluator) to ensure consistent matching.

When a field would otherwise force an arbitrary single-label assignment for a
legitimate boundary case, prefer a `text` field with an explicitly documented
format over a misleading `categorical` label. For example, a river benchmark
may use `Continent(s): "Europe; Asia"` rather than forcing the Ural River into
only one continent bucket.

---

## 7. cfg.json schema

Every registered instance requires a complete entry in `datasets/pkdb_bench/cfg.json`.

```jsonc
{
  "<table_id>": {
    // ── Identity ──────────────────────────────────────────────────────────
    "id": "<table_id>",               // snake_case, unique, matches CSV filename
    "table_title": "...",             // the natural-language query shown to the LLM

    // ── Schema ────────────────────────────────────────────────────────────
    "columns": ["ColA", "ColB", ...], // ordered list of column names
    "primary_key": ["ColA"],          // one or more columns forming the PK
    "column_types": {                 // one entry per column
      "ColA": "key",
      "ColB": "numeric",
      ...
    },

    // ── Query classification ───────────────────────────────────────────────
    "query_category": "<category_id>",           // from the 15-category list
    "secondary_category": "<category_id>",        // optional
    "db_analog": "SELECT ... FROM ... WHERE ...", // SQL representation of the query

    // ── Structure ─────────────────────────────────────────────────────────
    // operation_type — use exactly one of these values.
    // Values currently in cfg.json (use these for new seed instances):
    //   "join"         — implicit join across two or more source tables
    //   "union"        — row-wise union of two or more source tables
    //   "intersect"    — intersection of two source sets
    //   "projection"   — single-source multi-column SELECT, no filter
    //   "union+filter" — union combined with a filter predicate
    // Planned values for future query categories (not yet in cfg.json):
    //   "filter"       — single-source with a WHERE predicate (set_filter, geographic)
    //   "aggregation"  — GROUP BY / COUNT / MAX over a source (aggregation category)
    "operation_type": "<one of the values above>",
    "source_count": 1,                // number of implicit source tables required

    // ── Domain and difficulty ──────────────────────────────────────────────
    "domain": "Geography",            // free-text domain label
    "temporal_sensitivity": "low|medium|high",
    // low    = stable facts unlikely to change (geography, history)
    // medium = updated periodically (population, rankings)
    // high   = changes frequently or post-cutoff (current events, prices)

    // ── Provenance ─────────────────────────────────────────────────────────
    "gt_construction": "sparql_wikidata|manual_wikipedia|web_search_<source>|human_annotation",
    "temporal_note": "Snapshot as of <date>; <any caveats>",
    "source_urls": [
      "https://..."
    ]
  }
}
```

If you introduce a boundary-aware field such as `Continent(s)`, the
`primary_key` should reflect the true row identity rather than the boundary
label itself. For example, if each river appears only once, `["River"]` may be
better than `["Continent(s)", "River"]`.

**Required fields:** all fields above except `secondary_category`.

---

## 8. Collection workflow

### Step 1 — Identify a candidate instance

Pick a query category with insufficient coverage (see the Registered/Gap columns in Section 9).
Draft a `table_title` (the LLM query string) that satisfies the query criteria in Section 5.1.

### Step 2 — Choose a ground truth source

Follow the tier preference order in Section 4 (A → B → C → D).
Confirm the source is reachable and covers the entities the query asks for.

### Step 3 — Build the ground truth CSV

**For Tier A (SPARQL):**

```bash
python datasets/build_benchmark/sparql_to_csv.py \
    --query datasets/build_benchmark/sparql_queries/<name>.sparql \
    --out datasets/pkdb_bench/tables/<table_id>.csv \
    --table_id <table_id>
```

**For Tier B (Wikipedia) and Tier C (web search):**

- Download or copy the source table.
- Apply column type normalization (Section 6).
- Save to `datasets/pkdb_bench/tables/<table_id>.csv`.
- If an LLM agent was used to assist with extraction or merging, follow the agent-assisted
  collection policy in Section 4 and preserve a minimal audit trail.

### Step 4 — Validate the CSV

Check all quality criteria from Section 5.2:

```bash
python -c "
import pandas as pd
df = pd.read_csv('datasets/pkdb_bench/tables/<table_id>.csv')
print(f'Rows: {len(df)}  Cols: {len(df.columns)}')
print(df.dtypes)
print(df.head(3))
print('PK dupes:', df.duplicated(subset=['<pk_col>']).sum())
"
```

### Step 5 — Register in cfg.json

Add a complete entry to `datasets/pkdb_bench/cfg.json` following the schema in Section 7.
Fill every required field. Write a `temporal_note` even for low-sensitivity tables.

### Step 6 — Validate the registration (CSV + schema sanity check)

> **Current evaluator limitation:** `experiments/evaluate_multisources_metrics.py` is not
> compatible with `datasets/pkdb_bench/cfg.json` for two reasons: (1) it reads `item['category']`
> but pkdb_bench uses `query_category`, causing a `KeyError` that is caught but produces no valid
> output for the table; (2) a newly added table will not have outputs in any existing run directory,
> so the evaluator skips it immediately. A dedicated `evaluate_pkdb_bench.py` is needed before
> end-to-end evaluation against pkdb_bench instances is possible.

**Until `evaluate_pkdb_bench.py` exists, Step 6 is a CSV and schema sanity check only.**

Run the following to confirm the new table is well-formed:

```python
import json, pandas as pd

TABLE_ID = "<table_id>"

# 1. Load cfg entry
with open("datasets/pkdb_bench/cfg.json") as f:
    cfg = json.load(f)
assert TABLE_ID in cfg, f"{TABLE_ID} not found in cfg.json"
item = cfg[TABLE_ID]

# 2. Load CSV
csv_path = f"datasets/pkdb_bench/tables/{TABLE_ID}.csv"
df = pd.read_csv(csv_path)

# 3. Schema checks
assert list(df.columns) == item["columns"], "Column mismatch"
assert df.duplicated(subset=item["primary_key"]).sum() == 0, "Duplicate PKs"
assert len(df) >= 10, f"Too few rows: {len(df)}"
assert all(f in item["column_types"] for f in item["columns"]), "column_types incomplete"

print(f"OK — {len(df)} rows, {len(df.columns)} cols, PK={item['primary_key']}")
print(df.head(3))
```

Full end-to-end evaluation (F1 scoring against an LLM run) is not yet supported for
pkdb_bench instances. Track the `evaluate_pkdb_bench.py` task in the next-steps checklist
in `README.md`.

---

## 9. Coverage targets

Target at least **3 instances per category** for the benchmark to be meaningful.

Coverage is tracked in two columns: **Registered** (in `cfg.json`, officially part of the
benchmark) and **Prototype** (CSVs exist in `pkdb_bench/tables/` but not yet registered).
Only the Registered count contributes to the Gap.

| Category | Registered | Target | Gap |
| -------- | ---------: | -----: | --: |
| `set_operation` | 6 | 6 | 0 |
| `implicit_join` | 2 | 4 | 2 |
| `multi_attribute_projection` | 2 | 4 | 2 |
| `aggregation` | 1 | 3 | 2 |
| `set_filter` | 1 | 3 | 2 |
| `top_k` | 0 | 3 | 3 |
| `multi_hop` | 0 | 3 | 3 |
| `temporal` | 0 | 3 | 3 |
| `hierarchical_decomposition` | 0 | 3 | 3 |
| `comparative` | 0 | 3 | 3 |
| `scaled_enumeration` | 0 | 3 | 3 |
| `qualitative` | 0 | 3 | 3 |
| `geographic` | 0 | 3 | 3 |
| `causal` | 0 | 3 | 3 |
| `factual_lookup` | 0 | 2† | 2 |
| **Total** | **12** | **49** | **37** |

† `factual_lookup` is the simplest category and mainly serves as a warm-up / sanity-check tier.
2 instances are sufficient.

Four SPARQL prototype CSVs exist in `datasets/pkdb_bench/tables/` but are not registered
(see Section 2 for the issues). They are not counted in the Registered column above.

**Priority order for new curation:**

1. Fix and register the 4 unregistered SPARQL prototype CSVs — quickest coverage gain once
   the data quality issues are resolved (see Section 2 for the issue per file).
2. SPARQL-automatable new instances: `top_k`, `multi_hop`, `comparative`, `hierarchical_decomposition`.
3. Wikipedia-manual: `temporal`, `implicit_join` extensions.
4. Web-search-anchored: `qualitative`, `geographic`, `causal`, `scaled_enumeration`.

---

## 10. Open design questions (for reviewer)

These questions are unresolved and input is welcome:

1. **`factual_lookup` inclusion**: Is this category worth including, or is it too trivial to
   provide meaningful signal? Arguments for: establishes a baseline difficulty floor. Arguments
   against: a single-cell lookup is not a table extraction task in any meaningful sense.

2. **Fuzzy category ground truth anchor disclosure**: The protocol says do not disclose the
   source to the LLM. Should the benchmark paper disclose it to the reader? We think yes —
   transparency about what "correct" means is important — but this needs to be agreed.

3. **Partial ground truth for `scaled_enumeration`**: For queries like "top 5 scientists per
   discipline," the LLM may produce valid answers for well-known entities that simply don't
   appear in the citation-index ground truth. Should we use a Tier C source that is incomplete
   (citation index misses many pre-digital-era scientists) or accept partial ground truth with
   a coverage cap?

4. **Row count lower bound**: The current minimum is 10 rows. Is this sufficient? A 10-row
   `set_filter` table (e.g., 10 landlocked countries in a region) gives much less signal than
   a 100-row `implicit_join` table.

5. **Temporal sensitivity and re-annotation**: High-sensitivity tables will drift from their
   ground truth over time. Should the benchmark define a re-annotation cadence (e.g., annually),
   or should each table be considered a static snapshot tied to a specific date?

6. **`secondary_category` field**: Is this field useful or does it create ambiguity in coverage
   reporting? Alternative: drop it and keep each instance assigned to exactly one category.
