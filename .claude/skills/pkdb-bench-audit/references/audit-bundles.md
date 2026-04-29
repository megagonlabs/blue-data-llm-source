# Audit Bundles

These four bundles mirror `CHAT_PROMPT_BUNDLES` in
[curation_ui/app.py](../../../../curation_ui/app.py). The UI constant is
the source of truth for the prompt text; this file is a snapshot kept in
sync by hand. **If you change one, update both.**

Pick exactly one bundle per audit. Default is **Full pre-registration
sweep** if you are unsure or only running one pass.

Before running any bundle, follow `SKILL.md`'s Validation Source Policy:
audit the cited source set first, then run a bounded Wiki-family source
discovery pass to catch missing Wikipedia/Wikidata list, entity, sibling,
redirect, "See also", or SPARQL sanity-check sources. Non-Wiki external
sources are allowed only for validation/challenge under the concrete
triggers listed in `SKILL.md` § Validation Source Policy item 4 — do
not infer triggers loosely. Discovered sources do not automatically
become ground truth; recommend a curation repair if they should be
added to the staged source set.

---

## 1. Data integrity sweep

**Use when:** first pass after staging or major edits. Checks row
correctness, duplicates, and normalization in one run.

**Prompt:**

> Run a data-integrity sweep over this instance using the cited source
> set only for row-level correctness. In one response, do all of the
> following:
>
> 1. detect missing, incorrect, duplicate, or unsupported rows relative
>    to the cited source set,
> 2. check whether values are normalized and match the declared
>    column_types,
> 3. flag any near-duplicate entities, primary-key issues, or
>    multi-valued cells that break the intended table shape,
> 4. estimate whether the current total row count, per-partition row
>    count when relevant, and unique-key count look plausible for the
>    stated query and cited source set,
> 5. note whether the Wiki-family source discovery pass found a missing
>    source that could change scope, coverage, ranking, or provenance,
> 6. say whether problems are factual, structural, or scope-related,
> 7. propose a corrected table only if changes are actually needed.
>
> If you would propose table edits, describe them in the audit
> response — do NOT write them to the CSV (audit is append-only).

---

## 2. Source and scope sweep

**Use when:** the main risk is whether the cited sources actually support
the query wording and intended benchmark scope.

**Prompt:**

> Review the source grounding and scope of this instance in one pass,
> using the current benchmark policy rather than an idealized external
> standard. Do not equate "non-authoritative" with "invalid" if the
> current source tier is allowed and honestly declared, but keep
> coverage checks rigorous: assess whether the cited sources actually
> support every row and the claimed scope. Before concluding, run the
> bounded Wiki-family source discovery pass from `SKILL.md` and decide
> whether the staged source set is missing a more direct Wikipedia or
> Wikidata source. In one response, do all of the following:
>
> 1. summarize what each cited source contributes, including its role
>    (`universe` / `ranking` / `value` / `validation` / `entity-detail`)
>    per `references/artifact-contract.md` § Source Roles, and flag any
>    multi-source instance with no clearly identified `universe`,
> 2. assess whether the table is complete for the stated query scope,
> 3. estimate the expected coverage or row-count range implied by the
>    sources, including per-partition counts for partitioned queries,
> 4. identify any mismatch between the query wording and what the
>    sources actually support,
> 5. identify any missing Wiki-family source that materially affects
>    row universe, ranking rule, partition boundary, or provenance,
> 6. challenge weak, unstable, or non-authoritative sources,
> 7. decide whether the instance is exhaustive, source-defined, or
>    under-grounded,
> 8. recommend whether the query should be kept, narrowed, rebuilt, or
>    rejected, clearly separating blocking issues from non-blocking
>    stronger-source upgrades.

---

## 3. Metadata and registration sweep

**Use when:** near the end of curation, to verify cfg metadata,
benchmark fit, and whether the instance is ready to register.

**Prompt:**

> Perform a metadata and registration-readiness sweep using the current
> benchmark protocol as the decision standard. Be policy-faithful, not
> perfectionist: if a source tier is allowed by the protocol and
> honestly declared in metadata, do not treat "stronger sources would be
> better" as a blocker by itself. Separate blocking issues under current
> policy from non-blocking improvement suggestions. In one response, do
> all of the following:
>
> 1. validate the benchmark registration draft against the table and
>    sources,
> 2. assess whether db_analog, query_category, operation_type,
>    primary_key, column_types, gt_construction, temporal_note,
>    temporal_sensitivity, source_count, domain (using
>    `datasets/pkdb_bench/docs/domain_taxonomy.md`), and source_urls
>    are correct,
> 3. assess whether the current total and per-partition row count /
>    expected cardinality story is documented clearly enough for
>    registration,
> 4. assess whether the content is appropriate for LLM
>    parametric-knowledge benchmarking,
> 5. check whether the instance would support later per-type and
>    calibration analysis without misleading metadata,
> 6. return Ready / Not ready,
> 7. list the blocking issues and the minimal exact field changes
>    needed.

---

## 4. Full pre-registration sweep (default)

**Use when:** you can only run one pass, or the instance is being
prepared for registration. Broadest single audit.

**Prompt:**

> Perform a full pre-registration review of this staged instance using
> the cited source set and current benchmark policy. Be policy-faithful,
> not perfectionist: if the source tier is allowed and honestly
> declared, do not block registration only because stronger sources
> could exist. At the same time, keep coverage checking strict and
> assess whether the cited sources truly support the rows and claimed
> scope. Run the bounded Wiki-family source discovery pass from
> `SKILL.md` so missing Wikipedia/Wikidata sources are surfaced before
> broader web validation. Cover all of the following in one response:
>
> 1. row-level correctness and unsupported / missing rows relative to
>    the cited source set,
> 2. duplicates and primary-key problems,
> 3. normalization, table-shape quality, and column-type consistency,
> 4. expected total and per-partition row-count / cardinality estimate,
>    partition sanity, and unique-key sanity,
> 5. missing Wiki-family source check: whether a more direct Wikipedia
>    or Wikidata source exists and would change scope, coverage, ranking,
>    or provenance,
> 6. source quality, source tier, source-to-query scope match, and
>    whether each `source_urls[i]` has a documented role
>    (`universe` / `ranking` / `value` / `validation` / `entity-detail`)
>    per `references/artifact-contract.md` § Source Roles,
> 7. table completeness for the intended scope,
> 8. cfg metadata correctness, including whether `domain` follows
>    `datasets/pkdb_bench/docs/domain_taxonomy.md` closely enough for
>    benchmark stratification,
> 9. LLM-knowable fit for the benchmark,
> 10. whether this instance helps the benchmark's intended multi-source /
>    structured extraction story,
> 11. final Ready / Not ready judgment, clearly separating blocking
>     issues under current policy from non-blocking upgrade suggestions.
>
> If you would propose table corrections, describe them in the audit
> response — do NOT write them to the CSV (audit is append-only).
> Separately list any recommended cfg field changes.

---

## After running any bundle

For every finding:

- classify as **factual** / **structural** / **scope-related**
- cite source URL + locator (page section, list position, table cell)
- say whether each cited source is part of the staged ground-truth
  source set or an audit validation/challenge source

Then append the full audit transcript to `<id>.comments.json` per
`comments-schema.md`, and return a condensed verdict block to the user
(Ready/Not ready, counts by severity, counts by issue type, 3–5 minimal
fixes, path written).
