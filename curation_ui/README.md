# PKDB-Bench Curation UI

This is a Streamlit app for reviewing, editing, and registering PKDB-Bench
instances from `datasets/pkdb_bench/staging/`.

## Start the UI

Install the local UI dependencies:

```bash
conda create -n pkdb_curation_ui_py312 python=3.12
conda activate pkdb_curation_ui_py312
pip install -r curation_ui/requirements.txt
```

Launch the app from the repo root:

```bash
streamlit run curation_ui/app.py
```

Then open the local Streamlit URL shown in the terminal, usually
`http://localhost:8501`.

## Layout

The UI has three distinct areas:

| Area | Contents |
| --- | --- |
| **Left sidebar** | Provider / API key / model selector; staged instance picker; cfg.json form; source review panel |
| **Left main column** | `Review` and `Coverage` tabs |
| **Right main column** | Chat assistant — always visible regardless of which tab is active, with its own scrollable panel |

## What the UI expects

Each staged instance should have files in `datasets/pkdb_bench/staging/`:

- `<id>.csv`
- `<id>.draft.json`
- `<id>.curation.json` (optional; the UI will create/update it)
- `<id>.comments.json` (optional; the UI will create/update it)

The app also reads:

- `datasets/pkdb_bench/cfg.json`
- `datasets/pkdb_bench/tables/`
- `datasets/pkdb_bench/docs/categories.txt`

Staged schemas may also use boundary-aware text fields when a single
categorical label would be misleading, for example `Continent(s)` with a value
such as `Europe; Asia`.

## What the main actions do

- `Save draft`: writes the current table, metadata, and comments back to
  `datasets/pkdb_bench/staging/`
  The app now separates:
  - benchmark registration metadata in `<id>.draft.json`
  - GT collection / source interpretation / decision notes in `<id>.curation.json`
  - review comments and chat history in `<id>.comments.json`
- `Reload staged`: restores the latest staged CSV / draft / curation / comments
  files if an external agent or editor changed them
- `Register to cfg.json`: adds the cleaned metadata to
  `datasets/pkdb_bench/cfg.json`, writes the finalized CSV to
  `datasets/pkdb_bench/tables/<id>.csv`, writes an audit log to
  `datasets/pkdb_bench/review_logs/<id>.review.json`, and removes the staging
  files
- `Reject staging`: deletes the staged CSV/draft/comments files for that
  instance

Edits made in the UI are also auto-saved back to staging during normal review,
so comments, table changes, and chatbot history survive page reloads.

## Source review (sidebar)

The `Source review` expander in the left sidebar fetches the staged source URLs
and shows fetch status, prompt-budget inclusion, and excerpt previews. Click
`Refresh source review` to re-fetch. This panel is part of the cfg form area
because source URLs are set there.

One caveat: `query.wikidata.org` URLs mostly serve an interactive UI shell, so
their fetched text can be sparse or unhelpful compared with static HTML pages.

## Chat assistant (right column)

The chatbot is optional. It requires either an OpenAI or Anthropic API key set
in the sidebar. The OpenAI path uses the Chat Completions API.

By default, the UI loads credentials from the current shell environment or from
simple `export ...` entries in `~/.zshrc`:

- `OPENAI_API_KEY`
- `ANTHROPIC_API_KEY`

If no key is found, you can paste one into the sidebar. Without an API key, the
rest of the review UI still works.

The assistant receives:

- the full current CSV
- the current benchmark registration view derived from the draft
- the separate curation record with GT collection notes and decisions
- open review comments and doubt flags
- fetched source page excerpts (if `Fetch source page text for audits` is on)

If a staged instance uses a boundary-aware field such as `Continent(s)`, that
exact field and value are passed through to the assistant; the UI does not
collapse it back to a single category.

After a response returns, the UI shows a toast notification and keeps the full
assistant output inside the right-hand chat panel.

The chat header also includes:

- `Copy latest prompt + response`: copies the most recent user prompt plus the
  paired assistant response for handoff into an external agentic conversation
- `Clear chat`: resets only the current conversation and pending proposal

### Prompt library

The chat UI now has two layers:

- `Prompt library` for targeted checks
- `Audit bundles` for broader one-shot review passes

The targeted `Prompt library` contains 16 curated templates in three groups:

| Group | Templates |
| --- | --- |
| Correctness audits | Detect errors, Authoritative-source integrity sweep, Row-by-row audit, Check completeness, Normalize values, Detect duplicates, Estimate expected row count, Cardinality and coverage audit, Partition coverage audit, Schema and table-shape audit |
| Metadata & source | Validate metadata, Suggest db_analog, Summarize source, Challenge the source, Rebuild to authoritative sources |
| Registration | Check LLM-knowable, Confidence / calibration risk audit, Registration readiness |

