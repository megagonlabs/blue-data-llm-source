# Instance Selection Priorities

**Purpose.** This file is a lightweight planning aid for picking the next
PKDB-Bench instance to label. It is not a registration validator and not a
replacement for `curation_protocol.md`; use it at the moment of choosing an
instance, together with `categories.txt`, `example_questions.txt`, and the
`pkdb-bench-curation` skill.

**Selection happens in two stages:**

**Stage 1 — Pre-gate.** Run the fast viability gate below as a hard pass/fail.
A candidate that fails the gate must be adapted or abandoned before staging,
no matter how attractive it looks on the scored axes.

**Stage 2 — Score the surviving candidates** on three priority axes:

1. **Seed-first** — prefer questions in the human-curated working set
2. **Category target balance** — fill under-target categories before over-target
3. **Domain variety** — avoid concentrating instances in one topical domain

Viability is not a fourth scored axis. A candidate either passes the pre-gate
or it is reshaped/dropped; the rubric only ranks candidates that already pass.

---

## Fast viability gate

Use this as a quick candidate card, not a long proposal. A good candidate should
usually satisfy all of these:

- **Source-defined universe / rule:** the row universe, partition boundary,
  ranking rule, or filter can be grounded in cited sources rather than
  parametric judgment.
- **Enough rows:** expected CSV has at least 10 rows, or the seed can be
  adapted to reach that without changing its spirit.
- **Useful table shape:** at least one key and preferably 2+ non-key
  attributes for categories meant to test calibration across cells.
- **Confidence gradient:** includes a mix of broadly knowable facts and harder
  cells; not all rows should be trivial or all near-impossible.
- **LLM-knowable fit:** a closed-book general-purpose model with a pre-2025
  cutoff should plausibly know a meaningful share of the entities or attributes
  from pretraining alone.
- **Temporal fit:** not entirely post-cutoff/current unless the instance is
  deliberately testing freshness and declares that risk.

Tiny candidate card to record in `curation.json::decision_log`:

| Field | What to write |
| --- | --- |
| Pre-gate verdict | Pass / pass-with-adaptation (note adaptation) / fail (do not stage) |
| Seed/provenance | Verbatim seed, adapted seed, or original |
| Category | Primary category and why the DB analog fits |
| Source plan | Main source(s), plus source roles per `references/artifact-contract.md` |
| Expected shape | Approximate rows × columns and primary key |
| Domain | Domain label and whether it diversifies coverage |
| Risks/adaptation | Row-count, ambiguity, source, temporal, or metric risks |
| Decision | Stage / adapt / abandon |

## Axis 1 — Seed source preference

`example_questions.txt` is the human-curated seed working set; `categories.txt`
defines the structural shape per category. Both are higher-trust than original
questions because they reflect deliberate human design.

Preference order:

1. **Verbatim seed** from `example_questions.txt`
2. **Adapted seed** (close variant of an example_questions.txt entry, with the
   adaptation reason documented in `decision_log`)
3. **Original concept** fitting a `categories.txt` definition. Allowed when no
   seed covers the target category (per `pkdb-bench-curation/references/workflows.md`
   step 2(b)). Document the no-seed-fit reason in `decision_log`.

The seed pool is small (23 questions) and most are still unlabeled — bias
heavily toward labeling untouched seeds before doing originals.

---

## Axis 2 — Category target balance

Per `README.md`, target instance counts per category:

- **8 each** — `set_operation`, `implicit_join`, `multi_attribute_projection`
  (the three primary multi-source categories the benchmark exists to test)
- **5 each** — `set_filter`, `aggregation`, `top_k`, `multi_hop`, `qualitative`,
  `temporal`, `geographic`, `causal`, `comparative`,
  `hierarchical_decomposition`, `scaled_enumeration`
- **3** — `factual_lookup` (baseline)

**Total target: 82 instances.**

When choosing the next instance, prefer categories with the largest absolute
gap (target − current). Within similarly-gapped categories, prefer ones with
the most untouched seeds. Official registration gaps should be computed from
`cfg.json`; this planning file also tracks staged instances so the next curator
does not duplicate in-flight work.

