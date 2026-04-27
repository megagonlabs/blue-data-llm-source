# PKDB-Bench Agent Handoff Bundle

Use this when handing off a benchmark-instance task (creation, audit,
repair, reconciliation) to an agentic workflow such as Claude Code or
Codex. This file is the canonical, agent-agnostic handoff.

Claude Code has two parallel, native skills that mirror this content:

- `.claude/skills/pkdb-bench-curation/` — for creating, repairing,
  reconciling, or preparing instances (writes canonical staged files).
- `.claude/skills/pkdb-bench-audit/` — for audit-only work (reads staged
  files, appends findings to `<id>.comments.json`, never edits the
  canonical artifacts).

## Goal

The agent should create **one staged PKDB-Bench instance** that is:

- source-grounded
- category-labeled
- table-shaped
- aligned with the curation protocol
- ready for human review in the curation UI

The target output is a staged instance under `datasets/pkdb_bench/staging/`,
not a directly registered benchmark item.

## Files the agent should read first

Core docs:

1. [README.md](/Users/chen_s/Gits/megagon-internal/blue-llmsource/datasets/pkdb_bench/docs/README.md:1)
   Benchmark purpose, current status, and framing.
2. [curation_protocol.md](/Users/chen_s/Gits/megagon-internal/blue-llmsource/datasets/pkdb_bench/docs/curation_protocol.md:1)
   Main rules for valid instance construction and registration.
3. [categories.txt](/Users/chen_s/Gits/megagon-internal/blue-llmsource/datasets/pkdb_bench/docs/categories.txt:1)
   Query taxonomy and DB analogues.
4. [example_questions.txt](/Users/chen_s/Gits/megagon-internal/blue-llmsource/datasets/pkdb_bench/docs/example_questions.txt:1)
   Query style examples.
5. [cfg.json](/Users/chen_s/Gits/megagon-internal/blue-llmsource/datasets/pkdb_bench/cfg.json:1)
   Canonical metadata format for registered instances.

Reference example (a well-formed `source_defined` staged instance):

- `datasets/pkdb_bench/staging/transboundary_rivers_by_continent_over_1000_km.csv`
- `datasets/pkdb_bench/staging/transboundary_rivers_by_continent_over_1000_km.draft.json`
- `datasets/pkdb_bench/staging/transboundary_rivers_by_continent_over_1000_km.curation.json`

Optional context (give when relevant):

- [metrics_notes.txt](/Users/chen_s/Gits/megagon-internal/blue-llmsource/datasets/pkdb_bench/docs/metrics_notes.txt:1)
  If the instance should be useful for later calibration-aware analysis.
- [papers.md](/Users/chen_s/Gits/megagon-internal/blue-llmsource/datasets/pkdb_bench/docs/papers.md:1)
  If the agent should understand the research novelty and benchmark positioning.

## If you're using Claude Code

Pick the skill that matches the task ownership profile:

- For **creating** a new instance, **repairing** an existing one,
  **reconciling** a same-query duel, or **preparing for registration**:

  ```text
  /pkdb-bench-curation
  ```

  Preloads `references/workflows.md` and `references/artifact-contract.md`.

- For **auditing only** (read staged files, append findings to
  `<id>.comments.json`, do not edit canonical files):

  ```text
  /pkdb-bench-audit
  ```

  Preloads `references/audit-bundles.md` and `references/comments-schema.md`.

Do not invoke both in the same conversation — pick the one that matches
your ownership model. After invoking the skill, paste the "Standard
agent prompt" (§Standard agent prompt) for creation tasks, or the audit
prompts in `datasets/pkdb_bench/docs/launch_prompts.md` §2 for audits.

For other agents (Codex, etc.), there is no skill mechanism — the agent
reads the files listed above and the prompts in `launch_prompts.md`
directly. The contract is the same.

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

**Provenance — staging vs. registration.** Staging is scratch; do not
let canonical records depend on staging paths surviving.

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

## Standard agent prompt

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
- datasets/pkdb_bench/staging/transboundary_rivers_by_continent_over_1000_km.csv
- datasets/pkdb_bench/staging/transboundary_rivers_by_continent_over_1000_km.draft.json
- datasets/pkdb_bench/staging/transboundary_rivers_by_continent_over_1000_km.curation.json

The last three are a well-formed reference example of a staged instance.

## Category guidance

Before choosing a category, check current coverage (see "Identify category
gaps" in datasets/pkdb_bench/docs/agent_handoff_bundle.md). Prefer a
zero-coverage category; otherwise pick a low-count one over a high-count one.

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
3. If the primary source only covers "major" or "notable" entities, classify as
   partially_supplemented (not exhaustive), scope the title accordingly, and
   document any per-partition supplementation asymmetry in curation.json.
4. Before adding supplement rows beyond the main source, write proposed_supplements
   in curation.json first and pause for human review if: (a) more than ~3 individual
   entity pages would be added, (b) supplementation is asymmetric across partitions
   without clear justification, or (c) the supplement plan would change the claim type.
5. The table must have at least one key column and at least one numeric or categorical
   column (not all-text).
6. For source_count > 1 (join / union / intersect): collect each source independently
   first, use db_analog as the assembly blueprint, and record each source's row
   contribution in curation.json under source_coverage_notes.
7. Do not register directly — write staged files only.

## Output files to write

- datasets/pkdb_bench/staging/<id>.csv
- datasets/pkdb_bench/staging/<id>.draft.json
- datasets/pkdb_bench/staging/<id>.curation.json

draft.json must include: id, table_title, columns, primary_key, column_types,
query_category, db_analog, operation_type, source_count, domain,
temporal_sensitivity, gt_construction, temporal_note, source_urls,
_staged_by, _staged_at, _extraction_notes.

curation.json must include: claim_type, collection_summary, source_coverage_notes,
decision_log, normalization_decisions, open_questions.

## Return when done

1. Chosen category and why
2. Table title and db_analog
3. Claim type and justification
4. Source URLs used
5. Row count
6. File paths written
7. Any open review questions
```

## Human review checklist after handoff

After the agent writes the staged files:

1. Open the instance in the curation UI.
2. Check whether the query wording matches the source scope.
3. Run an audit bundle in the chat assistant.
4. Inspect normalization and duplicates.
5. Review the curation record (especially claim_type and source_coverage_notes).
6. Only then decide whether to register.
