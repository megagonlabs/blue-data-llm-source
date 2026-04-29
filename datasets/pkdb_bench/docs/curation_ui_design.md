# PKDB-Bench Curation UI — Design Spec

**Version:** 0.3
**Purpose:** Design document for the Streamlit-based curation UI that supports the
agent-assisted ground-truth collection workflow defined in `curation_protocol.md` §4.

---

## 1. Problem statement

The current curation workflow has three friction points that the conversation window
does not solve well:

1. **Table review** — a 50-row CSV rendered as markdown is hard to audit.
2. **cfg.json authoring** — protocol-driven metadata with enum and cross-field constraints is
   error-prone in chat.
3. **Iterative correction** — fixing individual rows requires going back and forth in chat
   with no persistent record of what was changed and why.

The UI addresses all three by combining an editable table grid, an embedded LLM chatbot,
and a structured registration form in a single surface.

---

## 2. Architecture

```text
Claude Code conversation          Shared filesystem              Streamlit app
─────────────────────────         ──────────────────             ─────────────
Agent extracts table,        →    datasets/pkdb_bench/      →   reads staging/,
writes staging files               staging/                      shows editable
                                   ├── <id>.csv                  table + chat,
                                   ├── <id>.draft.json           human edits
                                   └── <id>.comments.json        + approves
                                                             →   promotes to:
                                   datasets/pkdb_bench/
                                   ├── cfg.json  (updated)
                                   └── tables/<id>.csv (new)
```

**Principle:** Streamlit is the review and registration surface. The embedded chatbot
calls the Anthropic API directly from within the app. The Claude Code conversation
remains the primary extraction engine, but the chatbot handles iterative corrections
and validation queries without leaving the UI.

---

## 3. Staging file format

Claude Code writes three files per staged instance:

### `staging/<id>.csv`

Proposed ground-truth CSV, column-name-normalized, ready for human review.

### `staging/<id>.draft.json`

```jsonc
{
  "id": "<table_id>",
  "table_title": "...",
  "columns": ["ColA", "ColB"],
  "primary_key": ["ColA"],
  "column_types": { "ColA": "key", "ColB": "numeric" },
  "query_category": "<category_id>",
  "secondary_category": null,
  "db_analog": "SELECT ...",
  "operation_type": "<value>",
  "source_count": 1,
  "domain": "Geography",
  "temporal_sensitivity": "low",
  "gt_construction": "web_search_<source>",
  "temporal_note": "Snapshot as of <date>",
  "source_urls": ["https://..."],

  // Staging metadata — moved to review_log on registration, not discarded
  "_staged_by": "claude-code",
  "_staged_at": "2026-04-22T10:30:00Z",
  "_extraction_notes": "Merged two source tables; see temporal_note."
}
```

### `staging/<id>.comments.json`

Created and maintained by the UI. Stores per-cell doubt flags, free-text comments, and
a running chat summary. Written on every edit.

```json
{
  "doubts": [
    { "row": 4, "col": "Renewable Share (%)", "reason": "value seems too high" }
  ],
  "comments": [
    { "row": 11, "col": "Country", "text": "Verified against country_converter short name" }
  ],
  "chat_events": [
    {
      "at": "2026-04-22T11:05:00Z",
      "action": "table_update_applied",
      "summary": "Fixed PK duplicate in rows 3/4; row 3 kept, row 4 dropped.",
      "source_cited": "https://iea.org/reports/renewables-2023"
    }
  ]
}
```

### `datasets/pkdb_bench/review_logs/<id>.review.json`

Written by the UI on **Register**. This is the durable audit artifact — it survives
registration and is never deleted. The staging files are deleted after registration;
this file takes their place as the record of how the instance was curated.

```json
{
  "id": "<table_id>",
  "registered_at": "2026-04-22T12:00:00Z",
  "registered_by": "human",
  "staged_by": "claude-code",
  "staged_at": "2026-04-22T10:30:00Z",
  "extraction_notes": "Merged two source tables; see temporal_note.",
  "source_urls_at_registration": ["https://..."],
  "temporal_note_at_registration": "IEA 2023 data, accessed 2026-04-22",
  "chat_events": [ "... (copied from comments.json)" ],
  "resolved_doubts": [],
  "open_doubts_overridden": [],
  "comments": [ "... (copied from comments.json)" ]
}
```

