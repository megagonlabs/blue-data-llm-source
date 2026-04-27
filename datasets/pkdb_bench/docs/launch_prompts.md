# Agent Launch Prompts

Copy-paste prompts for launching Claude Code or Codex on PKDB-Bench
instance work. The full contract (rules, schemas, ownership) lives in
`agent_handoff_bundle.md`; this file is just the launch lines.

> Claude Code prompts assume the `pkdb-bench-curation` skill is installed
> at `.claude/skills/pkdb-bench-curation/`. The `/pkdb-bench-curation`
> invocation preloads workflows + artifact contract.
>
> Codex prompts work without any skill — the agent reads the bundle directly.

---

## 1. Solo create-instance (default mode)

One agent picks a category and stages one instance. Use this for the
common case.

### Claude Code

```text
/pkdb-bench-curation

Read datasets/pkdb_bench/docs/agent_handoff_bundle.md and follow the
"Standard agent prompt" to create one new staged PKDB-Bench instance.

Use the gap-counter snippet in the bundle to pick a category before
choosing your query. Set _staged_by = "claude-code" in draft.json.
```

### Codex

```text
Read datasets/pkdb_bench/docs/agent_handoff_bundle.md and follow the
"Standard agent prompt" to create one new staged PKDB-Bench instance.

Use the gap-counter snippet in the bundle to pick a category before
choosing your query. Set _staged_by = "codex" in draft.json.
```

### Optional steering (append to either prompt)

- `Constraint: target query_category = "geographic" if you can source it cleanly; otherwise pick another zero-coverage category.`
- `Constraint: prefer Wikidata SPARQL as the primary source.`
- `Constraint: aim for 20–60 rows; avoid all-text schemas (need ≥1 numeric or categorical column).`

---

## 2. Stage → Audit (default collaboration)

Two agents, two passes. Agent A stages; Agent B audits without editing.

**Step 1.** Use a solo prompt from §1 with Agent A.

**Step 2.** Audit the result with Agent B. Pick the audit bundle that
matches the risk you most want to surface — the four bundles below
mirror the ones the UI exposes in `curation_ui/app.py`
(`CHAT_PROMPT_BUNDLES`). The default is **Full pre-registration sweep**.

### Audit bundles (pick one)

- **Data integrity sweep** — best first pass after staging or major
  edits. Row correctness, duplicates, normalization, table-shape, and
  cardinality, in one run.
- **Source and scope sweep** — when the main risk is whether the cited
  sources actually support the query wording and intended scope.
- **Metadata and registration sweep** — near the end, to verify cfg
  metadata, benchmark fit, and whether the instance is ready to register.
- **Full pre-registration sweep** — broadest single pass; covers data
  integrity, scope, metadata, benchmark fit, and a final
  Ready / Not ready verdict. Use this if you can only run one.

### Claude Code (auditor)

```text
/pkdb-bench-audit

Audit the staged instance datasets/pkdb_bench/staging/<id>.* using the
audit bundles in references/audit-bundles.md, the comments-file schema
in references/comments-schema.md, and the "Agent Collaboration Patterns"
section of datasets/pkdb_bench/docs/agent_handoff_bundle.md.

## Ownership (append-only)

Audit writes are append-only. Do NOT edit
datasets/pkdb_bench/staging/<id>.csv,
datasets/pkdb_bench/staging/<id>.draft.json, or
datasets/pkdb_bench/staging/<id>.curation.json. You may only append to
datasets/pkdb_bench/staging/<id>.comments.json (or create it if missing,
following the schema below).

## Audit task — Full pre-registration sweep

Perform a full pre-registration review of this staged instance using
the cited source set and current benchmark policy. Be policy-faithful,
not perfectionist: if the source tier is allowed and honestly declared,
do not block registration only because stronger sources could exist.
At the same time, keep coverage checking strict and assess whether the
cited sources truly support the rows and claimed scope.

Cover ALL of the following in one structured response:

1. row-level correctness and unsupported / missing rows relative to the
   cited source set,
2. duplicates and primary-key problems,
3. normalization, table-shape quality, and column-type consistency,
4. expected row-count / cardinality estimate, partition sanity, and
   unique-key sanity,
5. source quality, source tier, and source-to-query scope match,
6. table completeness for the intended scope,
7. cfg metadata correctness (table_title, query_category, db_analog,
   operation_type, primary_key, column_types, source_count, domain,
   temporal_sensitivity, gt_construction, temporal_note, source_urls),
8. LLM-knowable fit (would a closed-book pre-2025 model plausibly know
   these rows from pretraining?),
9. whether this instance helps the benchmark's intended multi-source /
   structured extraction story,
10. final Ready / Not ready judgment, clearly separating BLOCKING issues
    under current policy from NON-BLOCKING upgrade suggestions, and
    listing the minimal exact fixes needed.

For each finding, classify as factual / structural / scope-related and
cite the source URL + locator (page section, list position, table cell)
that the finding rests on. If you would propose table edits, describe
them as a recommendation in the audit report — do NOT write them to the
CSV (that would violate append-only ownership).

## Output: append to <id>.comments.json

Append a chat_events entry AND a chat_history user/assistant pair using
the existing schema. If the file does not exist, create it with this
shape:

{
  "doubts": [],
  "comments": [],
  "chat_events": [
    {
      "at": "<ISO 8601 UTC timestamp>",
      "action": "assistant_response",
      "summary": "<first 200 chars of your audit, followed by '...'>",
      "source_cited": "",
      "source_fetch_enabled": false
    }
  ],
  "chat_summary": "<same as summary above>",
  "chat_history": [
    {"role": "user", "content": "<the audit prompt you were given, verbatim>"},
    {"role": "assistant", "content": "<your full audit report>"}
  ]
}

If the file already exists: append the new chat_events entry to the
existing chat_events list, append the user/assistant pair to the existing
chat_history list, and update chat_summary to point at the new summary.
Do not delete or rewrite any existing entries.

Set _audited_by = "claude-code" in any new top-level audit metadata
field if you add one (otherwise leave the schema unchanged).

## Return to me

A condensed verdict block:

1. Ready / Not ready
2. Number of blocking issues, by classification
3. The 3–5 minimal fixes needed (if any)
4. The path you wrote to
```

