# PKDB-Bench Agent Handoff Bundle

Use this when handing off a benchmark-instance task (creation, audit,
repair, reconciliation) to an agentic workflow such as Claude Code or
Codex. This file is the canonical, agent-agnostic handoff.

Claude Code and Codex sessions may have two local skills that mirror this
content:

- `.claude/skills/pkdb-bench-curation/` — for creating, repairing,
  reconciling, or preparing instances (writes canonical staged files).
- `.claude/skills/pkdb-bench-audit/` — for audit-only work (reads staged
  files, appends findings to `<id>.comments.json`, never edits the
  canonical artifacts).

## Purpose

This bundle is the shared launch context for three common handoffs:

- **curation / creation** — write one new staged instance under
  `datasets/pkdb_bench/staging/`
- **repair** — apply audit findings to existing staged artifacts
- **audit** — read staged artifacts and append findings to
  `<id>.comments.json` without editing canonical staged files

The target of curation work is a staged instance ready for human review in
the curation UI, not a directly registered benchmark item. Audit work is a
separate ownership mode: append-only, source-checking, and verdict-focused.

## Minimal Session Kickoff

For a fresh Claude Code or Codex session, the human can usually start with
only this:

```text
Read datasets/pkdb_bench/docs/agent_handoff_bundle.md. For PKDB-Bench work,
use pkdb-bench-curation for create / repair / registration-prep and
pkdb-bench-audit for audit-only append-only review.

Wait for my next message, then choose the matching mode and proceed.
```

After that, normal task prompts can be short:

```text
Audit the staged instance <id>.
```

```text
Apply the audit fixes for <id>.
```

```text
Create the next instance from the current priority queue.
```

The longer prompts later in this file are fallback templates for sessions
where skill routing, ownership, or context is uncertain. They are not the
default ritual for every run.

## Mode Router

Pick exactly one mode before reading deeper. The mode determines which
files may be edited.

| User request | Mode | Skill | Write permissions |
| --- | --- | --- | --- |
| "Create / label / stage the next instance" | create-instance | `pkdb-bench-curation` | may create `staging/<id>.csv`, `.draft.json`, `.curation.json`; optional append/create `.comments.json` |
| "Apply audit fixes / repair this instance" | repair-instance | `pkdb-bench-curation` | may edit canonical staged CSV/draft/curation; preserve comments history |
| "Audit / review / ready for registration?" | audit-instance | `pkdb-bench-audit` | append-only to `staging/<id>.comments.json`; never edit CSV/draft/curation |
| "Prepare/register after clean audit" | prepare-registration | `pkdb-bench-curation` | may update metadata needed for registration; do not lose audit history |
| "Reconcile two independent candidates" | compare-results | `pkdb-bench-curation` | only reconciler writes canonical `staging/<id>.*` |

If the mode is ambiguous, infer it from ownership:

- **writes or repairs canonical staged artifacts** → curation
- **checks and appends findings only** → audit

Do not mix curation and audit ownership in one pass unless the human
explicitly asks for it.

## What To Read

Read in layers. Do not bulk-load every file unless the task calls for it.
All file paths below are repo-relative; do not commit local absolute paths
such as `/Users/<name>/...` into this handoff.

### Always

- `datasets/pkdb_bench/docs/README.md`
  Benchmark purpose, current status, and framing.
- `datasets/pkdb_bench/docs/curation_protocol.md`
  Main rules for valid instance construction and registration.
- `datasets/pkdb_bench/docs/categories.txt`
  Query taxonomy and DB analogues.
- `datasets/pkdb_bench/docs/example_questions.txt`
  Human-curated seed working set. Prefer these questions, verbatim or
  adapted, when they fit the target category and source plan.

### Create / Repair

- `.claude/skills/pkdb-bench-curation/SKILL.md`
- `.claude/skills/pkdb-bench-curation/references/workflows.md`
- `.claude/skills/pkdb-bench-curation/references/artifact-contract.md`
- `datasets/pkdb_bench/docs/instance_selection_priorities.md`
  Read when choosing the next new instance.
- `datasets/pkdb_bench/docs/domain_taxonomy.md`
  Read when setting or changing `draft.json::domain`.

### Audit

