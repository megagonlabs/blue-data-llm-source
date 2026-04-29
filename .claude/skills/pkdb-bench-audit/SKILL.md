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
   for every finding. Say whether the evidence comes from the cited
   ground-truth source set or from a validation/challenge source.
5. Classify each finding as factual, structural, or scope-related.
6. If the audit identifies fixes, **recommend** them — do not apply them.
   The human or a `pkdb-bench-curation` agent applies fixes.
7. Be policy-faithful, not perfectionist. If a source tier is allowed by
   the protocol and honestly declared in metadata, do not block
   registration only because stronger sources would be preferable.

## Validation Source Policy

Audits are **cited-source-first and Wiki-primary**.

1. The URLs in `draft.json::source_urls` define the current staged
   ground truth. Always audit row correctness, row support, and metadata
   against those cited sources first.
2. After reading the cited sources, run a bounded **Wiki-family source
   discovery pass**. The goal is to detect whether the staged instance is
   missing a Wikipedia or Wikidata source that is necessary to answer the
   natural-language query.
3. Prefer Wiki-family validation before broader web search:
   - linked Wikipedia list pages
   - sibling list pages, redirects, and "See also" pages
   - individual Wikipedia entity pages
   - Wikidata entity pages
   - SPARQL count/rank sanity checks
4. Use non-Wiki external sources only when one of the following concrete
   triggers holds:
   - Wiki-family sources are missing entirely for the query universe,
   - Wiki-family sources contradict each other across revisions,
   - Wiki-family sources contain explicit "incomplete" or "list of
     notable" disclaimers,
   - Wiki-family sources are explicitly time-unstable for the staged
     temporal scope, or
   - the user explicitly asks for broader validation.

   These sources can challenge the staged source set; they do not
   automatically become registered ground truth.
5. General web search is allowed for discovery, but findings must rest on
   cited URLs with locators. Parametric memory is never evidence.

During the Wiki-family discovery pass, ask:

- Is there a more direct Wikipedia list/table page for the query than
  the current cited source set?
- Is the current source only an entity page, overview page, or partial
  table when the query asks for a ranked, partitioned, or exhaustive set?
- Do linked pages, redirects, sibling list pages, or Wikidata statements
  imply a different row universe, ranking rule, or partition boundary?
- For Wikidata/SPARQL instances, does a simple count/rank sanity check
  against Wikipedia list pages reveal missing rows, rank filtering
  issues, or ontology gaps?

If you discover a missing source:

- classify it as **BLOCKING** when the discovered missing source
  materially changes the row universe, partition boundary, ranking
  rule, or claim type that the staged instance asserts;
- classify it as **NON-BLOCKING HYGIENE** when the discovered missing
  source is provenance-only — the staged row set and scope claim
  survive without it, but citing it would make the curation record
  clearer;
- classify it as **SUGGESTION** when it is merely an optional stronger
  or cleaner source.

If the bounded discovery pass returns no candidate but you still suspect
a missing source exists (e.g., the query universe is large but no
Wikipedia list page surfaced, or the cited source is an entity page
where a list page would be more direct), record the suspected source
type and the search path you checked so the next auditor or curator does
not silently re-run the same dead-end pass. Default severity is
**SUGGESTION**; classify it as **NON-BLOCKING HYGIENE** only when the
current cited source set is visibly thin for the asserted query scope.

Do not edit `source_urls` or the CSV during audit. If a discovered source
should become part of the ground truth, recommend a `pkdb-bench-curation`
repair that adds it to `source_urls`, updates `source_count`,
`gt_construction`, `claim_type`, `temporal_note`, and records the choice
in `decision_log`.

## Cardinality And Partition Checks

Always validate cardinality at the level implied by the query, not only
at the whole-table level.

For partitioned, ranked, or hierarchical instances (`top_k`,
`scaled_enumeration`, `hierarchical_decomposition`, and similar shapes),
check all of the following:

- total row count;
- expected partitions and rows per partition;
- whether each partition uses the same ranking/filtering convention;
- whether the cited sources support the top-K or membership rule inside
  each partition;
- whether any higher-ranked or in-scope entity is omitted even though
  the table has the expected total row count.

A table can have the right total number of rows and still fail
cardinality if a partition is under-filled, over-filled, sourced from a
different convention, or populated with lower-ranked rows while
higher-ranked rows are omitted. Classify such findings by their effect
on the asserted row universe, partition boundary, ranking rule, or claim
type.

If the cited source set itself uses partition-specific conventions,
document the asymmetry and assess whether the staged `claim_type`,
`table_title`, and `db_analog` honestly describe that source-defined
scope. If they do not, recommend narrowing or rebuilding the instance.

## Domain Metadata Checks

For metadata or registration audits, read
`datasets/pkdb_bench/docs/domain_taxonomy.md` when checking
`draft.json::domain`.

- Missing `domain` is structural and must be fixed before registration.
- Actively misleading `domain` is structural; usually NON-BLOCKING
  HYGIENE if the rest of the metadata is correct, but can become
  BLOCKING if it would mislead benchmark stratification.
- Legacy but understandable labels are SUGGESTION at most.
- Prefer `Family/Subdomain` labels for new or repaired instances, but
  do not require churn on old labels only for style.

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
3. **Run the Wiki-family source discovery pass** from the Validation
   Source Policy. Keep it bounded: look for direct list/table pages,
   linked/sibling pages, entity pages, Wikidata pages, or SPARQL sanity
   checks that could materially change scope, coverage, ranking, or
   provenance. Use broader web search only under the policy above.
4. For metadata / registration audits, check `draft.json::domain`
   against `datasets/pkdb_bench/docs/domain_taxonomy.md`.
5. **Run the audit bundle** and write a structured, numbered response.
   Cite sources for every finding. Classify each issue.
6. **Append the audit** to `<id>.comments.json` per
   `references/comments-schema.md`. Do not overwrite prior entries.
7. **Return a condensed verdict block** to the user:
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