Select a template, edit the text in place if needed, then click `Load into
message box` to copy it to the message input. Send it with `Send message`.

Use `Prompt library` when you already know what kind of check you want. It is
best for targeted follow-up work such as:

- checking only normalization
- auditing only duplicates
- estimating expected row count or uniqueness coverage
- checking whether partition coverage is balanced
- validating only metadata
- auditing whether the schema and row granularity are right for the benchmark
- assessing which columns carry calibration risk for later evaluation
- drilling into a specific issue found by a broader review

There are now three distinct source-audit modes:

- `cited-source audit`
  Use the normal correctness templates and bundles when you want to verify the
  instance against its current `source_urls`. This is the default for staged
  instances grounded in approved Tier B Wikipedia sources.
- `authoritative-source audit`
  Use `Authoritative-source integrity sweep` when you want to hold the instance
  to a stronger source standard than its current citations.
- `rebuild-to-authoritative`
  Use `Rebuild to authoritative sources` when the instance concept seems worth
  keeping but the current source set should be replaced with ECB / EU / Census /
  OECD / similar materials.

`Check LLM-knowable` has a specific meaning: it asks whether a closed-book,
general-purpose model with a pre-2025 cutoff would likely know these facts from
pretraining alone. It does **not** ask whether the curation source was
Wikipedia, and it does **not** care whether an agent could fetch the same facts
with tools at curation time. A table can be grounded in Wikipedia and still be
a good parametric-knowledge benchmark if the underlying facts are broadly known;
conversely, a table can be source-grounded and still be a poor fit if it relies
on niche or obscure rows that a model would only recover with browsing.

### Audit bundles

The `Audit bundles` expander combines related checks so users do not need to
click through many templates one by one. The UI also shows a short guidance note
for the currently selected bundle.

Current bundles:

- `Data integrity sweep`
- `Source and scope sweep`
- `Metadata and registration sweep`
- `Full pre-registration sweep`

`Full pre-registration sweep` is the closest thing to a one-shot `test all`.
In practice, bundles are the recommended default. Individual templates are most
useful when a bundle already found a specific issue and you want to drill down.

Use `Audit bundles` when you want one broader pass that combines multiple checks
into a single model call. This is usually the best default for normal review:

- start with `Data integrity sweep` after staging or large edits
- use `Source and scope sweep` when the main risk is source/query mismatch
- use `Metadata and registration sweep` near the end before registration
- use `Full pre-registration sweep` when you want the broadest one-shot review

The default bundles now assume review against the **cited source set** and the
current benchmark policy, not against a stricter “authoritative sources only”
standard. If you want to decide whether an instance should be rebuilt around
stronger sources, use the targeted authoritative-source prompts instead.

### Proposed table updates

If the assistant returns a `<!--table_update-->` CSV block, a diff preview
appears below the chat with `Apply table update` and `Dismiss proposal` buttons.
The apply button is enabled only when a `<!--source_citation-->` block is also
present and covers every changed column.

### Controls

- `Reload staged` lives in the main action bar with `Reject staging`, `Save draft`,
  and `Register to cfg.json`
- Chat requests time out after 60 seconds

## Table Review And Comments

The table editor has three helper columns:

- `Select`: marks rows for bulk comment actions
- `Doubt`: marks rows with unresolved review doubts
- `Comment count`: shows how many row-linked comments touch that row

For comments, the current UI supports:

- `Quick comment for current selection`
  Use `Select` plus `Selected columns`, then click `Add comment to selected cells`
  for the fastest row×column rectangular comment workflow.
- `Comment tools`
  Includes single `cell` / `row` / `column` comments plus bulk comment actions.
- `Comments manager`
  Visible below the table; lists all comments and supports update / delete / clear.

One limitation remains: Streamlit does not expose the browser's literal drag-selected
cell region from `st.data_editor`, so the app cannot read the exact red-highlighted
selection directly. The supported equivalent is `Select` rows + `Selected columns`.

The table may include either ordinary categorical columns such as `Continent`
or boundary-aware text columns such as `Continent(s)`, depending on the staged
instance schema.

## Notes

- The app only shows instances that are currently staged.
- If no staged instances exist, the `Coverage` tab and chat placeholder still
  render; only the `Review` tab content is empty.
- Syntax-check: `python -m py_compile curation_ui/app.py`; Streamlit
  dependencies must be installed locally to run the app.