- `.claude/skills/pkdb-bench-audit/SKILL.md`
- `.claude/skills/pkdb-bench-audit/references/audit-bundles.md`
- `.claude/skills/pkdb-bench-audit/references/comments-schema.md`
- `datasets/pkdb_bench/docs/domain_taxonomy.md`
  Read when checking metadata / registration readiness.

### Background Only

- `datasets/pkdb_bench/docs/benchmark_domain_survey.md`
  Domain-taxonomy motivation; read when revising domain strategy.
- `datasets/pkdb_bench/docs/metrics_notes.txt`
  Calibration-aware analysis context.
- `datasets/pkdb_bench/docs/papers.md`
  Research positioning.

## Where To Find State

- Registered instances live in
  `datasets/pkdb_bench/cfg.json`.
- Current staged candidates live under `datasets/pkdb_bench/staging/`.
- A staged instance is the group:
  - `staging/<id>.csv`
  - `staging/<id>.draft.json`
  - `staging/<id>.curation.json`
  - `staging/<id>.comments.json` when audits or repairs have happened
- Previous audit / repair cycles for one instance are in
  `staging/<id>.comments.json` plus `curation.json::audit_response`.
- The current next-instance planning snapshot is in
  `datasets/pkdb_bench/docs/instance_selection_priorities.md`.

Do not infer previous-instance state from chat history alone. Read the
instance artifacts.

## Reference Example

A well-formed `source_defined` staged instance:

- `datasets/pkdb_bench/staging/transboundary_rivers_by_continent_over_1000_km.csv`
- `datasets/pkdb_bench/staging/transboundary_rivers_by_continent_over_1000_km.draft.json`
- `datasets/pkdb_bench/staging/transboundary_rivers_by_continent_over_1000_km.curation.json`

## Legacy Reading List

The older all-in-one reading list is preserved here for agents that do not
support skills or progressive loading.

1. `datasets/pkdb_bench/docs/README.md`
   Benchmark purpose, current status, and framing.
2. `datasets/pkdb_bench/docs/curation_protocol.md`
   Main rules for valid instance construction and registration.
3. `datasets/pkdb_bench/docs/categories.txt`
   Query taxonomy and DB analogues.
4. `datasets/pkdb_bench/docs/example_questions.txt`
   Human-curated seed working set. Prefer these questions, verbatim or
   adapted, when they fit the target category and source plan.
5. `datasets/pkdb_bench/cfg.json`
   Canonical metadata format for registered instances.

Mode-specific docs:

- `datasets/pkdb_bench/docs/instance_selection_priorities.md`
  Read before choosing the next new instance. It balances seed-first
  preference, category gaps, domain variety, and viability.
- `datasets/pkdb_bench/docs/domain_taxonomy.md`
  Read when setting or checking `draft.json::domain`.
- `datasets/pkdb_bench/docs/benchmark_domain_survey.md`
  Background motivation for the domain taxonomy; read only when revising
  domain strategy.

Reference example (a well-formed `source_defined` staged instance):

- `datasets/pkdb_bench/staging/transboundary_rivers_by_continent_over_1000_km.csv`
- `datasets/pkdb_bench/staging/transboundary_rivers_by_continent_over_1000_km.draft.json`
- `datasets/pkdb_bench/staging/transboundary_rivers_by_continent_over_1000_km.curation.json`

Optional context (give when relevant):

- `datasets/pkdb_bench/docs/metrics_notes.txt`
  If the instance should be useful for later calibration-aware analysis.
- `datasets/pkdb_bench/docs/papers.md`
  If the agent should understand the research novelty and benchmark positioning.

## Fallback Copy-Paste Launch Prompts

Use these blocks only when a short task prompt is not enough, such as a
fresh agent without skill routing, a handoff to another tool, or an
ownership-sensitive audit/repair cycle. Replace `<id>` and paste any
curation-agent output where indicated.

### New Instance — Curation

Use this when you want the agent to choose and stage the next instance.