The `review_logs/` directory is committed to version control alongside `cfg.json`
and `tables/`. It provides provenance for every registered instance.

---

## 4. Overall layout

The app has two top-level tabs: **Review** and **Coverage**.

```text
┌─ Sidebar ──────────────────────────────────────────────────────────────┐
│  🔑 API key      [sk-ant-...                    ] (password input)     │
│  Model           [claude-sonnet-4-6            ▾]                      │
│  ─────────────────────────────────────────────────────────────────     │
│  Instance        [renewable_energy_top20       ▾]  2 pending           │
│  ─────────────────────────────────────────────────────────────────     │
│  cfg.json form                                                          │
│  id              renewable_energy_top20                                 │
│  table_title     [List the top 20 countries by…]                       │
│  query_category  [top_k                        ▾]                      │
│  secondary_cat   [— none —                     ▾]                      │
│  db_analog       [SELECT country, share FROM …]                        │
│  operation_type  [filter                       ▾]                      │
│  source_count    [1  ]                                                  │
│  domain          [Energy                       ]                       │
│  temporal_sens   [medium                       ▾]                      │
│  gt_construction [web_search_iea               ]                       │
│  temporal_note   [IEA 2023 data, accessed 2026…]                       │
│  source_urls     [https://iea.org/...          ]  [+ add]              │
│  ─────────────────────────────────────────────────────────────────     │
│  Extraction notes (read-only):                                          │
│  "Merged rows from two source tables."                                  │
└────────────────────────────────────────────────────────────────────────┘

┌─ Tab: Review ────────────────────────────────────────────────────────────────────────────────┐
│                                                                                              │
│  ┌─ Editable table (55%) ──────────────────────────┐  ┌─ Chat (45%) ──────────────────────┐ │
│  │                                                  │  │                                   │ │
│  │  ⚠ 1 doubt flagged   💬 2 comments              │  │  [AI]: Staged 20 rows from IEA.   │ │
│  │  [ Filter rows... ]  [ Reset to staged ]         │  │  PK unique. Row 7: "Viet Nam"     │ │
│  │                                                  │  │  should be "Vietnam" per          │ │
│  │  Country         Share(%) ⚑  💬                 │  │  country_converter short_name.    │ │
│  │  ─────────────── ──────── ── ──                 │  │                                   │ │
│  │  Iceland         99.9     □   □                 │  │  [User]: Apply the fix             │ │
│  │  Norway          98.5     □   □                 │  │                                   │ │
│  │  ⚠ Costa Rica   [74.1  ] ■   □   ← editing     │  │  [AI]: Source: country_converter  │ │
│  │  Denmark         80.3     □   💬 ← has comment  │  │  v0.7 short_name. Apply?          │ │
│  │  ...             ...      □   □                 │  │                                   │ │
│  │                                                  │  │  [ Apply table update ]           │ │
│  │  [ + Add row ]  [ Delete selected ]             │  │                                   │ │
│  │                                                  │  │  ─────────────────────────────── │ │
│  │  Validation:                                     │  │  Quick actions:                   │ │
│  │  ✓ 20 rows  ✓ PK unique  ⚠ date in note         │  │  [ Fix PK dupes       ]           │ │
│  └──────────────────────────────────────────────────┘  │  [ Normalize columns  ]           │ │
│                                                         │  [ Summarize source   ]           │ │
│                                                         │  [ Suggest db_analog  ]           │ │
│                                                         │  [ Check LLM-knowable ]           │ │
│                                                         │                                   │ │
│                                                         │  [Type a message...    ]  [Send]  │ │
│                                                         └───────────────────────────────────┘ │
│                                                                                              │
│  [ Reject (delete staging) ]              [ Save draft ]          [ Register → cfg.json ]   │
└──────────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 5. Editable table (left panel)

Built on `st.data_editor` (Streamlit ≥ 1.23).

### Columns rendered

| Column | Editable | Notes |
| ------ | -------- | ----- |
| Data columns (from CSV) | Yes | Free-text or numeric depending on `column_types` |
| `⚑` (doubt flag) | Yes | Checkbox; row highlighted amber when checked |
| `💬` (comment) | Click to open | Opens a popover with a text input; saved to `.comments.json` |

### Row-level interactions

- **Edit a cell** — click directly; change is held in `st.session_state`, not written to
  disk until Save draft or Register.
- **Flag a doubt** — check the `⚑` column. The row turns amber. The doubt is saved to
  `.comments.json` immediately so it survives page reloads.
- **Leave a comment** — click the `💬` icon. A popover opens with a free-text input and
  a save button. Comment keyed by `(row_index, column_name)`.
- **Add / delete rows** — `[ + Add row ]` appends a blank row; select rows and click
  `[ Delete selected ]` to remove.
- **Reset to staged** — discards all in-session edits and reloads from the staging CSV.
- **Filter** — a text input above the table filters visible rows by any column value.

### Doubt / comment display

Rows with a doubt flag show a persistent amber highlight. The header bar shows counts:
`⚠ 1 doubt flagged   💬 2 comments`. Hovering a `💬` icon shows the comment preview.

### Chat → table updates

When the chatbot proposes a table change it includes a fenced block:

```text
<!--table_update-->
Country,Renewable Share (%)
Iceland,99.9
...
<!--end_table_update-->
```

The UI detects this block, computes a diff against the current table, and shows a
mandatory diff summary before the Apply button is enabled:

```text
  Proposed change — 3 rows changed · 1 row added · 0 rows deleted
  ┌─────────────────────────┬──────────────────┬──────────────────┐
  │ Row / Column            │ Before           │ After            │
  ├─────────────────────────┼──────────────────┼──────────────────┤
  │ row 7 · Country         │ "Viet Nam"       │ "Vietnam"        │
  │ row 3 · Share (%)       │ 74.1             │ 74.6             │
  │ ...                     │                  │                  │
  └─────────────────────────┴──────────────────┴──────────────────┘
  [ Apply ]   [ Dismiss ]
