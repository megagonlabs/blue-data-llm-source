---
name: pkdb-bench-curation
description: Use when creating, repairing, challenging the scope of, reconciling, or preparing PKDB-Bench instances for registration. This skill writes or modifies the canonical staged artifacts (CSV / draft.json / curation.json) under datasets/pkdb_bench/staging/. Use this when the task involves source-defined vs exhaustive scope, row coverage, cardinality, schema design, curation decisions, registration readiness, or applying fixes that an auditor recommended. For audit-only tasks (read staged files and append findings to <id>.comments.json without modifying canonical files), use pkdb-bench-audit instead.
---

# PKDB-Bench Curation

Use this skill for PKDB-Bench instance work that **writes or modifies the
canonical staged artifacts** (`<id>.csv`, `<id>.draft.json`,
`<id>.curation.json`). For **audit-only tasks** (read + append-only to
`<id>.comments.json`), use the sibling skill `pkdb-bench-audit` — do not
duplicate audit work here.

For session-level routing, handoff prompts, and multi-agent ownership
patterns, see `datasets/pkdb_bench/docs/agent_handoff_bundle.md`. Its
"Minimal Session Kickoff" and "Mode Router" sections are the canonical
agent-agnostic entrypoint; this skill is the curation action layer.
Expanded copy-paste prompts live in
`datasets/pkdb_bench/docs/launch_prompts.md` for fallback handoffs.

The benchmark artifact is not just a CSV. Every instance has four distinct layers:

- query intent
- candidate benchmark registration metadata
- ground-truth table
- curation rationale / source interpretation

Read the core benchmark docs first:

- `datasets/pkdb_bench/docs/README.md`
- `datasets/pkdb_bench/docs/curation_protocol.md`
- `datasets/pkdb_bench/docs/categories.txt`
- `datasets/pkdb_bench/docs/example_questions.txt`
  The human-curated seed working set for the benchmark — not loose
  examples, not rigid templates per category. Treat these questions
  as the pool to draw from when creating new instances. The
  load-bearing principle from the file's framing is that *"queries
  should be expected to return multiple attributes with varying
  degrees of scale and accuracy"* — this describes the benchmark's
  confidence-calibration goal, not a mandatory shape every instance
  must match. Some seed questions fit cleanly into one category;
  others span two or more or are loose enough to adapt.

Then load only the reference file you need:

- `datasets/pkdb_bench/docs/instance_selection_priorities.md`
  Read when choosing the next new instance to create. It is a
  lightweight picker guide, not a registration validator: use it to
  balance seed-first preference, category gaps, domain variety, and
  basic evaluation viability before staging a candidate.
- `datasets/pkdb_bench/docs/domain_taxonomy.md`
  Read when creating a new instance or setting/changing
  `draft.json::domain`. Prefer the `Family/Subdomain` pattern while
  keeping the field free text.
- `datasets/pkdb_bench/docs/benchmark_domain_survey.md`
  Background motivation for the domain taxonomy. Read only when
  revising domain strategy or reviewing whether the taxonomy still
  matches benchmark goals.
- `references/artifact-contract.md`
  Read when creating or modifying staged files. Includes the canonical
  `operation_type` vocabulary — consult before setting that field.
- `references/workflows.md`
  Read when choosing how to create, audit, challenge, or prepare
  registration. The `create-instance` mode now ends with a **creator
  preflight** that must run before reporting `ready`.
- `references/sparql-gotchas.md`
  Read whenever you write or modify a `.sparql` query. Documents the
  `wdt:` vs. `p:/ps:` rank pitfall and other recurring SPARQL footguns
  that have shipped real bugs in this benchmark.

For a well-formed example of a `source_defined` instance, see:
`datasets/pkdb_bench/staging/transboundary_rivers_by_continent_over_1000_km.*`
(especially `csv`, `draft.json`, and `curation.json`; `comments.json` is optional)

## When To Use This Skill

Trigger this skill when the user asks to:

- create a new PKDB-Bench instance
- repair an existing staged instance based on audit findings
- challenge or narrow the scope of a staged instance
- reconcile a same-query duel into canonical files
- prepare an instance for registration
- decide between exhaustive / source_defined / partially_supplemented /
  candidate_rebuild claim types

For **audit-only tasks** — checking source grounding, coverage,
normalization, metadata, or registration readiness without editing the
canonical files — use `pkdb-bench-audit` instead. That skill enforces
append-only writes to `<id>.comments.json`.

## Hard Rules

1. Do not generate ground truth from parametric knowledge alone.
2. Separate source-grounded facts from curation decisions.
3. Never imply exhaustiveness unless the sources actually support it.
4. Treat `draft.json` as candidate registration metadata, not as the full curation state.
5. Preserve why rows were added, excluded, normalized, or left uncertain in `curation.json`.

## Default Workflow

1. Identify the mode:
   - `create-instance`
   - `repair-instance` (apply fixes that an auditor recommended)
   - `challenge-scope`
   - `prepare-registration`
   - `compare-results` (reconcile a same-query duel into canonical files)

   For `audit-instance`, switch to the `pkdb-bench-audit` skill.
2. Read `references/workflows.md` and follow the matching mode.
3. Read `references/artifact-contract.md` before writing staged files.
4. Update artifacts consistently:
   - `<id>.csv`
   - `<id>.draft.json`
   - `<id>.curation.json`
   - optional `<id>.comments.json`
5. Summarize:
   - what sources were used
   - what claim type the instance now makes
   - what remains uncertain

## Claim Types

Every instance should be treated as one of these:

- `exhaustive`
- `source_defined`
- `partially_supplemented`
- `candidate_rebuild`

If the current docs do not have an explicit field for this, record it clearly in
`curation.json`.

## Output Standard

At the end of a curation task, report:

- chosen category and any secondary category
- claim type
- source URLs used
- files written or changed
- remaining open questions

If the instance is not safe to register, say so plainly and name the blocking
issue.

## Severity Taxonomy

When you receive findings (from an auditor, a reviewer, or your own
preflight) and when you report findings on someone else's work, classify
each one as exactly one of:

- **BLOCKING** — must be resolved before registration. The instance is
  factually wrong, structurally broken, or makes a scope claim the
  sources do not support. Examples: duplicate composite PK, universe
  count mismatches an authoritative source, `query_category` is `null`,
  `operation_type` is not in the vocabulary, schema columns do not
  match the CSV, `partially_supplemented` claim with no
  `proposed_supplements` audit trail.

- **NON-BLOCKING HYGIENE** — should be cleaned up but does not affect
  correctness. Examples: stale chat_summary referencing a prior state,
  type-inconsistent fields (boolean where string is expected), wording
  in narrative fields that no longer matches the current artifacts,
  whole-file reformatting noise in a metadata-only patch.

- **SUGGESTION** — a judgment call the next curator could reasonably
  decide either way. Examples: alternative schema shapes (per-triple
  vs. delimited text), down-weighting policies, a stricter source tier
  that would tighten an honest scope claim.

When applying a repair, address every BLOCKING finding before reporting
ready. Address NON-BLOCKING HYGIENE findings opportunistically (in the
same pass if cheap, in a follow-up if not). Treat SUGGESTIONS as input
to discussion, not as required actions — but record the decision in
`curation.json::decision_log` so the next reader does not re-litigate.