```text
You are the PKDB-Bench curation agent.

Use the `pkdb-bench-curation` skill if available. If it is not
auto-loaded, read the repo copy at:
- .claude/skills/pkdb-bench-curation/SKILL.md

Read datasets/pkdb_bench/docs/agent_handoff_bundle.md and follow the
"Standard Curation Prompt" section.

Create one new staged instance. Before choosing the question, consult:
- datasets/pkdb_bench/docs/instance_selection_priorities.md
- datasets/pkdb_bench/docs/example_questions.txt
- datasets/pkdb_bench/docs/categories.txt
- datasets/pkdb_bench/docs/domain_taxonomy.md

Prefer a human-curated seed question, verbatim or adapted, when it fits
an under-covered category and a viable source plan. Original questions
are still allowed when no seed fits; document why.

Write only staged artifacts:
- datasets/pkdb_bench/staging/<id>.csv
- datasets/pkdb_bench/staging/<id>.draft.json
- datasets/pkdb_bench/staging/<id>.curation.json
- optional datasets/pkdb_bench/staging/<id>.comments.json

Do not register directly. Run creator preflight before reporting ready.
Return: instance id, category, seed provenance, claim type, source tier,
source URLs, row/column count, files written, and open questions.
```

### Repair Existing Instance — Curation

Use this when an auditor already found issues and you want the curation
agent to apply fixes.

```text
You are the PKDB-Bench curation repair agent.

Use the `pkdb-bench-curation` skill if available. If it is not
auto-loaded, read the repo copy at:
- .claude/skills/pkdb-bench-curation/SKILL.md

Read datasets/pkdb_bench/docs/agent_handoff_bundle.md, especially
"Agent Collaboration Patterns".

Repair staged instance:
datasets/pkdb_bench/staging/<id>.*

You may edit the canonical staged files:
- <id>.csv
- <id>.draft.json
- <id>.curation.json

Preserve <id>.comments.json and prior audit history. If you add repair
notes to comments.json, append only.

Apply the audit findings below. Address every BLOCKING finding before
reporting ready. Address NON-BLOCKING HYGIENE findings when cheap, and
record decisions about SUGGESTION findings in curation.json::decision_log.
After repair, run creator preflight.

Audit findings / reviewer output:
<PASTE AUDIT OR REVIEW OUTPUT HERE>

Return: findings addressed, files changed, row-count/PK/source_count
state, remaining open questions, and whether the instance is ready for
re-audit.
```

### Audit Staged Instance

Use this when a curation agent says an instance is ready for audit.

```text
You are the PKDB-Bench audit agent.

Use the `pkdb-bench-audit` skill if available. If it is not
auto-loaded, read the repo copy at:
- .claude/skills/pkdb-bench-audit/SKILL.md

Read datasets/pkdb_bench/docs/agent_handoff_bundle.md, especially
"Agent Collaboration Patterns".

Audit staged instance:
datasets/pkdb_bench/staging/<id>.*

Expected files:
- <id>.csv
- <id>.draft.json
- <id>.curation.json
- <id>.comments.json may or may not exist

Ownership: audit is append-only. Do NOT edit <id>.csv,
<id>.draft.json, or <id>.curation.json. Only append to
<id>.comments.json, or create it if missing using the comments schema.

Run the Full pre-registration sweep:
1. row-level correctness and unsupported / missing rows relative to the
   cited source set,
2. duplicates and primary-key problems,
3. normalization, table-shape quality, and column-type consistency,
4. expected total and per-partition row-count / cardinality estimate,
   partition sanity, and unique-key sanity,
5. missing Wiki-family source check: whether a more direct Wikipedia or
   Wikidata source exists and would change scope, coverage, ranking, or
   provenance,
6. source quality, source tier, source roles, and source-to-query scope
   match,
7. table completeness for the intended scope,
8. cfg metadata correctness, including table_title, query_category,
   db_analog, operation_type, primary_key, column_types, source_count,
   domain using datasets/pkdb_bench/docs/domain_taxonomy.md,
   temporal_sensitivity, gt_construction, temporal_note, and source_urls,
9. LLM-knowable fit,
10. whether this instance helps the benchmark's multi-source /
    structured-extraction story,
11. final Ready / Not ready judgment.

Use the two-axis taxonomy for every finding:
- Issue type: factual / structural / scope-related
- Severity: BLOCKING / NON-BLOCKING HYGIENE / SUGGESTION

Cite source URL + locator for each finding, and say whether the evidence
comes from the staged ground-truth source set or an audit
validation/challenge source. Recommend fixes, but do not apply them.

Append the full audit to <id>.comments.json as a chat_events entry and a
chat_history user/assistant pair. Preserve all existing entries.

Return only a condensed verdict block:
1. Ready / Not ready
2. Counts by severity: BLOCKING / NON-BLOCKING HYGIENE / SUGGESTION
3. Counts by issue type: factual / structural / scope-related
4. The 3-5 minimal fixes needed, if any
5. The path written to

Latest curation-agent output:
<PASTE CURATION AGENT OUTPUT HERE>
```