```

The `[ Apply ]` button is disabled until the diff summary has been rendered and the user
has had at least 2 seconds to read it (enforced via `st.spinner` delay). Applying records
a `table_update_applied` event in `comments.json` with the source cited by the chatbot.
Existing doubt flags and comments on unmodified rows are preserved after apply.

For registration-safe provenance, a plain citation string is not enough. Every AI-proposed
table update must also include a structured evidence map for the changed rows/cells:

```text
<!--source_citation-->
{
  "sources": [
    {
      "id": "src1",
      "url": "https://iea.org/reports/renewables-2023",
      "locator": "Table 4.2"
    }
  ],
  "evidence_map": [
    {
      "row": 7,
      "column": "Country",
      "before": "Viet Nam",
      "after": "Vietnam",
      "source_id": "src1",
      "justification": "country_converter short_name normalization applied to source label"
    },
    {
      "row": 3,
      "column": "Share (%)",
      "before": 74.1,
      "after": 74.6,
      "source_id": "src1",
      "justification": "Corrected to match cited table value"
    }
  ]
}
<!--end_source_citation-->
```

The UI blocks `[ Apply ]` unless:

- a `<!--source_citation-->` block is present;
- it parses as valid JSON;
- every changed row/cell in the diff has a corresponding `evidence_map` entry; and
- every evidence entry resolves to a declared source.

The applied event written to `comments.json` stores both the diff summary and the parsed
evidence map so the registration audit trail can reconstruct why each factual change was made.

---

## 6. Chatbot (right panel)

### Connection

The chatbot calls the Anthropic API directly from Streamlit using the `anthropic`
Python SDK. The API key is entered in the sidebar and stored in `st.session_state`
only — never written to disk or logs.

Anthropic is the **initial supported chatbot backend** for v0.3. This is a product
implementation choice for the first UI version, not a benchmark-policy constraint.

### System prompt

```text
You are a curation assistant for PKDB-Bench, a benchmark for LLM parametric knowledge
extraction. You help validate and improve ground-truth tables before they are registered.