### Codex (auditor)

```text
Read datasets/pkdb_bench/docs/agent_handoff_bundle.md (especially the
"Agent Collaboration Patterns" section) and audit the staged instance
datasets/pkdb_bench/staging/<id>.*.

## Ownership (append-only)

Audit writes are append-only. Do NOT edit
datasets/pkdb_bench/staging/<id>.csv,
datasets/pkdb_bench/staging/<id>.draft.json, or
datasets/pkdb_bench/staging/<id>.curation.json. You may only append to
datasets/pkdb_bench/staging/<id>.comments.json (or create it if missing,
following the schema below).

## Audit task — Full pre-registration sweep

Perform a full pre-registration review of this staged instance using
the cited source set and current benchmark policy. Be policy-faithful,
not perfectionist: if the source tier is allowed and honestly declared,
do not block registration only because stronger sources could exist.
At the same time, keep coverage checking strict and assess whether the
cited sources truly support the rows and claimed scope.

Cover ALL of the following in one structured response:

1. row-level correctness and unsupported / missing rows relative to the
   cited source set,
2. duplicates and primary-key problems,
3. normalization, table-shape quality, and column-type consistency,
4. expected row-count / cardinality estimate, partition sanity, and
   unique-key sanity,
5. source quality, source tier, and source-to-query scope match,
6. table completeness for the intended scope,
7. cfg metadata correctness (table_title, query_category, db_analog,
   operation_type, primary_key, column_types, source_count, domain,
   temporal_sensitivity, gt_construction, temporal_note, source_urls),
8. LLM-knowable fit (would a closed-book pre-2025 model plausibly know
   these rows from pretraining?),
9. whether this instance helps the benchmark's intended multi-source /
   structured extraction story,
10. final Ready / Not ready judgment, clearly separating BLOCKING issues
    under current policy from NON-BLOCKING upgrade suggestions, and
    listing the minimal exact fixes needed.

For each finding, classify as factual / structural / scope-related and
cite the source URL + locator (page section, list position, table cell)
that the finding rests on. If you would propose table edits, describe
them as a recommendation in the audit report — do NOT write them to the
CSV (that would violate append-only ownership).

## Output: append to <id>.comments.json

Append a chat_events entry AND a chat_history user/assistant pair using
the existing schema. If the file does not exist, create it with this
shape:

{
  "doubts": [],
  "comments": [],
  "chat_events": [
    {
      "at": "<ISO 8601 UTC timestamp>",
      "action": "assistant_response",
      "summary": "<first 200 chars of your audit, followed by '...'>",
      "source_cited": "",
      "source_fetch_enabled": false
    }
  ],
  "chat_summary": "<same as summary above>",
  "chat_history": [
    {"role": "user", "content": "<the audit prompt you were given, verbatim>"},
    {"role": "assistant", "content": "<your full audit report>"}
  ]
}

If the file already exists: append the new chat_events entry to the
existing chat_events list, append the user/assistant pair to the existing
chat_history list, and update chat_summary to point at the new summary.
Do not delete or rewrite any existing entries.

Set _audited_by = "codex" in any new top-level audit metadata field if
you add one (otherwise leave the schema unchanged).

## Return to me

A condensed verdict block:

1. Ready / Not ready
2. Number of blocking issues, by classification
3. The 3–5 minimal fixes needed (if any)
4. The path you wrote to
```

Replace `<id>` with the instance id from Step 1.

### Swapping in a narrower audit bundle

To run **Data integrity sweep**, **Source and scope sweep**, or
**Metadata and registration sweep** instead of the full pre-registration
sweep, replace the `## Audit task` block in either prompt above with the
corresponding bundle content from `curation_ui/app.py` →
`CHAT_PROMPT_BUNDLES`. Everything else (ownership rule, output schema,
return format) stays the same.