### Claude Code Skill Shortcuts

If launching in Claude Code, prepend the matching skill command to the
copy block:

```text
/pkdb-bench-curation
```

for creation/repair, or:

```text
/pkdb-bench-audit
```

for audit-only work.

Do not invoke both skills in the same conversation. Pick the one that
matches the ownership model.

## Identify category gaps before choosing

Don't pick a category that's already saturated. Run this from the repo
root to see current coverage across registered + staged instances:

```bash
python3 - <<'PY'
import json, glob
from collections import Counter

with open("datasets/pkdb_bench/cfg.json") as f:
    registered = json.load(f)
# cfg.json is a dict of {id: entry} — iterate values, not keys.
registered_entries = registered.values() if isinstance(registered, dict) else registered
staged_entries = [json.load(open(p)) for p in
                  glob.glob("datasets/pkdb_bench/staging/*.draft.json")]

cats = Counter(e["query_category"] for e in registered_entries) \
     + Counter(e["query_category"] for e in staged_entries)
all_cats = {
    "factual_lookup", "set_filter", "implicit_join", "aggregation",
    "top_k", "set_operation", "multi_hop", "qualitative", "temporal",
    "geographic", "causal", "comparative", "hierarchical_decomposition",
    "multi_attribute_projection", "scaled_enumeration",
}
print("counts:", dict(cats))
print("zero-coverage:", sorted(all_cats - cats.keys()))
PY
```

Prefer a zero-coverage category. If none fits cleanly with available
sources, pick a low-count one over a high-count one.

## Expected output files

The agent should write:

- `datasets/pkdb_bench/staging/<id>.csv`
- `datasets/pkdb_bench/staging/<id>.draft.json`
- `datasets/pkdb_bench/staging/<id>.curation.json`

Optional:

- `datasets/pkdb_bench/staging/<id>.comments.json`

## What each staged file means

- `<id>.csv`
  Candidate ground-truth table.
- `<id>.draft.json`
  Candidate benchmark registration metadata.
- `<id>.curation.json`
  GT collection notes, source interpretation, normalization decisions, open
  questions, claim type, and curation rationale.
- `<id>.comments.json`
  Optional seeded review warnings or doubts for the human curator.

## Minimum required draft fields

- `id`
- `table_title`
- `columns`
- `primary_key`
- `column_types`
- `query_category`
- `secondary_category` (optional — when a second category is equally valid)
- `db_analog`
- `operation_type`
- `source_count`
- `domain`
- `temporal_sensitivity`
- `gt_construction`
- `temporal_note`
- `source_urls`
- `_staged_by` — agent or person who created the staging (e.g. `"codex"`, `"claude-code"`, `"human"`)
- `_staged_at` — ISO 8601 timestamp of initial staging
- `_extraction_notes` — one paragraph on how the row set was derived, what was supplemented, and what was excluded

## Minimum required curation fields

- `claim_type` — one of `exhaustive`, `source_defined`, `partially_supplemented`, `candidate_rebuild`
- `collection_summary`
- `source_coverage_notes`
- `decision_log`
- `normalization_decisions`
- `open_questions`

## Agent Collaboration Patterns

When more than one agent (Claude Code, Codex, etc.) participates in
curating a single instance, follow one of these patterns. Pick one
explicitly per task — do not improvise.

### Default: Stage → Audit

Agent A owns creation of:

- `staging/<id>.csv`
- `staging/<id>.draft.json`
- `staging/<id>.curation.json`
- optional `staging/<id>.comments.json`

Agent B (the auditor) appends findings to `staging/<id>.comments.json`
or returns an audit report for the human / curation UI to apply. **Audit
writes are append-only**: Agent B must not overwrite existing doubts,
comments, chat events, or staged CSV / draft / curation files. This
preserves the cross-check — without ownership, audit silently mutates
into co-authoring.

Use this by default.