Current instance:
- id: {id}
- table_title: {table_title}
- query_category: {query_category}
- source: {source_urls}

Current table (CSV):
{csv_content}

Current cfg draft:
{draft_json}

Rules:
- Ground-truth must come from an authoritative external source, not from your
  parametric knowledge.
- When you propose ANY factual change to the table, you MUST cite the specific
  source (URL or document name + section) that justifies the change. Do not
  propose edits based solely on what you believe from training.
- When you propose a table change, output a structured <!--source_citation-->
  JSON block first. It must contain:
  - a `sources` list with source ids, URLs, and locators/sections when available
  - an `evidence_map` list mapping every changed row/cell to one source id and
    a short justification
  Then output the full updated CSV inside
  <!--table_update--> ... <!--end_table_update--> tags.
- When you propose a cfg field change, state the field name, new value, and
  the reason clearly.
- Flag any row you are uncertain about rather than silently guessing.
- For country/territory key columns, use country_converter short_name for
  canonical names. Never shorten to abbreviations.
```

The system prompt is rebuilt on every message to include the current table state.
This means the LLM always sees the latest edits, even mid-session.

The UI enforces structured source citation: if a `<!--table_update-->` block is present but
no valid `<!--source_citation-->` JSON block precedes it, the `[ Apply ]` button is disabled
and an error is shown. If the evidence map does not cover every changed row/cell in the diff,
Apply is also blocked.

### Quick action buttons

Each button sends a fixed prompt to the chat:

| Button | Prompt sent |
| ------ | ----------- |
| Fix PK dupes | "Identify all duplicate primary key values in the current table and propose a corrected version. Explain each resolution." |
| Normalize columns | "Check every column against the column_types in the cfg draft. Flag and fix any values that don't match the expected format (e.g., numbers with commas, date strings not in ISO 8601)." |
| Summarize source | "Summarize what the source URL(s) contain and whether the current table appears to accurately represent them." |
| Suggest db_analog | "Propose a SQL db_analog expression for this table based on its query_category and structure." |
| Check LLM-knowable | "Given the table_title and domain, assess whether a general-purpose LLM trained before 2025 would plausibly have parametric knowledge of this content. Flag any rows that are likely post-cutoff or highly niche." |

### Chat history

Stored in `st.session_state["chat_history"]` as a list of `{role, content}` dicts.
Persists for the session but is not saved to disk. A `[ Clear chat ]` button resets it.

A one-line summary of the most recent agent action is saved to
`staging/<id>.comments.json` under `chat_summary` so audit context survives page reloads.

---

## 7. cfg.json form (sidebar)

All enum fields use `st.selectbox` populated from constants defined in `curation_protocol.md`
so values are always valid and in sync with the protocol schema. The form is the canonical
source of enum constraints — not hardcoded strings in `app.py`.

| Field | Widget | Values / constraint |
| ----- | ------ | ------------------- |
| `id` | Text input (read-only) | From draft; not editable in UI |
| `table_title` | Text area | Free text |
| `query_category` | Selectbox | 15 category values from `categories.txt` |
| `secondary_category` | Selectbox | Same 15 values + "— none —" |
| `db_analog` | Text area | Free text |
| `operation_type` | Selectbox | `join`, `union`, `intersect`, `projection`, `union+filter`, `filter`, `aggregation` |
| `source_count` | Number input | Integer ≥ 1 |
| `domain` | Text input | Free text |
| `temporal_sensitivity` | Selectbox | `low`, `medium`, `high` (per protocol §7) |
| `gt_construction` | Text input | Free text (e.g., `sparql_wikidata`, `web_search_iea`) |
| `temporal_note` | Text area | Free text |
| `source_urls` | Repeatable text inputs | `[ + add URL ]` appends a new input |

Form changes are held in `st.session_state`. Clicking **Save draft** writes the updated
draft JSON to `staging/<id>.draft.json`. Clicking **Register** uses the current form
state as the final cfg entry.

---

## 8. Validation rules

Run live on every form or table change. Shown in the table panel footer and in the
bottom action bar. Errors block **Register**; warnings do not.

### Per-field checks

| Check | Level | Condition |
| ----- | ----- | --------- |
| Rows ≥ 10 | Error | `len(df) < 10` |
| Primary key unique | Error | `df.duplicated(subset=primary_key).sum() > 0` |
| CSV columns match cfg `columns` | Error | `list(df.columns) != columns` |
| `column_types` complete | Error | Any column missing from `column_types` |
| `id` not already in `cfg.json` | Error | `id` already registered |
| `id` matches staging filename | Error | `id` ≠ stem of the staging CSV |
| `source_urls` non-empty | Error | `source_urls == []` |
| `gt_construction` non-empty | Error | Field is blank |
| `temporal_note` contains a year | Warning | No 4-digit year in `temporal_note` |
| Open doubt flags | Warning | Any `⚑` rows still checked at registration time |
| Rows ≤ 500 | Warning | `len(df) > 500` (justify in `temporal_note`) |

### Cross-field consistency checks

| Check | Level | Condition |
| ----- | ----- | --------- |
| `source_count` matches `source_urls` | Error | `source_count != len(source_urls)` |
| `gt_construction` aligns with `source_urls` | Warning | `gt_construction` starts with `sparql_wikidata` but no `query.wikidata.org` URL, or starts with `web_search_` but URL is a Wikipedia domain |
| `operation_type` consistent with `query_category` | Warning | Combinations that are structurally inconsistent, e.g. `operation_type=aggregation` with `query_category=set_filter`, or `operation_type=join` with `query_category=aggregation` |
| `secondary_category` ≠ `query_category` | Error | Both fields set to the same value |

Cross-field warnings are shown with an explanation of the inconsistency so the curator
can decide whether it is a metadata error or a legitimate edge case.

### Staged vs. benchmark-ready

The UI distinguishes two promotion states:

- **Save draft** — persists the current form + table to `staging/`. No quality bar.
  Useful for work-in-progress: the instance is not part of the benchmark yet.
- **Register** — promotes to `cfg.json` + `tables/`. Requires all Errors to be clear.
  Warnings produce a confirmation checklist the user must acknowledge before proceeding:
  *"The following issues were found. Confirm you have reviewed each before registering."*

An instance with open doubt flags can be registered only after explicit confirmation.
The doubt flags and their resolutions (or the fact they were overridden) are recorded
in `review_logs/<id>.review.json` under `open_doubts_overridden`.

---

## 9. Coverage tab

```text
┌─ Tab: Coverage ─────────────────────────────────────────────────────────────┐
│                                                                              │
│  Registered: 11 / 82    Categories covered: 4 / 15    Staged: N pending    │
│                                                                              │
│  Category                  Registered  Target  Gap   Progress               │
│  ─────────────────────────────────────────────────────────────────          │
│  set_operation             6           8       2     ████░░                 │
│  implicit_join             2           8       6     ██░░░░                 │
│  multi_attribute_proj…     2           8       6     ██░░░░ ◄ gap target   │
│  aggregation               1           5       4     █░░░░░                 │
│  set_filter                0           5       5     ░░░░░░                 │
│  top_k                     0           5       5     ░░░░░░                 │
│  multi_hop                 0           5       5     ░░░░░░                 │
│  temporal                  0           5       5     ░░░░░░                 │
│  ...                                                                         │
│                                                                              │
│  ▶ top_k (0 registered)                                                      │
│    No instances yet. Suggested prompt for Claude Code:                       │
│    "Stage a top_k instance: top 20 countries by renewable energy share,      │
│    source from IEA or Our World in Data."                                    │
│    [ Copy prompt ]                                                           │
│                                                                              │
│  ▶ set_operation (6 registered)                                              │
│    trial_join_4  — films in both top-50 highest-grossing and top-50 budget  │
│    trial_union_1 — Academy Award Best Actor/Actress 1927–2024               │
│    ...                                                                       │
└──────────────────────────────────────────────────────────────────────────────┘
```

**Behavior:**

- Reads `cfg.json` and `staging/` on each page load (no caching).
- "Next target" marker on the category with the largest gap and no pending staged items.
- Expanding a category row shows all registered instances (id, table_title, source).
- Expanding a zero-registered category shows a suggested Claude Code prompt the
  user can copy to start a new extraction conversation.

---

## 10. File paths

| Path | Role |
| ---- | ---- |
| `datasets/pkdb_bench/staging/` | Staging directory; created on first use |
| `datasets/pkdb_bench/staging/<id>.csv` | Proposed ground-truth CSV |
| `datasets/pkdb_bench/staging/<id>.draft.json` | Proposed cfg entry (with `_*` metadata) |
| `datasets/pkdb_bench/staging/<id>.comments.json` | Per-cell doubt flags, comments, chat events |
| `datasets/pkdb_bench/cfg.json` | Live benchmark registry; updated on Register |
| `datasets/pkdb_bench/tables/<id>.csv` | Registered CSVs; written on Register |
| `datasets/pkdb_bench/review_logs/<id>.review.json` | Durable audit artifact; written on Register, never deleted |
| `curation_ui/app.py` | Streamlit entry point |
| `curation_ui/requirements.txt` | `streamlit`, `pandas`, `anthropic` |

---

## 11. Curation conversation prompt template

When asking Claude Code to stage a new instance, use this template:

```text
Stage a new PKDB-Bench instance:

