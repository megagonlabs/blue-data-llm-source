# Workflows

Choose exactly one primary mode.

## 1. create-instance

Use when starting a new staged instance.

Steps:

1. Read benchmark docs and choose one category.
2. Find source-grounded rows from approved sources.
   Source tier order (prefer higher tiers): Wikidata SPARQL > manual Wikipedia > web search.
   See `md_claude_code/pkdb_bench/curation_protocol.md` §4 for the full tier rules.
3. Decide the claim type:
   - exhaustive
   - source_defined
   - partially_supplemented
   - candidate_rebuild

   **Major-source + individual-supplement pattern:** If the primary source covers only
   "major" or "notable" entities (not all qualifying entities), and you add individual
   entity pages to fill omissions, follow these rules:
   - Classify as `partially_supplemented` — not `source_defined` (the scope is broader
     than one source) and not `exhaustive` (the combined set is still not globally complete).
   - Reframe `db_analog` so the FROM clause references the curated source set, not an
     implied global table.
   - For `table_title`: prefer natural scoped language ("major international transboundary
     rivers…") if the source supports that phrase. Use "in the cited source set" only when
     no natural phrase captures the scope honestly. If the only honest title requires
     "in the cited source set," treat that as a signal to leave the instance in staging
     rather than registering it — a benchmark query should be natural, not provenance-aware.
   - Document per-partition supplementation asymmetry explicitly in `source_coverage_notes`.
     If Africa got 4 individual pages added but Europe got none, say so and explain why.
   - **Document-or-ask before supplementing.** Before adding any supplement rows, write
     `proposed_supplements` in `curation.json`. For each proposed entity include: entity
     name, partition/category if relevant, why the main source omitted it, the supplement
     URL, and the expected inclusion criterion.
     Require human review before proceeding when any of these apply:
     - the supplement plan would change the claim type
     - more than ~3 individual entity pages would be added
     - supplementation is asymmetric across partitions without a clear justification
     - individual entity pages are used instead of a systematic list source
     Otherwise the agent may proceed, but `proposed_supplements` must remain in
     `curation.json` as an audit trail.
   - Stopping rule for individual-page supplements: only add an entity's dedicated page if
     (a) it clearly meets the threshold, (b) it is absent from the main list for a sourcing
     reason (the main list is selective, not because the entity is obscure or marginal), and
     (c) you can cite a stable Wikipedia URL for it. Do not supplement from parametric
     knowledge alone — every added entity needs its own cited URL in `source_urls`.

4. If `source_count > 1` (join / union / intersect queries), decompose first:
   - Collect each source table independently before combining.
   - Use the `db_analog` field as the assembly blueprint.
   - For **join**: collect both tables, identify the shared key, merge on that key.
     Only rows present in all sources appear in the GT; document key-matching decisions.
   - For **union**: collect each source separately, row-bind, then handle overlapping
     rows (keep distinct or merge duplicates — document the choice).
   - For **intersect**: collect each source, then intersect on the primary key;
     only rows present in every source survive.
   - Record each source's row contribution in `curation.json` under `source_coverage_notes`.
5. Write the staged CSV.
6. Write `draft.json` with a truthful query/title and metadata.
7. Write `curation.json` with why the row set is what it is.

Do not register directly.

## 2. audit-instance

Use when checking a staged instance for factual or structural problems.

Check:

- unsupported rows
- missing rows relative to the stated source set
- duplicate primary keys
- normalization mismatches
- row count / cardinality plausibility
- metadata vs table mismatches

Classify each issue as:

- factual
- structural
- scope-related

## 3. challenge-scope

Use when the query may be stronger than the sources.

Questions:

- Is this exhaustive or only source-defined?
- Are omitted candidates evidence of incompleteness?
- Is supplementation asymmetric across partitions?
- Does the title / `db_analog` overclaim what the sources support?

Preferred output:

- `keep` — scope is honest; no changes needed
- `narrow` — title/db_analog overclaims; tighten the query or add a scope caveat
- `rebuild` — source set is too thin; return to `create-instance` with a broader or different source
- `reject` — no source can support the query as stated; do not stage

For `narrow`, `rebuild`, or `reject`, record the reasoning explicitly in
`curation.json` so the next curator does not have to rediscover the same scope
problem from scratch.

## 4. prepare-registration

Use when the instance is close to done.

Check:

- registration draft matches CSV
- curation record explains cardinality and scope
- source URLs are stable enough
- downstream evaluation would not be misled
- LLM-knowability: the query must be answerable from parametric knowledge alone.
  Ask: would a closed-book general-purpose model with a pre-2025 cutoff likely know
  these facts from pretraining? Facts should be broadly known, not niche, post-cutoff,
  or recoverable only via tool use. If the answer is mostly "no", flag the instance
  as a poor benchmark candidate before registration.
- Evaluation fit: the schema must be measurable by the benchmark metric suite.
  Check that the table has at least one `key` column and at least one `numeric` or
  `categorical` column. A table with only `text` columns underexercises Key F1 and
  the calibration metrics. If the schema is all-text, flag it before registration.

If not ready, list only the minimal exact changes needed.

If the instance is close but still blocked, prefer updating `curation.json`
and optional `comments.json` with the exact blocker rather than making the next
reviewer reconstruct the issue from chat history alone.

## 5. compare-results

Use when two agents disagree.

Compare:

- rows only in A
- rows only in B
- conflicting values
- conflicting source interpretations
- different claim types

Do not merge silently. Record the reconciliation logic in `curation.json`.
