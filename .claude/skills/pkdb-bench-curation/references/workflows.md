# Workflows

Choose exactly one primary mode.

## 1. create-instance

Use when starting a new staged instance.

Steps:

1. **Choose an under-covered category** by checking current
   coverage against the per-category targets in `README.md`. When
   choosing among multiple plausible next instances, also consult
   `datasets/pkdb_bench/docs/instance_selection_priorities.md` so you
   account for staged work, untouched seeds, domain variety, and the
   fast viability gate.
2. **Choose the question.** Walk this priority:

   a. **First, look at `datasets/pkdb_bench/docs/example_questions.txt`** —
      the human-curated seed working set. The seed questions are not
      pre-labeled per category; some fit cleanly into one category,
      others span two or more, some are loose enough to adapt. Pick a
      seed question that matches the under-covered category — verbatim
      or as a close sourceable equivalent.

   b. **If no seed question fits**, the agent may adapt a seed
      question or propose an original category-targeted question.
      "No fit" means the seed is unsuitable (e.g., post-cutoff,
      sources unstable), already labeled in this benchmark,
      duplicates existing coverage, or doesn't actually exercise the
      chosen category's DB analog.

   c. **Document the choice** in `curation.json::decision_log`:
      state whether you used a seed verbatim, adapted a seed (and
      what changed), or proposed an original (and why no seed fit).

   Before staging, fill the tiny candidate card from
   `instance_selection_priorities.md` in `decision_log`: seed/provenance,
   category fit, source plan, expected shape, domain, risks/adaptation,
   and stage/adapt/abandon decision. Keep this brief; it should explain
   the irreversible choice, not become a second protocol.

   When setting `draft.json::domain`, use
   `datasets/pkdb_bench/docs/domain_taxonomy.md`: prefer
   `Family/Subdomain`, keep the subdomain natural, and document any
   unusual fit in `decision_log`.

   **Logical decomposition is separate from URL count.** Keep
   `source_count` consistent with the current UI/protocol contract
   (`source_count == len(source_urls)`); document any logical
   joins/unions/intersections/chained-traversals in `db_analog` and
   `decision_log` regardless of how many distinct URLs the instance
   cites.
   - `implicit_join`, `set_operation`, `multi_hop` require a logical
     join / union / intersection / chained-traversal **shape**. A
     single SPARQL endpoint or single Wikipedia URL can still be
     acceptable for these categories if the logical decomposition is
     explicit in `db_analog` and documented in `decision_log` — but
     `source_count` still reflects `len(source_urls)`, not the
     number of logical legs.
   - Other categories may prefer multi-source / multi-page evidence
     when it improves the benchmark (e.g., per-entity Wikipedia pages
     for `multi_attribute_projection` exercise per-cell confidence
     better), but single-source instances are acceptable when they
     honestly match the category's DB analog and produce a useful
     table.

3. Find source-grounded rows from approved sources.
   Source tier order (prefer higher tiers): Wikidata SPARQL > manual Wikipedia > web search.
   See `datasets/pkdb_bench/docs/curation_protocol.md` §4 for the full tier rules.
4. Decide the claim type:
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

5. If `source_count > 1` (join / union / intersect queries), decompose first:
   - Collect each source table independently before combining.
   - Use the `db_analog` field as the assembly blueprint.
   - For **join**: collect both tables, identify the shared key, merge on that key.
     Only rows present in all sources appear in the GT; document key-matching decisions.
   - For **union**: collect each source separately, row-bind, then handle overlapping
     rows (keep distinct or merge duplicates — document the choice).
   - For **intersect**: collect each source, then intersect on the primary key;
     only rows present in every source survive.
   - Record each source's row contribution in `curation.json` under `source_coverage_notes`.
   - Identify each `source_urls[i]` by role (`universe` / `ranking` /
     `value` / `validation` / `entity-detail`) using the convention in
     `references/artifact-contract.md`. A multi-source instance with
     no clearly identified `universe` source is a red flag.
6. Write the staged CSV.
7. Write `draft.json` with a truthful query/title and metadata. Set
   `domain` using `domain_taxonomy.md` unless a legacy repair is only
   preserving an existing understandable label.
8. Write `curation.json` with why the row set is what it is.
9. **Run the creator preflight** (next subsection) before reporting
   `ready`. Auditors will catch most things that slip through, but
   preflight catches a class of bug that an auditor's existence does
   not justify shipping.

Do not register directly.

### Creator preflight (run before reporting `ready`)

This is **lightweight pre-stage validation**, not a full audit. The goal
is to surface bugs that the create-instance flow itself would miss
because the agent and the validator are the same person. Run every
applicable check; if any fails, fix or document before reporting.

1. **Source rerun matches CSV** (Tier A only)
   For Tier A (Wikidata SPARQL): re-run the `.sparql` file via the
   pipeline and confirm the row set is identical to what is staged.
   The staged CSV must be reproducible from the cited query alone.

2. **PK uniqueness**
   `csv.duplicated(subset=draft["primary_key"]).sum() == 0`. A
   duplicate composite PK is always a structural bug, never an
   intentional choice.