#### Cycle numbering convention

A "cycle" is one curator pass paired with the audit pass that reviews
it. Numbering is shared across the two agents and increments together:

- **Curator cycle 1** = initial stage of the instance.
- **Audit cycle 1** = first audit of that initial stage.
- **Curator cycle 2** = repair pass addressing audit-cycle-1 findings.
- **Audit cycle 2** = re-audit of the cycle-2 repair.
- **Curator cycle N** = repair pass addressing audit-cycle-(N-1) findings.
- **Audit cycle N** = re-audit of the cycle-N repair.

Both agents should label their work with the same cycle number when
they are on the same iteration. Audit handoff prompts, chat messages,
and `audit_response.cycle_N` blocks in `curation.json` should all use
this shared numbering. There is no "cycle 0" — the initial stage is
cycle 1.

#### Termination: audit confirmation cycle

The last cycle is always an audit pass that returns **0 BLOCKING + 0
HYGIENE + 0 SUGGESTION** — a pure confirmation that the staged
instance is ready for registration with no remaining issues. If the
auditor returns a Ready verdict but with non-blocking findings (any
HYGIENE or SUGGESTION count > 0), the curator should run one more
repair cycle to clear them, then trigger another audit. Repeat until
the audit returns all zeros.

Ready-with-non-blocking is **not** a terminal state. Lock only on
all-zeros confirmation.

This avoids two failure modes:

1. **Latent inconsistencies** — non-blocking issues are still real
   inconsistencies that future readers (or downstream evaluation
   tooling) will hit. Clearing them as part of staging is cheap; doing
   so after registration is expensive.
2. **Drift between curator and audit narratives** — the audit's
   findings remain documented in `comments.json`, but the curator's
   resolution should also be documented in
   `curation.json::audit_response.cycle_N` for each cycle. The
   confirmation cycle makes that paper trail complete.

### Parallel Independent Staging

Agents may create different instances concurrently only if they use
different `<id>` values. No two agents should write the same
`staging/<id>.*` files at the same time.

### Experimental: Same-Query Duel

Use only when explicitly requested. Both agents independently produce a
candidate for the same query, then a third pass reconciles them.

Agent outputs must be namespaced under a duel directory:

- `staging/duel/<id>/codex.csv`
- `staging/duel/<id>/codex.draft.json`
- `staging/duel/<id>/codex.curation.json`
- `staging/duel/<id>/claude.csv`
- `staging/duel/<id>/claude.draft.json`
- `staging/duel/<id>/claude.curation.json`
- `staging/duel/<id>/reconciliation.json`

A reconciler compares rows, values, sources, claim types, and scope
decisions. **Only the reconciler writes the canonical `staging/<id>.*`
files.** Individual agents must not promote their own duel output to
canonical.

`reconciliation.json` schema (keep it tiny):

```json
{
  "id": "...",
  "mode": "same_query_duel",
  "inputs": ["codex", "claude"],
  "row_differences": [],
  "value_conflicts": [],
  "source_conflicts": [],
  "claim_type_decision": "...",
  "canonicalization_decisions": [],
  "remaining_open_questions": [],
  "reconciled_by": "...",
  "reconciled_at": "..."
}
```

**Provenance — staging vs. registration.** Staging is mutable working
state; do not let canonical records depend on staging paths surviving
unchanged.

During staging, the canonical `<id>.curation.json` includes:

- `"reconciliation_artifact": "datasets/pkdb_bench/staging/duel/<id>/reconciliation.json"`
- `"reconciliation_summary": "...one sentence..."`

Before pressing Register in the current UI, the human must copy:

- `datasets/pkdb_bench/staging/duel/<id>/reconciliation.json`

to:

- `datasets/pkdb_bench/review_logs/<id>.reconciliation.json`

Then rewrite `reconciliation_artifact` in the canonical curation record
to point at the durable review-log copy.

The duel directory itself can then be cleaned up without breaking
provenance on registered instances.

> **TODO:** automate this in `curation_ui/app.py::register_instance`
> after duel mode has been exercised. Until then it is a manual
> pre-registration step.

## Standard Curation Prompt