Query category: <category>
Table title (LLM query): "<table_title>"
Suggested source: <source name or URL>

Steps:
1. Find the authoritative source via web search. Confirm it covers the query.
2. Extract and normalize the table (column type conventions in
   curation_protocol.md §6).
3. Write the CSV to: datasets/pkdb_bench/staging/<id>.csv
4. Write the draft entry to: datasets/pkdb_bench/staging/<id>.draft.json
   (schema in curation_ui_design.md §3).
5. Create an empty comments file: datasets/pkdb_bench/staging/<id>.comments.json
   with content: {"doubts": [], "comments": [], "chat_summary": ""}
6. Show a summary: row count, columns, PK uniqueness, any issues.
```

---

## 12. Implementation plan

| Step | Task | Effort |
| ---- | ---- | ------ |
| 1 | Create `staging/` with `.gitkeep`; add `staging/*.csv`, `staging/*.json` to `.gitignore` | < 15 min |
| 2 | Coverage tab — reads `cfg.json`, renders gap table, copy-prompt expanders | ~1.5 h |
| 3 | Sidebar — API key input, model selector, instance selector, cfg.json form | ~1.5 h |
| 4 | Editable table — `st.data_editor` with `⚑` and `💬` columns, doubt/comment persistence | ~2 h |
| 5 | Chatbot panel — Anthropic API call, system prompt with table context, message history | ~1.5 h |
| 6 | `<!--table_update-->` detection, diff summary, source-citation gate, Apply button | ~1.5 h |
| 7 | Quick action buttons | ~30 min |
| 8 | Validation rules (per-field + cross-field), error/warning display | ~2 h |
| 9 | Register / Reject / Save draft actions; write `review_logs/<id>.review.json` | ~1.5 h |
| 10 | End-to-end test with a real staged instance | ~30 min |
| **Total** | | **~12 h** |

**Recommended build order:** 2 → 3 → 5 → 4 → 6 → 7 → 8 → 9.
Build the coverage tab first (useful immediately), then the chatbot (core loop), then
the editable table (most complex widget), then wire everything together.

---

## 13. Out of scope (v0.2)

- Multi-user or concurrent editing.
- Diff view when applying a `<!--table_update-->` block (show before/after side-by-side).
- Undo history for table edits.
- Annotation tooling for Tier D (human annotation from scratch).
- Authentication or access control.
- Saving chat history to disk across sessions.
