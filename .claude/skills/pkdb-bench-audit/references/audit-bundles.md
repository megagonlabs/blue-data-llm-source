# Audit Bundles

These four bundles mirror `CHAT_PROMPT_BUNDLES` in
[curation_ui/app.py](../../../../curation_ui/app.py). The UI constant is
the source of truth for the prompt text; this file is a snapshot kept in
sync by hand. **If you change one, update both.**

Pick exactly one bundle per audit. Default is **Full pre-registration
sweep** if you are unsure or only running one pass.

---

## 1. Data integrity sweep

**Use when:** first pass after staging or major edits. Checks row
correctness, duplicates, and normalization in one run.

**Prompt:**

> Run a data-integrity sweep over this instance using the cited source
> set only. In one response, do all of the following:
>
> 1. detect missing, incorrect, duplicate, or unsupported rows relative
>    to the cited source set,
> 2. check whether values are normalized and match the declared
>    column_types,
> 3. flag any near-duplicate entities, primary-key issues, or
>    multi-valued cells that break the intended table shape,
> 4. estimate whether the current row count and unique-key count look
>    plausible for the stated query and cited source set,
> 5. say whether problems are factual, structural, or scope-related,
> 6. propose a corrected table only if changes are actually needed.
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
> support every row and the claimed scope. In one response, do all of
> the following:
>
> 1. summarize what each cited source contributes,
> 2. assess whether the table is complete for the stated query scope,
> 3. estimate the expected coverage or row-count range implied by the
>    sources,
> 4. identify any mismatch between the query wording and what the
>    sources actually support,
> 5. challenge weak, unstable, or non-authoritative sources,
> 6. decide whether the instance is exhaustive, source-defined, or
>    under-grounded,
> 7. recommend whether the query should be kept, narrowed, rebuilt, or
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
>    temporal_sensitivity, source_count, and source_urls are correct,
> 3. assess whether the current row count / expected cardinality story
>    is documented clearly enough for registration,
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
> scope. Cover all of the following in one response:
>
> 1. row-level correctness and unsupported / missing rows relative to
>    the cited source set,
> 2. duplicates and primary-key problems,
> 3. normalization, table-shape quality, and column-type consistency,
> 4. expected row-count / cardinality estimate, partition sanity, and
>    unique-key sanity,
> 5. source quality, source tier, and source-to-query scope match,
> 6. table completeness for the intended scope,
> 7. cfg metadata correctness,
> 8. LLM-knowable fit for the benchmark,
> 9. whether this instance helps the benchmark's intended multi-source /
>    structured extraction story,
> 10. final Ready / Not ready judgment, clearly separating blocking
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

Then append the full audit transcript to `<id>.comments.json` per
`comments-schema.md`, and return a condensed verdict block to the user
(Ready/Not ready, blocking-issue counts, 3–5 minimal fixes, path written).