---

## 3. Same-Query Duel (experimental — both agents label the same instance)

Both agents independently produce a candidate for the **same** query, then
a reconciler merges them. Use this when you want a quality cross-check on
a high-stakes instance, not for routine staging.

### Step 0: Pre-pick the instance

Decide before launching:

- `<id>` — the instance id (e.g., `unesco_world_heritage_count_by_country`)
- `<query_intent>` — one-paragraph description of what the table is
- `<category>` — the chosen `query_category` from `categories.txt`

Both agents must receive the **identical** id, query intent, and
category. Do not let either agent pick — that defeats the duel.

### Step 1: Launch both agents in parallel

Paste the Claude Code prompt into one conversation, the Codex prompt
into another. They must not coordinate.

#### Claude Code (duel side A)

```text
/pkdb-bench-curation

You are participating in a same-query duel. Another agent is
independently producing a candidate for the SAME query. Do not
coordinate; do not look at the other agent's output.

Instance id: <id>
Query intent: <query_intent>
Target query_category: <category>

Read datasets/pkdb_bench/docs/agent_handoff_bundle.md and follow the
"Standard agent prompt", with these duel-mode overrides:

- Write your output to (create the directory if missing):
    datasets/pkdb_bench/staging/duel/<id>/claude.csv
    datasets/pkdb_bench/staging/duel/<id>/claude.draft.json
    datasets/pkdb_bench/staging/duel/<id>/claude.curation.json
- Set _staged_by = "claude-code" in draft.json.
- Do NOT write any canonical datasets/pkdb_bench/staging/<id>.* files.
  Only the reconciler may promote duel output to canonical.

Return: chosen sources, claim type, row count, file paths written, and
any open questions you'd flag for the reconciler.
```

#### Codex (duel side B)

```text
You are participating in a same-query duel. Another agent is
independently producing a candidate for the SAME query. Do not
coordinate; do not look at the other agent's output.

Instance id: <id>
Query intent: <query_intent>
Target query_category: <category>

Read datasets/pkdb_bench/docs/agent_handoff_bundle.md and follow the
"Standard agent prompt", with these duel-mode overrides:

- Write your output to (create the directory if missing):
    datasets/pkdb_bench/staging/duel/<id>/codex.csv
    datasets/pkdb_bench/staging/duel/<id>/codex.draft.json
    datasets/pkdb_bench/staging/duel/<id>/codex.curation.json
- Set _staged_by = "codex" in draft.json.
- Do NOT write any canonical datasets/pkdb_bench/staging/<id>.* files.
  Only the reconciler may promote duel output to canonical.

Return: chosen sources, claim type, row count, file paths written, and
any open questions you'd flag for the reconciler.
```

### Step 2: Run the reconciler (fresh conversation, either agent)

```text
/pkdb-bench-curation        ← omit this line if launching in Codex

You are reconciling a same-query duel for instance <id>. Two agents
independently staged candidates under:

  datasets/pkdb_bench/staging/duel/<id>/codex.{csv,draft.json,curation.json}
  datasets/pkdb_bench/staging/duel/<id>/claude.{csv,draft.json,curation.json}

Follow the compare-results workflow in references/workflows.md and the
"Same-Query Duel" rules in datasets/pkdb_bench/docs/agent_handoff_bundle.md.

Tasks:

1. Compare row sets, value conflicts, source interpretations, and
   claim types.
2. Write the reconciliation artifact using the schema in
   agent_handoff_bundle.md:
     datasets/pkdb_bench/staging/duel/<id>/reconciliation.json
3. Promote the reconciled result to canonical:
     datasets/pkdb_bench/staging/<id>.csv
     datasets/pkdb_bench/staging/<id>.draft.json
     datasets/pkdb_bench/staging/<id>.curation.json
   In <id>.curation.json, include:
     "reconciliation_artifact": "datasets/pkdb_bench/staging/duel/<id>/reconciliation.json"
     "reconciliation_summary": "<one sentence>"
4. Remind the human: BEFORE pressing Register in the UI, manually copy
   reconciliation.json to
     datasets/pkdb_bench/review_logs/<id>.reconciliation.json
   and rewrite reconciliation_artifact in the canonical curation record
   to point at the durable review-log path. (Until UI registration
   automates this, it's a manual pre-registration step.)

Return: row counts (codex / claude / canonical), key disagreements,
chosen claim type, and the file paths written.
```

---

## Quick reference: which mode for what

| Goal | Mode | Cost |
|---|---|---|
| Add an instance to the bench | Solo create-instance (§1) | 1 agent run |
| Cross-check a routine instance | Stage → Audit (§2) | 2 agent runs |
| High-stakes instance, want independent verification | Same-Query Duel (§3) | 3 agent runs (2 stagers + reconciler) |

Default to §1. Use §2 when the instance is unusually subtle (scope
ambiguity, supplementation pattern, novel category). Reserve §3 for
instances that will anchor the benchmark.
