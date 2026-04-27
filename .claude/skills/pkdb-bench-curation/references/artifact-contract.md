# Artifact Contract

Use this file when creating or editing staged PKDB-Bench instances.

## Required Staged Files

- `datasets/pkdb_bench/staging/<id>.csv`
- `datasets/pkdb_bench/staging/<id>.draft.json`
- `datasets/pkdb_bench/staging/<id>.curation.json`

Optional:

- `datasets/pkdb_bench/staging/<id>.comments.json`

## Meaning Of Each File

- `csv`
  Candidate ground-truth table only.
- `draft.json`
  Candidate benchmark registration metadata only.
- `curation.json`
  Source coverage, decision log, normalization choices, open questions, claim type.
- `comments.json`
  Review comments, doubts, and chat history.

## Draft Minimum

Populate at least:

- `id`
- `table_title`
- `columns`
- `primary_key`
- `column_types`
- `query_category`
- `secondary_category` (optional — use when a second category is an equally valid label)
- `db_analog`
- `operation_type`
- `source_count`
- `domain`
- `temporal_sensitivity`
- `gt_construction`
- `temporal_note`
- `source_urls`
- `_staged_by` — agent or person who created this staging (e.g. `"codex"`, `"human"`)
- `_staged_at` — ISO 8601 timestamp of initial staging
- `_extraction_notes` — one paragraph explaining how the row set was derived, what was supplemented, and what was excluded

## Operation Type Vocabulary

`draft.json::operation_type` must be **exactly one** of these values.
The list mirrors `datasets/pkdb_bench/docs/curation_protocol.md` §7
(cfg.json schema) and must stay in sync with what the curation UI
accepts. If you find yourself wanting a value that is not listed, do
**not** invent one — either pick the closest match and document the
choice in `curation.json::decision_log`, or stop and propose an
extension to the protocol.

Allowed values:

| Value | When to use | Typical `query_category` |
| --- | --- | --- |
| `join` | implicit join across two or more source tables, or chained joins | `implicit_join`, `multi_hop` |
| `union` | row-wise union of two or more source tables | `set_operation` |
| `intersect` | intersection of two source sets | `set_operation` |
| `projection` | single-source multi-column SELECT, no filter | `multi_attribute_projection` |
| `union+filter` | union combined with a filter predicate | `set_operation` (filtered variants) |
| `filter` | single-source with a WHERE predicate | `set_filter`, `temporal`, `geographic` |
| `aggregation` | GROUP BY / COUNT / MAX / MIN over a source | `aggregation` |

Common wrong values that have shipped and were caught in audit:

- `multi_join` — **not in the vocabulary.** Use `join` for chained joins
  (the `multi_hop` query category uses `join` because the structural
  primitive is the same — chained joins are still joins).
- `aggregate` (without the `-ion`) — use `aggregation`.

## Curation Record Minimum

Populate at least:

- `collection_summary`
- `source_coverage_notes`
- `decision_log`
- `normalization_decisions`
- `open_questions`

## Consistency Checks

Before finishing:

- CSV columns match `draft["columns"]`
- primary key is unique
- `source_count == len(source_urls)`
- `column_types` covers every column
- `table_title`, `db_analog`, and curation notes make the same scope claim

## Commonsense vs. Source Roles

Commonsense or domain knowledge may be used only to form search hypotheses,
identify likely omissions, or choose which sources to inspect.

It must not be used as ground truth. Every row in the CSV must trace to at least
one cited URL. Entity-selection judgment belongs in `curation.json`; entity
values belong in cited sources.

When you supplement with individual entity pages, record `proposed_supplements`
in `curation.json` before writing any new rows to the CSV. For each proposed
entity include:

- entity name
- partition or category (if relevant)
- why the main source may have omitted it
- supplement URL
- expected inclusion criterion
- whether human review is needed before proceeding

## Boundary-Aware Fields

When a single categorical label would be misleading, use a text field such as:

- `Continent(s): "Europe; Asia"`

If you do this:

- document the delimiter format in `curation.json`
- reconsider whether that field belongs in the primary key
- do not silently collapse it back to one category