---

## Axis 3 — Domain variety

Each `draft.json` declares a `domain` field (e.g., `Geography/Climate`,
`Culture/Film`, `Politics/Geopolitics`). Aim for variety across the
benchmark. Avoid stacking new instances in already-saturated domains.

Use `domain_taxonomy.md` for the operational domain-family vocabulary. The
field is still free text, but new instances should prefer `Family/Subdomain`
labels such as `Science/Astronomy`, `Culture/Literature`, or
`Business/Companies`.

Source-tier diversity, claim-type diversity, and multi-source/single-source
balance are useful tie-breakers, but not primary axes. They should not override
an otherwise strong seed/category/domain pick unless the candidate fails the
viability gate or would create a visible benchmark skew.

---

## Selection rubric

For each candidate that has already passed the pre-gate, evaluate these three
signals:

| Signal | Bad | Neutral | Good |
| --- | --- | --- | --- |
| **Seed source** | Original (no seed) | Adapted seed | Verbatim seed |
| **Category gap** | Already at/over target | Mid-gap | Largest gap |
| **Domain novelty** | Domain saturated | Domain present but light | Domain unrepresented |

Highest combined score = best next pick. Document the rubric scoring in
`curation.json::decision_log` so the next reviewer can audit the choice.
Record the pre-gate verdict in the candidate card alongside the scored axes;
do not score a candidate that did not pass the pre-gate.

If a category has zero seeds and no untouched candidates (e.g., `comparative`),
either propose an original instance and document the seed-pool gap, or add a
seed to `example_questions.txt` first and label it second.

---

## Current snapshot (2026-04-28)

### Category coverage

This staged+registered view is for planning. The four formerly null-category
staged drafts (`eu_member_states_accession_years`,
`eurozone_adoptions_1999_2023`,
`largest_islands_by_area_top20_britannica`,
`landlocked_africa_neighbor_languages`) now carry their documented
`query_category` in `draft.json` and are reflected directly in the staged
counts below.

| Category | Target | Registered | Staged | Total | Gap |
| --- | --- | ---: | ---: | ---: | ---: |
| `factual_lookup` | 3 | 0 | 1 | 1 | **2** |
| `set_filter` | 5 | 0 | 2 | 2 | **3** |
| `implicit_join` | 8 | 2 | 1 | 3 | **5** |
| `aggregation` | 5 | 1 | 0 | 1 | **4** |
| `top_k` | 5 | 0 | 1 | 1 | **4** |
| `set_operation` | 8 | 6 | 0 | 6 | **2** |
| `multi_hop` | 5 | 0 | 1 | 1 | **4** |
| `qualitative` | 5 | 0 | 1 | 1 | **4** |
| `temporal` | 5 | 0 | 1 | 1 | **4** |
| `geographic` | 5 | 0 | 1 | 1 | **4** |
| `causal` | 5 | 0 | 1 | 1 | **4** |
| `comparative` | 5 | 0 | 0 | 0 | **5** |
| `hierarchical_decomposition` | 5 | 0 | 1 | 1 | **4** |
| `multi_attribute_projection` | 8 | 2 | 1 | 3 | **5** |
| `scaled_enumeration` | 5 | 0 | 2 | 2 | **3** |
| **Total** | **82** | **11** | **14** | **25** | **57** |

Planning coverage, counting registered + staged instances: ~30% of target.

### Seed coverage of `example_questions.txt`

