---
name: pkdb-bench-curation
description: Use when creating, auditing, challenging, or preparing PKDB-Bench instances for registration. This skill is for source-grounded benchmark curation work that writes or reviews staged artifacts under datasets/pkdb_bench/staging/, especially when the task involves source-defined vs exhaustive scope, row coverage, cardinality, schema design, curation decisions, or registration readiness.
---

# PKDB-Bench Curation

Use this skill for PKDB-Bench instance work, not for general dataset editing.

The benchmark artifact is not just a CSV. Every instance has four distinct layers:

- query intent
- candidate benchmark registration metadata
- ground-truth table
- curation rationale / source interpretation

Read the core benchmark docs first:

- `md_claude_code/pkdb_bench/README.md`
- `md_claude_code/pkdb_bench/curation_protocol.md`
- `md_claude_code/pkdb_bench/categories.txt`

Then load only the reference file you need:

- `references/artifact-contract.md`
  Read when creating or modifying staged files.
- `references/workflows.md`
  Read when choosing how to create, audit, challenge, or prepare registration.

For a well-formed example of a `source_defined` instance, see:
`datasets/pkdb_bench/staging/transboundary_rivers_by_continent_over_1000_km.*`
(especially `csv`, `draft.json`, and `curation.json`; `comments.json` is optional)

## When To Use This Skill

Trigger this skill when the user asks to:

- create a new PKDB-Bench instance
- audit or repair an existing staged instance
- check source grounding, coverage, or completeness
- reconcile source-defined vs exhaustive scope
- prepare an instance for registration
- compare two agentic curation results

## Hard Rules

1. Do not generate ground truth from parametric knowledge alone.
2. Separate source-grounded facts from curation decisions.
3. Never imply exhaustiveness unless the sources actually support it.
4. Treat `draft.json` as candidate registration metadata, not as the full curation state.
5. Preserve why rows were added, excluded, normalized, or left uncertain in `curation.json`.

## Default Workflow

1. Identify the mode:
   - `create-instance`
   - `audit-instance`
   - `challenge-scope`
   - `prepare-registration`
   - `compare-results`
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
