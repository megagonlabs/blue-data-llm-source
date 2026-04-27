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

**Step 2.** Audit the result with Agent B using one of:

### Claude Code (auditor)

```text
/pkdb-bench-curation

Audit the staged instance datasets/pkdb_bench/staging/<id>.* using the
audit-instance workflow in references/workflows.md.

Audit writes are append-only. Do NOT edit the CSV, draft.json, or
curation.json. Append findings to
datasets/pkdb_bench/staging/<id>.comments.json (or create it if missing).

Return a short audit report classifying each issue as factual,
structural, or scope-related. See "Agent Collaboration Patterns" in
datasets/pkdb_bench/docs/agent_handoff_bundle.md for the full rule.
```

### Codex (auditor)

```text
Read datasets/pkdb_bench/docs/agent_handoff_bundle.md (especially the
"Agent Collaboration Patterns" section) and audit the staged instance
datasets/pkdb_bench/staging/<id>.*.

Audit writes are append-only. Do NOT edit the CSV, draft.json, or
curation.json. Append findings to
datasets/pkdb_bench/staging/<id>.comments.json (or create it if missing).

Return a short audit report classifying each issue as factual,
structural, or scope-related.
```

Replace `<id>` with the instance id from Step 1.

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
