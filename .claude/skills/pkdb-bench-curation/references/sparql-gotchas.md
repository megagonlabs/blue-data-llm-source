# SPARQL Gotchas

Read this when writing or modifying a `.sparql` query for a Tier A
(Wikidata SPARQL) instance. These are recurring footguns that have
already shipped bugs to staged instances; the preflight in
`workflows.md` will catch most of them, but knowing the cause prevents
the bug at write time.

## 1. `wdt:` (truthy) vs. `p:/ps:` (all ranks) for universe membership

**Symptom:** the universe size is smaller than every authoritative list
says it should be — one or more entities that clearly satisfy the
predicate are silently missing from the result.

**Cause:** Wikidata statements have ranks (preferred / normal / deprecated).
The `wdt:` shortcut path returns only **truthy-rank** statements: the
preferred-rank value if any preferred-rank value exists for that property,
otherwise normal-rank values. If an entity has **multiple statements** for
the same property and one of them is marked preferred, the truthy path
returns only the preferred one — even if the value you wanted is recorded
at normal rank on the same property.

**Concrete case (this benchmark):**
Rwanda (Q1037) has multiple `P31` (instance of) statements: `Q3624078`
(sovereign state), `Q6256` (country), and `Q123480` (landlocked country).
The first two are at preferred rank, so `wdt:P31` returns those — and
silently drops `Q123480`. A query that filters landlocked countries via
`?x wdt:P31 wd:Q123480` therefore omits Rwanda even though Rwanda **is**
typed as landlocked on Wikidata.

**Fix:** for **universe membership** queries — where you are filtering by
property value to find a category of entities — use the all-ranks
property path `p:Pxx/ps:Pxx`. **But** be aware this also includes
deprecated-rank statements; for most universe queries that is harmless,
but if any item in your domain has a `Q123480` claim explicitly marked
deprecated, the all-ranks path will pick it up. Decide which behavior
you want:

```sparql
# WRONG — silently drops normal-rank statements when a preferred-rank
# value exists for the same property on the same entity
?landlocked wdt:P31 wd:Q123480 .

# OPTION A — captures every P31 statement regardless of rank.
# Acceptable when you have checked your domain has no deprecated-rank
# Q123480 claims, or when including deprecated-but-historically-true
# values is intentional (e.g., historical accession lists).
?landlocked p:P31/ps:P31 wd:Q123480 .

# OPTION B — captures preferred + normal rank but excludes deprecated.
# Safer default if you have not audited deprecated-rank coverage.
?landlocked p:P31 ?stmt .
?stmt ps:P31 wd:Q123480 .
FILTER NOT EXISTS { ?stmt wikibase:rank wikibase:DeprecatedRank }
```

If you take Option A, run preflight item #5 (universe-size sanity check)
against an authoritative cited count and document any discrepancy in
`curation.json::decision_log`. If you take Option B, no extra
documentation is needed.

**When to keep `wdt:`:** for **canonical attribute hops** — where you
want "the" current value, not "any" value — `wdt:` is correct because it
intentionally honors rank curation (e.g., a country's current capital
when the previous capital is recorded at deprecated rank). Mixing both
in one query is normal:

```sparql
?country p:P31/ps:P31 wd:Q123480 .   # universe: any P31=landlocked claim
?country wdt:P36 ?capital .           # canonical (current) capital
```

**How to spot the bug at preflight:** compare the universe size against
a Wikipedia-listed count for the category. The Rwanda case was caught
because "16 landlocked African countries" is widely cited in geography
references but the SPARQL returned only 15.

## 2. Selecting only `?xLabel` skips suffix stripping in `sparql_to_csv.py`

**Symptom:** column rename map in `sparql_to_csv.py --rename '{"x":"X"}'`
silently does nothing; the output column is named `xLabel` instead of `X`.

**Cause:** the pipeline's `bindings_to_dataframe` strips the `Label`
suffix only when **both** `?x` and `?xLabel` appear in the SELECT. If you
only select `?xLabel`, the column stays `xLabel` and the rename map
(keyed on `x`) does not match.

**Fix:** key the rename map on the actual SPARQL var name, including
the `Label` suffix:

```bash
# Either select both:
SELECT ?country ?countryLabel ...
--rename '{"country":"Country"}'

# Or rename the Label var directly:
SELECT ?countryLabel ...
--rename '{"countryLabel":"Country"}'
```

## 3. `wikibase:label` SERVICE position

The `SERVICE wikibase:label { bd:serviceParam wikibase:language "en". }`
clause must be inside the `WHERE` block but **after** the variable bindings
that need labeling. Putting it before the patterns that bind your variables
results in "no label" fallbacks (raw QID strings) leaking into the CSV.
The pipeline's `bindings_to_dataframe` warns when it sees QID-shaped
values in a label column — treat that warning as a query bug, not a
data bug.

## 4. `DISTINCT` is per-row, not per-entity

`SELECT DISTINCT ?countryLabel ?neighborLabel ?languageLabel` deduplicates
the **triple**, not any one column. If South Africa has 12 official
languages and Botswana borders South Africa, the result will contain 12
rows for the (Botswana, South Africa) pair — one per language. This is
usually what you want for a multi-hop benchmark; if not, project to
fewer columns or aggregate in post-processing.

## 5. `P47` (shares border with) includes maritime borders

For some pairs (e.g., countries that share only a sea border), `P47`
links them despite no land border. There is no clean Wikidata property
that strictly means "shares a *land* border with," so do not assume one
exists — earlier drafts of this gotcha pointed at `P3179`, which is
actually "territory overlaps" and would exclude the wrong pairs.

If your query intent is "land border," the safe pattern is:

1. Run the query with `wdt:P47` as-is.
2. **Inspect the result for known maritime-only pairs** in your domain
   (e.g., USA–Bahamas, Italy–Greece, Indonesia–Australia). Wikidata
   classifies these inconsistently across countries.
3. Either narrow the query intent in `table_title` to "shares a border"
   (which the protocol allows when documented), or filter the suspect
   pairs out post-query and document each removal in
   `curation.json::decision_log`.

Do not invent a "land border" filter from a property whose semantics
you have not verified — the cost of dropping a real land neighbor is
higher than the cost of leaving in a maritime pair you flagged.

## 6. `P37` may include languages with mixed de jure / de facto status

`P37` (official language) is taken at face value by this benchmark — it
includes statutory and constitutional official languages, but for some
countries it may also include de facto national languages. If the
distinction matters for your instance, document the choice in
`curation.json::decision_log`; do not silently filter to a stricter
subset.

---

## Summary preflight checklist (Tier A only)

Before saving a Tier A CSV to staging, verify:

1. Universe-membership clauses use **§1 Option B** by default
   (preferred + normal rank, excludes deprecated) rather than `wdt:Pxx`.
   Use **§1 Option A** (all ranks, including deprecated) only when you
   have audited deprecated-rank coverage for the universe and recorded
   the choice in `curation.json::decision_log`. `wdt:Pxx` for the
   universe is acceptable only when you have verified there are no
   normal-rank statements that would be silently dropped — this is
   rare in practice and should also be documented.
2. Column rename keys match actual SPARQL var names (including `Label`
   suffix where applicable).
3. No QID-shaped values appear in label columns.
4. Universe size matches an authoritative Wikipedia-listed count, or
   the discrepancy is documented in `curation.json::open_questions`.
5. Composite PK is unique on the columns you declared as the PK.

The full preflight (covering all source tiers) lives in
`workflows.md` under the create-instance mode.
