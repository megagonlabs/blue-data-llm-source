---
name: pkdb-bench-audit
description: Use when auditing an already-staged PKDB-Bench instance under datasets/pkdb_bench/staging/<id>.*. Audit work is append-only — it reads CSV/draft/curation files and writes findings to <id>.comments.json without modifying the canonical artifacts. Use this for source-grounding checks, row-correctness sweeps, scope verification, metadata validation, and registration-readiness verdicts. For creating new instances, repairing existing ones, or reconciling a same-query duel into canonical files, use pkdb-bench-curation instead.
---

# PKDB-Bench Audit

Use this skill for audit-only work on already-staged PKDB-Bench instances.
Audit has a different ownership model from creation: append-only,
source-checking, registration judgment.

## Hard Rules

1. **Append-only.** You may read all staged files but you may only write
   to `datasets/pkdb_bench/staging/<id>.comments.json`. Do NOT edit:
   - `<id>.csv`
   - `<id>.draft.json`
   - `<id>.curation.json`
2. Do not delete or rewrite existing entries in `comments.json`. Append.
3. Audit findings go in the `chat_events` + `chat_history` lists, matching
   the schema the curation UI uses. See `references/comments-schema.md`.
4. Cite source URL + locator (page section, list position, table cell)
   for every finding that rests on the source set.
5. Classify each finding as factual, structural, or scope-related.
6. If the audit identifies fixes, **recommend** them — do not apply them.
   The human or a `pkdb-bench-curation` agent applies fixes.
7. Be policy-faithful, not perfectionist. If a source tier is allowed by
   the protocol and honestly declared in metadata, do not block
   registration only because stronger sources would be preferable.

## When To Use This Skill

Trigger this skill when the user asks to:

- audit a staged instance
- check whether a staged instance is ready for registration
- verify source grounding, completeness, or scope
- check normalization, duplicates, or schema correctness
- act as agent B in a Stage → Audit handoff (see
  `datasets/pkdb_bench/docs/agent_handoff_bundle.md` →
  "Agent Collaboration Patterns")

Do NOT use this skill for:

- creating a new staged instance → use `pkdb-bench-curation`
- editing or repairing staged files based on audit findings → use
  `pkdb-bench-curation`
- reconciling a same-query duel and writing canonical files → use
  `pkdb-bench-curation`'s `compare-results` mode

## Default Workflow

1. **Pick the audit bundle** from `references/audit-bundles.md`:
   - **Data integrity sweep** — best first pass after staging or major
     edits. Row correctness, duplicates, normalization, table-shape,
     cardinality.
   - **Source and scope sweep** — when source-vs-query mismatch is the
     main risk.
   - **Metadata and registration sweep** — near the end, to verify cfg
     metadata and benchmark fit.
   - **Full pre-registration sweep** — broadest single pass, with a final
     Ready / Not ready verdict. Default if unsure.
2. **Read the staged instance and its sources**:
   - `datasets/pkdb_bench/staging/<id>.csv`
   - `datasets/pkdb_bench/staging/<id>.draft.json`
   - `datasets/pkdb_bench/staging/<id>.curation.json`
   - `datasets/pkdb_bench/staging/<id>.comments.json` (if it exists —
     read prior audits before adding yours)
   - the URLs in `draft.json::source_urls`
3. **Run the audit bundle** and write a structured, numbered response.
   Cite sources for every finding. Classify each issue.
4. **Append the audit** to `<id>.comments.json` per
   `references/comments-schema.md`. Do not overwrite prior entries.
5. **Return a condensed verdict block** to the user:
   - Ready / Not ready (Ready requires zero BLOCKING findings)
   - Counts by severity: BLOCKING / NON-BLOCKING HYGIENE / SUGGESTION
   - Counts by issue type: factual / structural / scope-related
   - 3–5 minimal fixes needed (if any)
   - Path written to

   See the Severity Taxonomy section below for the two-axis tagging
   contract — every finding must carry one tag from each axis, e.g.
   `(structural, BLOCKING)`.

## Required Reading Before First Audit

If you have not audited in this benchmark before, read:

- `datasets/pkdb_bench/docs/README.md`
- `datasets/pkdb_bench/docs/curation_protocol.md`
- `datasets/pkdb_bench/docs/categories.txt`
- `datasets/pkdb_bench/docs/agent_handoff_bundle.md` —
  especially the "Agent Collaboration Patterns" section

The handoff bundle is the agent-agnostic source of truth for the
Stage → Audit ownership contract; this skill mirrors it.

## Output Standard

At the end of an audit task, the user should be able to read your
condensed verdict block and decide:

- whether to register
- whether to send the instance back for repair (and to which agent)
- whether to discard or rebuild

If your verdict cannot support that decision, the audit is too thin —
expand it before returning.

## Severity Taxonomy

Every audit finding has **two orthogonal classifications**. They are
not alternatives — every finding gets one tag from each axis.

**Axis 1 — Issue type** (per Hard Rule 5):

- `factual` — a value in the CSV is wrong relative to the cited source
- `structural` — schema, PK, column-types, or operation vocabulary is broken
- `scope-related` — the claim made by `table_title` / `db_analog` /
  `claim_type` overstates or understates what the sources support

**Axis 2 — Severity** (this section):

- **BLOCKING** — must be resolved before registration. Examples:
  duplicate composite PK (structural+blocking), universe count
  mismatches an authoritative source (factual+blocking),
  `query_category` is `null` (structural+blocking), `operation_type`
  not in the vocabulary (structural+blocking), `partially_supplemented`
  claim with no `proposed_supplements` audit trail (scope+blocking).

- **NON-BLOCKING HYGIENE** — should be cleaned up but does not affect
  correctness. Examples: stale `chat_summary` referencing a prior state
  (structural+hygiene), type-inconsistent fields like a boolean where a
  string is expected (structural+hygiene), wording in narrative fields
  that no longer matches the current artifacts (scope+hygiene).

- **SUGGESTION** — a judgment call the next curator could reasonably
  decide either way. Examples: alternative schema shapes
  (structural+suggestion), down-weighting policies for skewed rows
  (scope-related+suggestion), a stricter source tier that would tighten
  an already-honest scope claim (scope-related+suggestion).

**Reporting requirements:**

- Each numbered finding must carry both tags, e.g. `(structural,
  BLOCKING)` or `(scope, NON-BLOCKING HYGIENE)`.
- The verdict block must report counts by severity (e.g., "1
  BLOCKING, 2 NON-BLOCKING HYGIENE, 0 SUGGESTION") **and** the counts
  by issue type Hard Rule 5 already requires (e.g., "1 factual, 0
  structural, 2 scope-related"). Both summaries are useful for
  different downstream decisions.
- A `Ready` verdict requires zero BLOCKING findings; NON-BLOCKING
  HYGIENE and SUGGESTION findings do not block registration but should
  be listed so the curator can address them in the same or a follow-up
  pass.

The severity axis is shared with `pkdb-bench-curation`'s
repair-instance flow so findings translate cleanly across the audit ↔
repair handoff.
