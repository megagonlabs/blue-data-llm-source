# PKDB-Bench Agent Handoff Bundle

Use this when handing off a new benchmark-instance creation task to an external
agentic workflow such as Claude Code or Codex.

## Goal

The agent should create **one staged PKDB-Bench instance** that is:

- source-grounded
- category-labeled
- table-shaped
- aligned with the curation protocol
- ready for human review in the curation UI

The target output is a staged instance under `datasets/pkdb_bench/staging/`,
not a directly registered benchmark item.

## Minimum files to provide

These are the core files an agent should read before creating an instance:

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

6. One similar registered CSV from
   [datasets/pkdb_bench/tables](/Users/chen_s/Gits/megagon-internal/blue-llmsource/datasets/pkdb_bench/tables)
   Choose one that matches the intended category or shape.

## Optional but useful context

Give these when relevant:

- [metrics_notes.txt](/Users/chen_s/Gits/megagon-internal/blue-llmsource/datasets/pkdb_bench/docs/metrics_notes.txt:1)
  If the instance should be useful for later calibration-aware analysis.
- [papers.md](/Users/chen_s/Gits/megagon-internal/blue-llmsource/datasets/pkdb_bench/docs/papers.md:1)
  If the agent should understand the research novelty and benchmark positioning.
- [framework_analysis.md](/Users/chen_s/Gits/megagon-internal/blue-llmsource/md_claude_code/analysis/framework_analysis.md:1)
  If the agent should understand why partitioning, schema, or cardinality matter.
- [research_discussion.md](/Users/chen_s/Gits/megagon-internal/blue-llmsource/md_claude_code/analysis/research_discussion.md:1)
  If the agent should be extra careful about schema normalization, duplicates,
  entity resolution, and coverage.

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
  questions, and curation rationale.

- `<id>.comments.json`
  Optional seeded review warnings or doubts for the human curator.

## Minimum required fields for the draft

At minimum, the agent should populate:

- `id`
- `table_title`
- `columns`
- `primary_key`
- `column_types`
- `query_category`
- `db_analog`
- `operation_type`
- `source_count`
- `domain`
- `temporal_sensitivity`
- `gt_construction`
- `temporal_note`
- `source_urls`

## Standard instructions for the agent

Use a prompt like this:

```text
Create one new staged PKDB-Bench instance.

Read and follow:
- datasets/pkdb_bench/docs/README.md
- datasets/pkdb_bench/docs/curation_protocol.md
- datasets/pkdb_bench/docs/categories.txt
- datasets/pkdb_bench/docs/example_questions.txt
- datasets/pkdb_bench/cfg.json

Requirements:
- Use authoritative external sources only.
- Do not generate ground truth from parametric knowledge alone.
- Choose one category from categories.txt and make sure the instance clearly fits it.
- Build a rectangular table with a clear primary key and at least one non-key attribute.
- Normalize values consistently.
- Record source URLs and curation decisions.
- Write the staged outputs:
  - datasets/pkdb_bench/staging/<id>.csv
  - datasets/pkdb_bench/staging/<id>.draft.json
  - datasets/pkdb_bench/staging/<id>.curation.json

Return:
1. the chosen category
2. the query/table title
3. the source URLs
4. any open review questions
5. the file paths written
```

## Recommended extra instruction

If you want the agent to be especially careful:

```text
Do not assume the first source you find is exhaustive.
If the source only supports a curated or source-defined subset, say so explicitly
in the curation record and avoid implying literal exhaustiveness in the draft.
```

## Human review checklist after handoff

After the agent writes the staged files:

1. Open the instance in the curation UI.
2. Check whether the query wording matches the source scope.
3. Run an audit bundle in the chat assistant.
4. Inspect normalization and duplicates.
5. Review the curation record.
6. Only then decide whether to register.