23 seeds total. Clear touches or close adaptations: **7** (#3, #5, #10, #11,
#15, #21, #22). Seed #20 remains untouched; the film-by-decade instance is
structurally similar but is not an adaptation of the inventions seed.

Untouched: **16** (#1, #2, #4, #6, #7, #8, #9, #12, #13, #14, #16, #17,
#18, #19, #20, #23).

### Domain distribution (across all 23 instances)

Domain families below are planning labels. Use `domain_taxonomy.md` when
assigning or repairing `draft.json::domain`.

| Domain family | Instances |
| --- | ---: |
| Geography | 8 |
| Politics | 5 |
| Culture | 5 |
| Science | 2 |
| Business | 1 |
| Health | 1 |
| History | 1 |
| Religion | 1 |
| Sports | 1 |
| **Technology / Food / Law / Transportation / Environment / Education / Society** | **0** |

Geography, Politics, and Culture are currently the largest families.
`Science/Astronomy` was added by the planets-named-moons instance (a
verbatim adaptation of seed #15), bringing the Science family to two.
`Religion/Holy Sites` was added by the world-religions-top5-holy-sites
instance (verbatim seed #21). Technology, Food, Law, Transportation,
Environment, Education, and Society are still unrepresented.

---

## Selection rubric applied to current state

**Top candidates for the next instance** (scored on the 3 axes; #15 and #21 are now staged):

| Seed | Category | Category gap | Likely domain | Domain novelty | Score |
| --- | --- | ---: | --- | --- | --- |
| **#20** Decade × 5 inventions | `scaled_enumeration` | 3 | Technology | new | ★★★★★ |
| **#19** Sci disciplines × top 5 scientists | `scaled_enumeration` | 3 | Science/History of Science | mild overlap | ★★★★ |
| **#23** Nobel categories × laureates | `multi_attribute_projection` | 5 | Science/Academia | mild overlap | ★★★ |
| **#4** Top 3 vitamin C dishes | `top_k` | 4 | Food/Nutrition | new | ★★ |
| **#14** G20 GDP/pop/lang/govt | `multi_attribute_projection` | 5 | Business/Economics | overlap | ★★ |
| **#16** South America countries × attrs | `multi_attribute_projection` | 5 | Geography | overlap | ★ |
| **#17** 50 US states × attrs | `multi_attribute_projection` | 5 | Politics/Government | overlap | ★ |
| **#18** 20th-c US presidents × attrs | `multi_attribute_projection` | 5 | History/Politics | overlap | ★ |

**`comparative` (gap 5, zero seeds)** is a special case — no seed exists to
draw from. Either add a comparative seed to `example_questions.txt` first, or
do an original-only instance and document the seed-pool gap. Lower priority
than the seed-driven candidates above.

---

## Recommended next-instance sequence

Bias toward seed-driven, gap-filling, domain-diversifying picks. A reasonable
ordered sequence given the current snapshot (seed #15 already staged):

1. ~~#15 — Planets × named moons~~ — **STAGED 2026-04-28**
   (multi_attribute_projection + Science/Astronomy, 137 rows × 5 cols, Tier A)
2. ~~#21 — World religions × holy sites~~ — **STAGED 2026-04-28**
   (scaled_enumeration + Religion, 20 rows × 6 cols, Tier B)
3. **#20 — Decade × 5 inventions** (scaled_enumeration + Technology)
4. **#19 — Scientific disciplines × top 5 scientists** (scaled_enumeration + Science)
5. **#23 — Nobel categories × last 5 laureates** (multi_attribute_projection + Science)
6. **#14 — G20 GDP/pop/lang/govt** (multi_attribute_projection)
7. **#4 — Dishes high in vitamin C** (top_k + Food/Nutrition; adapt beyond top 3
   or defer, because a 3-row CSV fails the row-count gate)
8. (eventually) original `comparative` instance OR add a comparative seed first

This sequence can still touch 5 more untouched seeds, fills ~5 category gaps,
and introduces or strengthens 2 more underrepresented domains/subdomains
(Technology, Food/Nutrition).

---

## Maintenance

This file is a snapshot. Refresh it after each active labeling session, or
before using it if several staged/registered artifacts have changed since the
snapshot date.

1. Update the category-coverage table for any new instances.
2. Update the seed-coverage list (which seeds are touched, which remain).
3. Update the domain-frequency table.
4. Re-run the rubric and update the recommended-next-instance sequence.

Keep the snapshot date in this file's "Current snapshot" header. The
one-month rule is only a fallback for quiet periods; during active curation,
refresh per session.