3. **Schema consistency**
   - `list(csv.columns) == draft["columns"]`
   - `set(draft["column_types"]) == set(draft["columns"])`
   - `draft["source_count"] == len(draft["source_urls"])`
   - `draft["query_category"]` is a valid 15-category id; not `null`
   - `draft["operation_type"]` is a value listed in
     `references/artifact-contract.md` "Operation Type Vocabulary"

4. **Category ↔ operation compatibility**
   Mismatch is allowed but should be deliberate. Common pairings:
   `implicit_join`/`multi_hop` → `join`; `set_filter`/`temporal`/
   `geographic` → `filter`; `set_operation` → `union` /
   `intersect` / `union+filter`; `aggregation` → `aggregation`;
   `multi_attribute_projection` → `projection`; `factual_lookup` →
   `projection`; `qualitative` → `filter` / `projection`; `causal` →
   `filter` / `projection` / `aggregation` (the protocol §4 FSI
   threshold example uses `filter`). If your pairing is not on this
   list, document the reason in
   `curation.json::decision_log`.

5. **Universe-size sanity check** (when applicable)
   When the universe is named in the query (e.g., "landlocked
   countries in Africa", "G7 countries", "EU member states"), compare
   the count against an authoritative cited source. The
   `wdt:`-vs-`p:/ps:` rank bug, the most expensive bug this benchmark
   has shipped, is caught here. See `references/sparql-gotchas.md` §1.

6. **No QID-shaped values in label columns**
   Tier A only. A row with `Q\d+` in a label column means the
   `wikibase:label` service failed to resolve and the bug must be
   fixed in the SPARQL, not patched in the CSV.

7. **`claim_type` is recorded in `curation.json`**
   One of `exhaustive` / `source_defined` / `partially_supplemented`
   / `candidate_rebuild`. Match the language in `table_title`,
   `db_analog`, and `_extraction_notes` to the chosen claim.

8. **`proposed_supplements` consistency**
   If `claim_type` is `partially_supplemented`,
   `curation.json::proposed_supplements` must be a non-empty list
   with `applied: true` on every entry that produced a row in the
   CSV. If `claim_type` is **not** `partially_supplemented`,
   `proposed_supplements` should be absent or empty.

9. **Row count within protocol bounds**
   `len(csv) >= 10` (per protocol §5.2). Tables over 500 rows must
   be justified in `_extraction_notes` (usually only for
   `hierarchical_decomposition` or `scaled_enumeration`).

10. **Question selection and shape (soft check, document don't block)**
    Verify and briefly document each of these in `decision_log`:

    a. **Seed provenance.** Did the instance come from a seed
       question (`example_questions.txt`) verbatim, an adaptation,
       or an original? If adapted or original, the reason should be
       documented (no seed fit / seed already labeled / duplicates
       coverage / etc.).

    b. **Category fit.** Does the question match the selected
       category's DB analog (per `categories.txt`)? A query whose
       `db_analog` doesn't structurally match the category's analog
       is in the wrong category, not a wrong query.

    c. **Calibration capacity.** Does the table provide enough
       non-key attributes to exercise per-cell confidence /
       calibration where the category calls for it? Naturally narrow
       categories (`factual_lookup`, `set_filter`, `top_k`,
       `aggregation`, `temporal`, `geographic`, `qualitative`) are
       exempt; categories that exist to test multi-attribute
       confidence (`multi_attribute_projection`,
       `hierarchical_decomposition`, `scaled_enumeration`) should
       not collapse to a single non-key column without a documented
       reason.

    d. **Logical decomposition for join-shape categories.** If the
       instance is single-source AND the chosen category is
       `implicit_join`, `set_operation`, or `multi_hop`, is the
       logical join / union / intersection / chained-traversal shape
       still explicit in `db_analog` and `decision_log`? A
       single-source query for one of these categories needs a
       visible logical decomposition; if the decomposition isn't
       there, the category is wrong.

    A soft failure on any of these means: document the situation in
    `decision_log` and stage. The audit classifies findings on its
    own merits using the two-axis severity taxonomy — do not
    pre-declare a classification for the auditor.

If any check surfaces an issue, **fix or document it before reporting
ready** — do not punt to the auditor. The auditor's job is to catch
what preflight cannot, not to do preflight's job.

## 2. audit-instance

**Audit work has moved to the sibling skill `pkdb-bench-audit`.**

Audit is append-only (reads staged files, writes only to
`<id>.comments.json`). Reconciling an audit into canonical edits is a
different ownership profile and stays in this skill as `repair-instance`
(apply the fixes an auditor recommended). The sibling skill exists so
audit and repair can be triggered independently without one skill having
to know both ownership models.

If the user asked for an audit, switch to `pkdb-bench-audit` and read
its `references/audit-bundles.md` and `references/comments-schema.md`.
If the user asked you to apply fixes that an auditor identified, stay
here and follow `repair-instance` (treat it as `create-instance` applied
to existing staged files; preserve `comments.json` and prior audit
history).

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

For multi-agent collaboration patterns (default Stage→Audit ownership rule,
parallel-staging ID rule, experimental same-query duel layout, and the
reconciliation persistence rule), see the "Agent Collaboration Patterns"
section of `datasets/pkdb_bench/docs/agent_handoff_bundle.md`. That file is
the agent-agnostic source of truth — this skill mirrors it but does not
restate it.