```text
You are creating one new staged PKDB-Bench instance.

## What PKDB-Bench is

PKDB-Bench evaluates whether an LLM can reconstruct structured tables from its
own parametric knowledge. Each instance is a (query, ground-truth table) pair.
The ground truth must be sourced from an authoritative external source —
never generated from parametric knowledge alone.

## Before you start, read these files

- datasets/pkdb_bench/docs/README.md
- datasets/pkdb_bench/docs/curation_protocol.md
- datasets/pkdb_bench/docs/categories.txt
- datasets/pkdb_bench/docs/example_questions.txt
- datasets/pkdb_bench/docs/instance_selection_priorities.md
- datasets/pkdb_bench/docs/domain_taxonomy.md
- datasets/pkdb_bench/staging/transboundary_rivers_by_continent_over_1000_km.csv
- datasets/pkdb_bench/staging/transboundary_rivers_by_continent_over_1000_km.draft.json
- datasets/pkdb_bench/staging/transboundary_rivers_by_continent_over_1000_km.curation.json

The last three are a well-formed reference example of a staged instance.

## Question and category guidance

Before choosing the question, check current coverage and the picker guide
in datasets/pkdb_bench/docs/instance_selection_priorities.md.

Prefer this order:

1. a human-curated seed from example_questions.txt, verbatim if it is
   sourceable and category-fit;
2. an adapted seed, with the adaptation documented;
3. an original category-targeted question, only when no seed fits cleanly.

Prefer under-covered categories and under-represented domains, but do not
force a weak source plan just to fill a gap.

Choose the category you can source most cleanly. The instance must be
LLM-knowable: a general-purpose model with a pre-2025 knowledge cutoff
should be able to answer the query from memory alone.

## Source tier order (prefer higher)

1. Wikidata SPARQL query (strongest — reproducible, structured)
2. Manual Wikipedia page (acceptable — cite the stable URL)
3. Web search (last resort — cite explicitly)

## Hard rules

1. Every row must trace to a cited URL. No parametric-only rows.
2. Declare the claim type: exhaustive / source_defined / partially_supplemented / candidate_rebuild.
3. Scope table_title, db_analog, and claim_type to what the cited source
   set actually supports. Prefer natural scoped language over provenance-
   aware titles like "in the cited source set".
4. If the primary source only covers "major" or "notable" entities and
   you add supplement rows, follow the proposed_supplements audit trail in
   the curation skill before broad supplementation.
5. The table must have at least one key column and at least one numeric or categorical
   column (not all-text).
6. Keep source_count == len(source_urls). Logical decomposition belongs in
   db_analog and decision_log; source_count is URL count.
7. Identify source roles in source_coverage_notes using the artifact-contract
   convention: universe / ranking / value / validation / entity-detail.
8. Set domain using domain_taxonomy.md, preferably Family/Subdomain.
9. Do not register directly — write staged files only.

## Output files to write

- datasets/pkdb_bench/staging/<id>.csv
- datasets/pkdb_bench/staging/<id>.draft.json
- datasets/pkdb_bench/staging/<id>.curation.json
- optional datasets/pkdb_bench/staging/<id>.comments.json

draft.json must include: id, table_title, columns, primary_key, column_types,
query_category, secondary_category when useful, db_analog, operation_type,
source_count, domain, temporal_sensitivity, gt_construction, temporal_note,
source_urls, _staged_by, _staged_at, _extraction_notes.

curation.json must include: claim_type, collection_summary, source_coverage_notes,
decision_log, normalization_decisions, open_questions.

Before reporting ready, run the creator preflight in
.claude/skills/pkdb-bench-curation/references/workflows.md. At minimum,
confirm PK uniqueness, schema consistency, query_category validity,
operation_type vocabulary, source_count == len(source_urls), row-count
bounds, source roles, seed provenance, category fit, domain fit, and any
Tier-A reproducibility checks.

## Return when done

1. Instance id
2. Seed provenance and chosen category
3. Table title and db_analog
4. Claim type and justification
5. Source tier, source_count, and source URLs used
6. Row/column count and primary key
7. File paths written
8. Any open review questions
```

## Human review checklist after handoff

After the agent writes the staged files:

1. Open the instance in the curation UI.
2. Check whether the query wording matches the source scope.
3. Run an audit bundle in the chat assistant.
4. Inspect normalization and duplicates.
5. Review the curation record (especially claim_type and source_coverage_notes).
6. Only then decide whether to register.
