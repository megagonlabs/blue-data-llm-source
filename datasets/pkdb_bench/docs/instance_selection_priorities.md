# Instance Selection Priorities

**Purpose.** This file is a lightweight planning aid for picking the next
PKDB-Bench instance to label. It is not a registration validator and not a
replacement for `curation_protocol.md`; use it at the moment of choosing an
instance, together with `categories.txt`, `example_questions.txt`, and the
`pkdb-bench-curation` skill.

**Selection happens in two stages, preceded by an inventory check.**

**Stage 0 — Inventory check (mandatory; cheap to skip-and-regret).**
Before proposing or staging anything, list what already exists. Multiple
agents curate this benchmark in parallel, so duplicates are easy to ship
if you skip this step. Run the four-line check in the next subsection
and confirm the candidate ID is not already in `staging/`, `tables/`, or
`cfg.json`.

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

## Stage 0 — Inventory check before proposing

Run this check at the moment of choosing the next candidate. Skipping it
has shipped duplicate proposals more than once: an agent re-suggested
"countries that drive on the left" (already staged) and "G20 country
attributes" (already staged) in the same session.

```bash
ls datasets/pkdb_bench/staging/*.draft.json | xargs -n1 basename
ls datasets/pkdb_bench/tables/*.csv | xargs -n1 basename
python3 -c "import json; print('\n'.join(json.load(open('datasets/pkdb_bench/cfg.json')).keys()))"
```

If you can't run the commands, scan the "Current snapshot" section of
this file (kept updated per session). Any candidate whose ID, seed
number, or table title already appears in those listings is a
duplicate — adapt it materially or pick a different seed.

**Entity-class duplicate check.** Beyond ID-level duplicate
detection, scan the **Entity-class inventory** subsection (canonical
per-instance table). A proposal whose primary entity class is
already represented at 3+ instances should be flagged in
`decision_log` and either justified (category-gap-fills-it) or
swapped for an under-represented entity class. The mountain-ranges
near-miss on 2026-05-14 (proposed Option A entity class
`physical-features/mountains` when `top_5_highest_mountains_per_continent`
already covered that fine-class) is the canonical motivating
incident for this check.

The "Recommended next-instance sequence" at the bottom of this file
explicitly marks staged seeds with `~~strikethrough~~` and **STAGED**;
a strikethrough seed is off the table.

---

## Fast viability gate

Use this as a quick candidate card, not a long proposal. A good candidate should
usually satisfy all of these:

- **Source-defined universe / rule:** the row universe, partition boundary,
  ranking rule, or filter can be grounded in cited sources rather than
  parametric judgment.
- **Deterministic universe rule (preference, not gate):** prefer row-universe
  rules expressible as a SQL `WHERE` clause using only (a) numeric thresholds
  (≥ N, top-K, > X units), (b) canonical-list membership (UN members, IUPAC
  groups, Wikipedia 'List of X'), or (c) deterministic ranking attributes
  (atomic number, apparent magnitude, GDP). When the natural phrasing uses
  curator-judgment words like "major" / "notable" / "representative" /
  "important" / "famous" / "prominent", the universe is curator-bound —
  `claim_type = source_defined` becomes harder to defend, and the audit arc
  tends to push for either a rename or a non-`source_defined` claim_type.
  Both are valid:
  *(i)* renaming to the deterministic equivalent at Stage-0 is cheap and
  typically saves 1-2 audit cycles after staging (e.g., `representative_elements`
  → `first_three_elements_by_atomic_number`).
  *(ii)* accepting `partially_supplemented` or `candidate_rebuild` is fine
  when the curator-bound universe is the load-bearing point of the instance
  (e.g., `major_philosophical_schools_founders_origins` → `candidate_rebuild`,
  chosen at design review; `decisive_battles_of_world_war_ii` →
  `partially_supplemented`, after audit-cycle-1 found `source_defined`
  overstated the curator-selected universe). The preference is to think about
  this trade-off explicitly at Stage-0, not to forbid curator-bound framings.
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

Per `README.md`, target instance counts per category (2-tier structure
revised 2026-05-12 — `factual_lookup` raised from 3 to 5 for tier
simplification and per-category statistical comparability):

- **8 each** — `set_operation`, `implicit_join`, `multi_attribute_projection`
  (the three primary multi-source categories the benchmark exists to test
  — these warrant higher N because they exercise the benchmark's
  distinctive contribution)
- **5 each** — `set_filter`, `aggregation`, `top_k`, `multi_hop`,
  `qualitative`, `temporal`, `geographic`, `causal`, `comparative`,
  `hierarchical_decomposition`, `scaled_enumeration`, `factual_lookup`
  (12 categories; 5 is the minimum N for meaningful per-category
  mean ± stderr in paper-submission reporting)

**Total target: 84 instances.**

The prior tier structure had factual_lookup at 3 as a "baseline" tier;
the revised 2-tier structure simplifies the methodology-section
justification (multi-source-emphasis-vs-everything-else) and avoids
the anomaly of having one category with too few instances for
meaningful per-category statistics. The cost is +2 instances of
factual_lookup curation work.

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

**Entity-class diversity (now a scored axis as of 2026-05-14 — see
the 4-axis Selection rubric below).** When two candidates are
otherwise comparable on the other three axes, prefer the one with a
less-represented entity class. Entity class is tracked as a
`bucket/fine-class` pair (controlled vocabulary
documented in the **Entity-class inventory** subsection of Current
snapshot below). The per-instance entity-class column in that table
is the canonical reference — check it at Stage-0 inventory time
before scoring a candidate. As of 2026-05-15 the most-represented
buckets are `countries` (14 instances) and `people/*` (12
instances); the most under-represented are
`cities-and-capitals` (1), `time-periods/*` (1),
`abstract-categories/*` (4), `taxonomic-entities/*` (3),
`engineered-structures/*` (4), `organizations/*` (4),
`physical-features/*` (7), and `events/*` (10 — extended this
snapshot with NEW `empires` fine-class from
`major_historical_empires_founder_capital_century`). Treat this
as a soft preference — it
should not block a strong country-keyed pick that fills a category
gap, but it should break a tie in favor of entity novelty, and a
proposed candidate whose entity class is already at 10+ instances
should carry a documented entity-overlap-acceptance note in
`decision_log`.

**NEW-entity-class proposals can surface curator-bound framings during
ideation.** When a candidate's entity bucket or fine-class is novel,
the natural phrasing can drift toward "major X" / "notable X" —
canonical/closed lists for novel entity classes can be sparser, so
curators may reach for curator-judgment framings as the only obvious
angle. (Counter-examples exist: bridges, dams, ports, airports, and
periodic-table groups all reached `source_defined` cleanly once the
ranking or partition rule was made explicit.) This isn't a problem in
itself, but it's worth flagging at the same Stage-0 step as the
entity-novelty check: if the only natural framing is curator-bound,
expect a longer audit arc and an honest `partially_supplemented` or
`candidate_rebuild` claim_type. If a deterministic framing exists
(numeric threshold, canonical list, ranking attribute — see the Fast
viability gate's deterministic-universe-rule preference), that's the
cheaper path; but both routes can reach Ready+Clean. The preference is
to think about the trade-off explicitly when proposing a NEW-entity-
class candidate, not to forbid curator-bound novel-entity proposals.

---

## Selection rubric

For each candidate that has already passed the pre-gate, evaluate these four
signals:

| Signal | Bad | Neutral | Good |
| --- | --- | --- | --- |
| **Seed source** | Original (no seed) | Adapted seed | Verbatim seed |
| **Category gap** | Already at/over target | Mid-gap | Largest gap |
| **Domain novelty** | Domain saturated | Domain present but light | Domain unrepresented |
| **Entity-class novelty** | Bucket at 10+ instances or fine-class at 3+ | Bucket present but light (3–9) | Bucket at 0–2 instances OR fine-class is NEW within an existing bucket |

Highest combined score = best next pick. Document the rubric scoring in
`curation.json::decision_log` so the next reviewer can audit the choice.
Record the pre-gate verdict in the candidate card alongside the scored axes;
do not score a candidate that did not pass the pre-gate. **Entity-class
novelty** uses the bucket + fine-class counts from the **Entity-class
inventory** subsection of Current snapshot; check both the bucket-level
saturation (e.g., `countries` at 14) and the fine-class level (e.g.,
`physical-features/mountains` at 1 — already covered, so a second
mountain-keyed candidate scores Bad on this axis even though
`physical-features` bucket is mid-light).

If a category has zero seeds and no untouched candidates (e.g., `comparative`),
either propose an original instance and document the seed-pool gap, or add a
seed to `example_questions.txt` first and label it second.

---

## Current snapshot (2026-05-21, post-CorpusWideQualityReview (4-parallel-agent audit pass) — **🎯🎯 RAISED TARGET 102 HIT (post-deprecation bench at 103); 15/15 ABSOLUTE-SATURATION HELD.** Quality review identified 4 critical fixes applied: (1) `largest_sauropods_by_estimated_mass` re-categorized comparative→top_k (db_analog is ORDER BY rank LIMIT 10 with no threshold); (2) `countries_with_more_than_5_fields_medalists` re-categorized comparative→aggregation (db_analog is COUNT GROUP BY HAVING≥5); (3) `top_10_most_populous_cities_per_continent` re-categorized top_k→hierarchical_decomposition (6 continents × 10 cities partitioned top-K with composite PK); (4) `eurozone_adoptions_1999_2023` categorized NULL→temporal (year-window scope is load-bearing). **1 deprecation**: `transboundary_rivers_by_continent_over_100_miles` soft-duplicate moved to `datasets/pkdb_bench/deprecated/` (canonical kept = `over_1000_km` version with richer source plan + boundary-aware Continent(s) schema). Net category-table effect: aggregation 5→6, hierarchical 5→6, temporal 6→7, comparative 8→6, set_filter 6→5, top_k 7→7 (sauropods in + cities out, net 0), uncategorized 1→0, total bench 104→103 (1 deprecated). Useful reference for future corpus-quality-review passes: (a) `db_analog` is the most reliable category-fit signal (comparative requires threshold predicate; top_k requires LIMIT-on-ranking; aggregation requires GROUP BY); (b) per-instance categorization should run a final db_analog-vs-category-contract check before staging; (c) soft duplicates with parallel-structure across awards/wars/physical-features are USUALLY intentional probes (Booker/Pulitzer, WWI/WWII, Islands/Lakes, Airports/Container-ports all retained) — only deprecate when source plan is strictly weaker AND universe scope is strictly subsumed (rivers_100_miles was both). Prior snapshot: post-FilmsByDecade-Ready+Clean (4-cycle post-staging arc terminating at cycle-4 0/0/0 Ready+Clean — cycle-1 Ready+2-HYGIENE [scope: source-freeze ambiguous / factual: 1995+2005 multi-row years + 2010s gap-check wrong #4 (was HP DH2 → corrected to The Avengers 2012 $1.52B) + non-year-leader challengers Jurassic World 2015 $1.67B + Lion King 2019 $1.66B + Zootopia 2 2025 $1.87B flagged]; cycle-2 curator 3 prose fixes [Source-access freeze policy + Year-leader data-shape normalization_decisions + corrected gap-check]; cycle-2 re-audit Ready+2-HYGIENE prose-remnants [table_title not 'predominantly' + post-2025 still 'unknown']; cycle-3 curator 2 prose fixes [table_title + freeze framing]; cycle-3 re-audit Ready+2-HYGIENE prose-remnants [excluded_from_universe.post_2025 still 'unknown' + open_questions[3] stale 2010s claim]; cycle-4 curator 2 final prose fixes [post_2025 + open_questions[3] both reworded]; cycle-4 re-audit Ready+Clean LOCKED). hierarchical_decomposition chips 5/5 → 6/5 (7th of 13 floor-lift instances toward 102-target after epidemics-1M lifting comparative + WWI battles lifting multi_hop + industrial-disasters lifting causal + world-expositions lifting temporal + antarctic-stations lifting set_filter + F1-champions lifting aggregation). NON-CONTINENT scaffold (Decade) per user-preference avoiding by-continent bias in prior hierarchical instances. Universe = 15 films = top 3 highest-grossing year-leader films per decade × 5 decades (1980s-2020s) per Wikipedia 'List of highest-grossing films' master's 'High-grossing films by year of release' table. claim_type=source_defined; operation_type=projection; secondary_category=top_k; gt_construction=manual_wikipedia; source_count=1. Schema = 5 cols composite-PK (Decade + Rank Within Decade + Film + Release Year + Worldwide Gross USD). Sort: Decade ASC + Rank Within Decade ASC; within-decade gross DESC verified. **Useful reference for future hierarchical_decomposition / prose-heavy instances**: (a) **Source-shape limitation framing must propagate to ALL prose fields** consistently — 'top 3 films released per decade' vs 'top 3 year-leader films per decade' are different operations; (b) **Range-value normalization choice (lower vs upper bound)** can change membership at decade boundaries (1989 Indiana Jones LC $474-494M range; lower bound used); (c) **Non-year-leader challengers from master's all-time top chart should be cross-checked** against staged top-3-from-year-leader-data — Jurassic World 2015 + Lion King 2019 + Zootopia 2 2025 all exceed #4 year-leader but not #3 staged threshold; (d) **CRITICAL prose-fix lesson**: when applying audit-recommended phrasing changes, search-and-replace the stale phrase across ALL fields (claim_type_rationale + collection_summary + decision_log + open_questions + excluded_from_universe + draft.json temporal_note + draft.json table_title + draft.json _extraction_notes) in a SINGLE repair pass — this instance took 4 audit cycles + 3 repair cycles because cycle-2/cycle-3 prose fixes missed propagating to all 7+ fields where the stale phrasing lived; (e) **Source-access freeze policy** (e.g., 'frozen at 2026-05-21 access time, scoped to release years 1980-2025') is a useful normalization_decision pattern for live-data sources to disambiguate curation-vs-audit-time scope. Prior snapshot: post-PresidentsVPs-Ready+Clean (3-cycle post-staging arc terminating at cycle-3 0/0/0 — cycle-1 0/2/0 same-year/succession overclaim + Biden gap mixed-metric; cycle-2 0/2/0 missed open_questions same-year answer + Vance VP number 49→50 live-source correction; cycle-3 clean after open_questions rewrite + 4-spot Vance correction) + post-F1Champions-Ready+Clean (2-cycle post-staging arc terminating at cycle-2 0/0/0 Ready+Clean — cycle-1 returned Ready with 2 HYGIENE [factual: single-time-champion prose list missing Nigel Mansell 1992 + Damon Hill 1996 + stale '35-13=22' arithmetic / structural: open_questions[0]+[1] still 'open-audit-verification' placeholders despite cycle-1 audit having resolved them], 0 BLOCKING; cycle-2 curator repair applied both [completed 18-name list with chronological insertion of Mansell + Damon Hill / closed both open_questions to 'resolved-cycle-1-audit' with audit conclusions / refreshed arithmetic to '35 − 17 = 18']; cycle-2 re-audit Ready+Clean LOCKED. aggregation chips 5/5 → 6/5 (6th of 13 floor-lift instances toward 102-target after epidemics-1M lifting comparative + WWI battles lifting multi_hop + industrial-disasters lifting causal + world-expositions lifting temporal + antarctic-stations lifting set_filter). NEW Sports/Motorsport subdomain (Sports family extends +1 — Motorsport genuinely novel joining Football/Olympics/Records). NEW `people/race-car-drivers` fine-class within people bucket. Universe = 17 F1 drivers with ≥2 World Drivers' Championships through 2025 season (76 total seasons 1950-2025 → 35 unique champions = 17 multi-champion in scope + 18 single-time out of scope including Mansell 1992 + Damon Hill 1996 + Norris 2025 as most recent first-time champion). master = Wikipedia 'List of Formula One World Drivers' Champions'. claim_type=source_defined; operation_type=aggregation; gt_construction=manual_wikipedia; source_count=1. Schema = 4 cols aggregation-pure (Driver PK + Championship Count COUNT + First Title Year MIN + Last Title Year MAX). Sort: Count DESC primary + First Title Year ASC secondary tiebreak (chronological-first within count tier — Schumacher 1994 → Hamilton 2008 for 7-tier; Prost 1985 → Vettel 2010 → Verstappen 2021 for 4-tier; Brabham 1959 → Stewart 1969 → Lauda 1975 → Piquet 1981 → Senna 1988 for 3-tier; Ascari 1952 → G.Hill 1962 → Clark 1963 → Fittipaldi 1972 → Häkkinen 1998 → Alonso 2005 for 2-tier). Diacritics preserved (Häkkinen with umlaut). normalization_decisions + open_questions **seeded at cycle-0** per cycle-2 lesson from antarctic-stations (built-in upfront, NOT added in cycle-2 repair). **Useful reference for future aggregation instances**: (a) **Multi-fetch reconciliation** — 3 sequential WebFetch passes were needed to lock the universe against Wikipedia's 'By driver' summary table (pass 1 missed Lauda+Brabham 3rd titles + Clark's 2 titles; pass 2 missed Ascari + Graham Hill entirely; pass 3 reconciled all 17); (b) **Out-of-scope enumeration also worth audit-side verification** — cycle-0 curator surfacing of the 18 single-time champions missed Mansell 1992 + Damon Hill 1996 even after the 3-pass in-scope reconciliation worked correctly; (c) **MIN/MAX year aggregates over master-supplied year-list columns** preserve source_defined cleanly without separate source for First/Last Title Year. Prior snapshot: post-AntarcticStations-Ready+Clean (parallel-agent; 2-cycle post-staging arc terminating at cycle-2 0/0/0 Ready+Clean — cycle-1 returned Not ready with 2 BLOCKING [scope predicate falsely 'continental Antarctica only' / curation.json missing normalization_decisions + open_questions per agent_handoff_bundle.md contract] + 2 HYGIENE [Concordia 'Italy/France' non-verbatim non-standard / partition counts stale '~57'/'~50+' vs live 45/69]; cycle-2 curator repair applied all 4 [scope predicate → `source_partition='Permanent active stations table'` / added 5-entry normalization_decisions + 6-entry open_questions / Concordia → 'Italy; France' semicolon-delimited / counts → 9/45/69]; cycle-2 re-audit Ready+Clean LOCKED) + post-Automobiles-Ready+Clean (terminal cycle-3 0/0/0 — 3-cycle post-staging arc: cycle-1 0/2/0 tiebreak-prose + Manufacturer-source-attribution; cycle-2 0/1/0 missed table_title spot; cycle-3 clean after single rewrite) — **🎯🎯 RAISED TARGET 102 HIT AND EXCEEDED 🎯🎯 — bench at 104 unique instances on disk (2 over target with F1Champions LOCKED); 15/15 ABSOLUTE-SATURATION HELD; implicit_join chips 9→10 (2 over original 8 saturation) via automobiles; NEW Transportation/Automotive subdomain; NEW `vehicles/automobiles` fine-class extending vehicles bucket 1→2 instances (matures youngest pkdb_bench entity bucket); F2 EV pivot ABANDONED per audit BLOCKING — F2a all-time best-selling automobiles pivot used real Wikipedia master + 10-row gate + stable lifetime-cumulative figures + 4-unique-manufacturer-dedupe yielding source_count=5. **MILESTONE: bench reaches 105 unique instances on disk (AntarcticStations + Automobiles + F1Champions + PresidentsVPs all LOCKED Ready+Clean); 3 over RAISED TARGET 102. PresidentsVPs chips set_operation 10→11 (3 over original 8 saturation).** AntarcticStations details: set_filter chips 5→6 LOCKED (1 over original 5 saturation; 5th of 13 floor-lift instances toward 102-target after epidemics-1M lifting comparative + WWI battles lifting multi_hop + industrial-disasters lifting causal + world-expositions lifting temporal); NEW Science/Polar Research subdomain (Science family extends from 15 to 16 subdomains — Polar Research genuinely novel joining Astronomy/Chemistry/Mathematics/Biology/Space Exploration/Volcanology/Geology/Mineralogy/Academia/etc.); NEW `engineered-structures/antarctic-research-stations` fine-class extending engineered-structures bucket; set_filter source-friction-pattern this session — 6th set_filter candidate after 5 prior friction points (canals duplicate; lighthouses fragmented; IGOs fragmented; UNESCO Danger List 403; glaciers fragmented; volcanic-eruptions VEI≥5 set-filter-vs-comparative ambiguity). Antarctic research stations has cleanest set_filter shape — Wikipedia's master article literally provides 4 separate partition tables (Permanent active stations table + Subantarctic stations subsection + Summer-only active + Inactive), so the boolean conjunction filter (active=true AND year-round=true AND source_partition='Permanent active stations table') is source-visible. Universe = 43 permanent-active Antarctic research stations (master's 'Permanent active stations' table — includes BOTH island stations like King George Island ×9 + Laurie Island + Anvers Island + Adelaide Island + Ross Island ×2 + Marambio Island AND continental-interior stations like Geographical South Pole + Coats Land + Vincennes Bay; the master-defined boundary is Antarctic-vs-Subantarctic NOT continental-vs-island, per cycle-2 audit BLOCKING scope-predicate fix). claim_type=source_defined; operation_type=filter; gt_construction=manual_wikipedia; source_count=1. Schema = 4 cols (Station Name PK + Country [Concordia 'Italy; France' is the only multi-value cell, project's standard semicolon-delimiter] + Location + Year Established); year-range 1903 (Orcadas) to 2024 (Qinling). **Useful reference for future set_filter / scope-predicate instances**: (a) **Scope predicate must align with the source's own partition structure**, not curator-invented geographic dimensions — Wikipedia's `Research stations in Antarctica` master uses a 4-partition data shape (Permanent active stations table / Subantarctic stations subsection / Summer-only active / Inactive); the cycle-2-corrected predicate `source_partition='Permanent active stations table'` directly maps to this source-visible boundary, whereas cycle-0's `location_scope='continental_antarctica'` was a curator overlay false for >50% of staged rows; (b) **Multi-value joint-operator cells normalize to project's semicolon delimiter** (precedent: crewed_spaceflight_fatal_incidents Cause cells + PulitzerTonyPlays co-author cells), NOT slash form or master's line-break wikitext rendering; (c) **curation.json must include normalization_decisions + open_questions** per `agent_handoff_bundle.md:444-451` contract — both fields are BLOCKING when absent, even if claim_type + collection_summary + decision_log + source_coverage_notes are complete; (d) **Wikipedia partition counts can drift between fetches** — re-verify out-of-scope partition row counts in the audit-cycle window since they shift as the article is edited (e.g., Summer-only ~57→45 + Inactive ~50+→69 between cycle-0 and cycle-2). Prior snapshot: post-PulitzerTonyPlays-Ready+Clean — set_operation chips 9→10 (2 over original 8 saturation; 2 instances now drive set_operation past raised-target ceiling); NEW Culture/Theater subdomain (Culture family extends 10→11 — Theater genuinely novel); NEW `artifacts/plays` fine-class extending artifacts bucket 4→5 fine-classes; total bench 100→101 = 1 short of target 102. 2-cycle post-staging arc terminating at cycle-2 0/0/0 Ready+Clean (cycle-1 HYGIENE db_analog-raw-title→normalized-identity-join; cycle-2 clean). Prior snapshot: post-UNESCOMixedSites-Ready+Clean — **🎯 15/15 ABSOLUTE-SATURATION HELD; set_operation chips 8→9 toward raised-target-102 ceiling (1 over original saturation, gap-1 under raised target); NEW `heritage-properties/*` entity bucket added (pkdb_bench grows 14→15 entity buckets) with NEW `heritage-properties/world-heritage-sites` fine-class; NEW Culture/Heritage subdomain; **3rd pkdb_bench instance using `gt_construction = web_search_*` pattern** (joining FAOSTAT crops + WHO causes-of-death) — UNESCO DataHub whc001 web_search_unesco. 3-cycle post-staging arc terminating at cycle-3 0/0/0 Ready+Clean (cycle-1 BLOCKING Chiribiquete-quote-style + Uluṟu-rendering-normalization-policy + HYGIENE db_analog-Tongariro-edge; cycle-2 prose-hygiene; cycle-3 clean). Prior snapshot: post-WideBodyAircraft-Ready — NEW `vehicles/*` entity bucket added (pkdb_bench grew 13→14 entity buckets) with NEW `vehicles/aircraft` fine-class. FIFTEEN categories FULLY CLEARED: factual_lookup 6/5 + top_k 6/5 + multi_hop 5/5 + qualitative 6/5 + multi_attribute_projection 8/8 + set_operation 8/8 + set_filter 5/5 + causal 5/5 + implicit_join 9/8 + scaled_enumeration 6/5 + hierarchical_decomposition 5/5 + comparative 5/5 + temporal 5/5 + geographic 5/5 + aggregation 5/5 (saturated this snapshot by `top_15_most_produced_crops_by_global_production` + parallel-agent `scotus_appointment_rows_by_president_mckinley_through_biden` — the 14th and 15th fully-cleared categories)**; total target 84; **0 gap-4 / gap-3 / gap-2 / gap-1 categories**; all 16 domain families represented; **Food family extends from 1 to 2 instances** with `top_15_most_produced_crops_by_global_production` (Food/Agriculture NEW subdomain — crisis-light family chip 1→2; only Health remains at 1 instance as crisis-light); **agricultural-commodities/* NEW entity bucket added** (pkdb_bench grows from 12 to 13 entity buckets) with NEW `crops` fine-class; prior snapshot extensions: Law/Judiciary 1→2 (ChiefJustices — people/judges NEW); Environment 1→2 (Global200TMBF — Environment/Conservation NEW + physical-features/ecoregions NEW); UIAA 8000ers saturating comparative + VideoGames saturating hierarchical_decomposition; Business 2→3 (NBERRecessions — events/recessions NEW); Environment 1→2 (Global200TMBF — NEW Environment/Conservation subdomain + physical-features/ecoregions NEW); UIAA 8000ers saturating comparative + VideoGames saturating hierarchical_decomposition; Business 2→3 (NBERRecessions — events/recessions NEW); Culture/Gaming subdomain 1→2 (VideoGames — saturating hierarchical_decomposition as 11th fully-cleared); Business 2→3 (NBERRecessions — events/recessions NEW); Sports 3→4 (UCL clubs — Sports/Football NEW + organizations/sports-clubs NEW); Science 14→15 (PhanerozoicPeriods — NEW Geology subdomain + time-periods/geological-periods NEW); Geography 13→14 (DecadeVolcanoes — NEW Volcanology + physical-features/volcanoes NEW); Politics/IR 1→2 (NATO SG); cities-and-capitals NEW national-capitals; abstract-categories NEW chemical-groups; people NEW diplomats; taxonomic-entities NEW stars/asteroids; Culture/Film 1→2 (FilmsWinning8Oscars))

### Category coverage

This staged+registered view is for planning. As of this snapshot,
`eurozone_adoptions_1999_2023` is the only staged draft still carrying a
`null` `query_category` and is therefore not counted in any per-category
row below; it is held out as a separate "uncategorized" entry in the
total. Refresh notes since the prior snapshot:
- `us_presidents_who_also_served_as_vice_president` (set_operation, **History/Politics existing subdomain — 4th US-government instance** joining twentieth-century-presidents + presidents-count-by-birth-state + scotus-appointments; 15 rows × 5 cols; single PK = Person; gt_construction=manual_wikipedia; source_count=2: Wikipedia 'List of presidents of the United States' + 'List of vice presidents of the United States' masters, both carrying roles=[universe, value] for their respective role-roster per cycle-1 audit's source plan) **STAGED 2026-05-21 → Ready+Clean 2026-05-21 after 1 round pre-staging design review (cycle-1 'Approve With Required Edits + Row Dump Provided' — 4 required edits: keep 5-col schema [no Succession Path 6th column — would introduce curator-classification surface] + Person names from VP list joined via normalize_person_identity() + VP Number footnote markers stripped [Ford = 40 plain numeric, stripped from footnoted '40†' reflecting 25th-Amendment appointment] + scope documented as current of source snapshot 2026-05-21 [current VP JD Vance + current P Donald Trump 47 do NOT affect intersection unless future VP becomes P]) + 3-cycle post-staging arc terminating at cycle-3 0/0/0 Ready+Clean (cycle-1 'Ready with residual hygiene' 0/2/0 — same-year/succession explanation overclaim conflating 5 same-calendar-year cases with all 9 succession cases + Biden gap prose mixed VP-start-to-P-start [12yr] vs VP-end-to-P-start [4yr] metrics; cycle-2 'Ready with residual hygiene' 0/2/0 — missed open_questions same-year answer still used cycle-1 narrow framing + Vance VP number wrong [49→50 per live source: Pence=48 / Harris=49 / Vance=50]; cycle-3 0/0/0 Ready+Clean after open_questions rewrite + 4-spot Vance correction)**. people/politicians existing fine-class extension. Chips `set_operation` from 10 staged (2 over original 8 saturation post-PulitzerTonyPlays) → 11 staged (3 over original; toward raised-target ceiling). Schema: Person PK + VP Number + VP Term Start Year + President Number + President Term Start Year. Universe rule: 15 people who held BOTH US VP AND US P offices per the two Wikipedia masters, intersected on person-identity. claim_type=source_defined; operation_type=intersect; secondary_category=set_filter. Notable: (a) **Schema kept at 5 columns per cycle-1 audit required edit #1** — Succession Path 6th column would introduce curator-classification (Elected vs Succession-of-death vs Succession-of-resignation) beyond pure source-extraction; calibration features it would surface (5 same-year-VP-and-P transitions = all succession-of-deceased-predecessor cases) are inferable from year-cell patterns without requiring a separate column; (b) **Person names from VP list per cycle-1 audit required edit #2** — VP list as displayed-key authority; joined to P list via normalize_person_identity() intermediate per cycle-2 PulitzerTonyPlays + automobiles precedent; (c) **VP Number footnote-stripped per cycle-1 audit required edit #3** — Ford = 40 plain numeric (master shows footnote reflecting Ford's 25th-Amendment appointment after Agnew resignation 1973); other 14 VPs have plain numeric values; (d) **Scope locked to source snapshot 2026-05-21 per cycle-1 audit required edit #4** — current VP JD Vance (49th VP under Trump 47) NOT in intersection because hasn't held P office; current P Donald Trump (45+47) NOT in intersection because hasn't held VP office; latest in-scope row remains Joe Biden (47VP/46P); future VPs who become P would extend universe; (e) **Notable calibration features**: 5 same-year VP-and-P transitions (Tyler 1841 / Andrew Johnson 1865 / Arthur 1881 / TR 1901 / Truman 1945 — all succession-of-deceased-predecessor cases where VP and P inauguration year are identical because predecessor died early in VP's tenure); 5 numerical inversions where President Number < VP Number (Truman 34VP/33P / LBJ 37VP/36P / Ford 40VP/38P / GHW Bush 43VP/41P / Biden 47VP/46P — reflects independent VP and P numbering sequences across same chronological timeline); (f) **Name disambiguation load-bearing** — Andrew Johnson (16VP/17P succession-after-Lincoln) distinct from Lyndon B. Johnson (37VP/36P succession-after-JFK); both are in 15-row universe; sons of universe members (John Quincy Adams was 6P but never VP; George W. Bush was 43P but never VP) are NOT in universe; (g) Cycle-1 audit's master-derived 15-row dump used verbatim at cycle-0 staging — all 75 cell values committed Wikipedia-master-verbatim with NO best-guess cells. **MILESTONE: brings bench from 103 → 104 unique instances on disk (with F1Champions LOCKED by parallel agent = total may be higher); 2+ over raised target 102.** Useful reference for future set_operation INTERSECT-on-people instances using two role-rosters: (1) **Role-roster intersection on person-identity** with normalize_person_identity() join intermediate is the cycle-2 PulitzerTonyPlays + automobiles label-form-tolerance precedent — defensive against cross-master name-form variants; (2) **Footnote-marker stripping** for numeric cells is a HYGIENE pattern when source rendering carries annotations (Ford's 25th-Amendment-appointment footnote belongs in source-citation prose not the staged numeric cell); (3) **Scope-snapshot-current documentation** when role-rosters are still active (US VP + P offices still being filled) — explicitly state that current officeholders are tracked in the source masters but do not affect the intersection unless they later hold both offices; (4) **Avoid Succession Path-style curator-classification columns** when calibration features are inferable from raw source cells (year-cell patterns reveal succession-type without requiring explicit classification). 4-cycle audit history total: 1 pre-staging design review + 3 post-staging cycles (cycle-1 0/2/0 + cycle-2 0/2/0 + cycle-3 0/0/0). Useful Cycle-2→Cycle-3 reference: even after cycle-2 corrected the main prose with extensive sweep, two distinct hygiene errors survived — (a) one open_questions answer was missed by the cycle-2 sweep targeting only the primary prose spots (need to grep ALL prose spots including open_questions answers when applying multi-spot prose hygiene); (b) factual error in current-state metadata about live source state (Vance VP number) only surfaces when audit cross-checks against the live source — pre-cycle-0 fact-checking against the live source for current-state assertions (current VP, current P, current numbering) would have prevented the cycle-2/cycle-3 round-trip. Pattern reference for future role-roster instances: when staging documents current-state assertions about live officeholder numbers, verify against the live source explicitly + flag in collection_summary.
- `top_10_best_selling_automobiles_by_cumulative_units_sold` (implicit_join, **Transportation/Automotive NEW subdomain** — Transportation family extends from 4 to 5 subdomains joining Road Traffic/Maritime/Aviation/Logistics; Automotive genuinely novel; 10 rows × 5 cols; single PK = Model; gt_construction=manual_wikipedia; source_count=5: Wikipedia 'List of best-selling automobiles' master + 4 unique manufacturer Wikipedia pages [Toyota / Ford Motor Company / Volkswagen / Honda] — deduped per cycle-1 audit HYGIENE since 10 models span only 4 unique manufacturers Toyota×3 + Volkswagen×3 + Ford×2 + Honda×2) **STAGED 2026-05-20 → Ready+Clean 2026-05-20 after 2 rounds pre-staging design review (F2 cycle-1 BLOCKED — proposed 'List of best-selling plug-in electric cars' Wikipedia source does not exist + 5-row<10-row gate + Dec-2023 cutoff + source_count not deduped; PIVOTED F2→F2a all-time best-selling automobiles; F2a cycle-1 'Approve With Required Edits + Row Dump Provided' — 5 required edits: units_produced→units_sold rename + verbatim 10-row dump replacing wrong parametric estimate at 5 ranks + source_count=5 dedupe + numeric millions stripping + Ford HQ US→United States normalization) + 3-cycle post-staging arc terminating at cycle-3 0/0/0 Ready+Clean (cycle-1 'Ready with residual hygiene' 0/2/0 — Model-ASC-tiebreak-prose vs CSV's master-row-order + Manufacturer-prose-claimed-separate-master-column-but-master-has-4-cols-only [Nameplate/Production/Units sold/Notes]; cycle-2 'Ready with residual hygiene' 0/1/0 — missed table_title spot still had legacy Manufacturer-as-master-verbatim phrasing despite 5+ other prose spots correctly updated in cycle-2 sweep; cycle-3 0/0/0 Ready+Clean after single table_title rewrite)**. **NEW `vehicles/automobiles` fine-class** extending vehicles bucket from 1 (aircraft) to 2 instances — matures the youngest pkdb_bench entity bucket (vehicles was added 2 cycles ago via wide_body_aircraft). Chips `implicit_join` from 9 (1 over original 8 saturation) to 10 (2 over original; toward raised-target ceiling). Schema: Model PK + Manufacturer (Wikipedia-master short forms 'Toyota'/'Ford'/'Volkswagen'/'Honda' — corporate-name expansion via normalize_manufacturer_identity() at db_analog level per cycle-2 PulitzerTonyPlays precedent) + Cumulative Units Sold (millions) [numeric stripped of master's 'million' text] + Production Years [text-range verbatim per master with 'present' active-marker for 8 of 10 rows + end-year for 2 discontinued: Beetle 1938-2003 + Ford Fiesta 1976-2023] + HQ Country [Japan ×5 + United States ×2 + Germany ×3]. Universe rule: top 10 best-selling automobiles by cumulative units sold per Wikipedia 'List of best-selling automobiles' master's 'Best-selling car nameplates of all time' table — closed canonical 10-row top-10 ranking. claim_type=source_defined; operation_type=join; secondary_category=top_k. Notable: (a) **F2→F2a pivot per cycle-1 BLOCKING** — initial F2 design (BEV cumulative-through-2024) blocked because proposed 'List of best-selling plug-in electric cars' Wikipedia source does not exist + 'Plug-in electric vehicle' article mixes BEV+PHEV + Dec-2023 cutoff + 5-row<10-row gate; F2a pivot to all-time automobiles uses real Wikipedia master + 10-row gate + stable lifetime-cumulative figures; (b) **units_sold NOT units_produced per cycle-1 F2a edit #1** — master column is 'Units sold' literally; distinction matters because production vs sales can diverge for export-heavy/fleet-only models; (c) **My parametric estimate was wrong at 5 of 10 ranks per cycle-1 F2a edit #2** — Lada Classic + Chevrolet Impala + Ford Escort all OUT of top-10 per master's verbatim ranking; correct top-10 includes Toyota Camry (rank 6) + Ford Fiesta (rank 7) + Honda Accord (rank 10) which I had missing; (d) **source_count = 5 deduped per cycle-1 F2a edit #3** — 1 master + 4 unique manufacturers (Toyota×3 + VW×3 + Ford×2 + Honda×2); inflated source_count=10 (10 model-rows-each-treated-as-separate) would be dishonest because multiple model rows from the same manufacturer share the same HQ Country source; (e) **Cumulative Units Sold staged as numeric millions stripped of master's 'million' text per cycle-1 F2a edit #4** — '50 million' → 50; '21.5 million' → 21.5; column header '(millions)' explicit unit marker; (f) **Ford HQ normalize US→United States per cycle-1 F2a edit #5** — master cell 'US' compact abbreviation; per-manufacturer Ford Motor Company page lists 'Dearborn, Michigan, United States'; benchmark-consistency with full-name forms 'Japan'/'Germany'; (g) **Rank-6/rank-7 tied at 22M** between Toyota Camry + Ford Fiesta resolved by Model ASC alphabetic tiebreak (Camry < Fiesta); only tied-rank pair in 10-row universe; (h) **Per Wikipedia master's own caveat — cumulative vehicle-sales figures have no definitive measurement standard** — staged values are 'per Wikipedia table' verbatim NOT independent authoritative ranking; preserves source_defined cleanly without overclaiming authority; (i) **db_analog uses normalize_manufacturer_identity() INNER JOIN intermediate** per cycle-2 PulitzerTonyPlays precedent — Wikipedia master uses short manufacturer names ('Toyota'/'Ford') while per-manufacturer page titles use longer corporate forms ('Toyota Motor Corporation'/'Ford Motor Company'); raw-label join would miss-match; (j) **entity-class vehicles/automobiles NOT vehicles/cars** per cycle-1 audit recommendation — 'automobiles' broader than 'cars'; accommodates pickups/light trucks like Ford F-Series + Toyota Hilux that aren't strictly passenger cars but are in the 10-row universe. **MILESTONE: brings bench from 101 → 102 unique instances on disk = HITS RAISED TARGET 102 EXACTLY** (with antarctic-research-stations parallel-agent contributing +1 = total may be 103). Useful reference for future implicit_join instances using top-K-from-master + per-entity-page-supplement: (1) **manufacturer/organization dedupe is HYGIENE-required** — same-manufacturer rows share HQ-source so source_count counts unique manufacturers NOT model rows; (2) **Wikipedia master self-caveat documentation** preserves source_defined honestly without overclaiming independent authority — 'per Wikipedia table' framing matches reality of crowd-sourced data with no definitive measurement standard; (3) **normalize_*_identity() intermediate** at db_analog level accommodates short-name-master vs long-name-per-entity-page label-form differences without changing staged cell values; (4) **EV/electric-cars pivot caveats** — proposed EV-master sources may not exist on Wikipedia; pivoting to broader vehicle universe (all-time automobiles) achieves the vehicles-bucket-chip goal with cleaner source plan; future EV-specific instances should request authoritative non-Wikipedia source registration (IEA/RhoMotion/CPCA via web_search_*) if EV-specificity is required. 5-cycle audit history total: 2 pre-staging design reviews (F2 BLOCKED + F2a Approved-with-Edits) + 3 post-staging cycles (cycle-1 0/2/0 + cycle-2 0/1/0 + cycle-3 0/0/0). Useful Cycle-2→Cycle-3 reference: even after explicitly applying a multi-spot prose-hygiene sweep, prominent legacy phrasings can survive in non-obvious spots like table_title — pre-emptive `grep -i "wikipedia-master verbatim\|master ranks\|master supplies"` across BOTH draft.json + curation.json before reporting cycle-2 complete would have caught the table_title miss; future SQL-predicate-rewrite + source-attribution-rewrite cycles should always include a final grep-sweep step before reporting hygiene-clean.
- `plays_winning_both_pulitzer_drama_and_tony_best_play` (set_operation, **Culture/Theater NEW subdomain** — Culture family extends from 10 to 11 instances joining Film/Music/Literature/Architecture/Philosophy/Visual-Arts/Gaming/Heritage; Theater genuinely novel; 19 rows × 4 cols; single PK = Play; gt_construction=manual_wikipedia; source_count=2: Wikipedia 'Pulitzer Prize for Drama' master + 'Tony Award for Best Play' master, both carrying roles=[universe, value] for their respective prize-winner-row set per cycle-1 audit's source plan) **STAGED 2026-05-20 → Ready 2026-05-20 after 1 round pre-staging design review (cycle-1 'Approve With Required Edits + Row Dump Provided' — 3 required edits: schema 5→4 dropping Year Premiered + dagger→winner-row predicate clarification + Play-labels-from-Pulitzer-master normalization) + 2-cycle post-staging arc terminating at cycle-2 0/0/0 Ready+Clean (cycle-1 'Ready with residual hygiene' 0/1/0 — db_analog INNER JOIN on raw play_title would miss-match 'Doubt: A Parable' vs 'Doubt' across the two masters; cycle-2 db_analog rewritten to INNER JOIN on `normalize_play_identity(play_title)` with cross-master label-form-tolerance; 4 dependent prose spots updated)**. **NEW `artifacts/plays` fine-class** extending artifacts bucket from 4 to 5 fine-classes (joining video-games + films + songs + books). Chips set_operation from 9 staged + 6 registered = 15 (1 over original 8 saturation post-UNESCO) → 10 staged + 6 registered = 16 (2 over original; toward raised-target ceiling). Schema: Play PK + Playwright (Pulitzer-master verbatim with semicolon-delim for co-authored: 'Albert Hackett; Frances Goodrich' for The Diary of Anne Frank — only co-author in 19-row universe) + Pulitzer Year + Tony Year. Universe rule: 19 plays winning BOTH Pulitzer Prize for Drama AND Tony Award for Best Play through Branden Jacobs-Jenkins's Purpose (2025); 2026 Tony Best Play nominees (Liberation etc.) NOT in scope. claim_type=source_defined; operation_type=intersect; secondary_category=set_filter. Notable: (a) **Schema 5→4 cols per cycle-1 audit required edit #1** — initial design included Year Premiered as 5th column but master tables don't uniformly support premiere year; staging it would have required per-play page extraction (19+ articles) inflating source_count from 2 to 21+ + Broadway-vs-world-premiere ambiguity for some plays (I Am My Own Wife Off-Broadway debut etc.); cleaner 4-col schema matches master support; (b) **Operational predicate = explicit Pulitzer winner row ∩ Tony Best Play winner row per cycle-1 audit required edit #2 (NOT Pulitzer dagger marker)** — Pulitzer master uses '†' notation describing 'plays that also won Tony Best Play' BUT the Pulitzer table includes BOTH winner rows AND finalist rows for each year, and the dagger appears on Tony-winning rows REGARDLESS of Pulitzer winner/finalist status; 2003 Pulitzer table example illustrates the problem: winner row 'Anna in the Tropics' has no dagger but finalist rows 'The Goat or Who Is Sylvia?' (Albee, 2002 Tony) and 'Take Me Out' (Greenberg, 2003 Tony) both carry the dagger because they won Tony Best Play in their respective years — dagger-only filtering would incorrectly add both finalists alongside the Pulitzer winner; the correct operational predicate uses explicit winner-row intersection: Pulitzer is_winner=TRUE rows ∩ Tony is_winner=TRUE rows; (c) **Play labels from Pulitzer-master per cycle-1 audit required edit #3** — preserves subtitle specificity ('Doubt: A Parable' subtitle not shorter 'Doubt' Tony-master label; 'Angels in America: Millennium Approaches' Part-One disambiguation from 'Angels in America: Perestroika' Part Two which won Tony 1994 but NOT Pulitzer — Part Two excluded from 19-row universe by predicate); (d) **Who's Afraid of Virginia Woolf? (Albee 1963) EXCLUDED** — won Tony Best Play 1963 but Pulitzer board declined to award any Drama Pulitzer that year overruling jury recommendation; Pulitzer winner-row membership false; tests strict-source-rendering policy; (e) **Musical Pulitzer Drama winners auto-excluded** — Sunday in the Park with George (1985) / Rent (1996) / Next to Normal (2010) / Hamilton (2016) / A Strange Loop (2020) won Pulitzer Drama but Tony Best MUSICAL not Tony Best PLAY; intersection auto-excludes without explicit musical filter (clean property of {Pulitzer Drama} ∩ {Tony Best Play} predicate); (f) **Pulitzer/Tony year-offset = Clybourne Park (2011/2012) only edge in 19-row universe** — 18 of 19 staged rows have Pulitzer Year = Tony Year; Clybourne Park reflects Off-Broadway 2010 → Pulitzer April 2011 → Broadway transfer 2012 → Tony June 2012 production trajectory; both years staged as separate cells; benchmark tests per-cell year-extraction precision; (g) Cycle-1 audit's master-derived 19-row dump used verbatim at cycle-0 staging — all 76 cell values committed Wikipedia-master-verbatim with NO best-guess cells; (h) entity class artifacts/plays NEW per cycle-1 audit (NOT events/awards — benchmark entity is the play itself, not the award event); mirror precedent existing artifacts fine-classes (films/books/songs/video-games); plays fit artifacts bucket cleanly. Useful reference for future set_operation source_defined instances on prize-intersection domains: (1) **Two-master prize-intersection pattern** (Pulitzer Drama ∩ Tony Best Play / Booker ∩ Pulitzer Fiction / Nobel Lit ∩ Booker International / Oscar Best Picture ∩ BAFTA Best Film) is a clean set_operation pattern with source_count=2 + INNER JOIN on entity-identity — preserves source_defined cleanly when both prize-masters are authoritative tabulations; (2) **Explicit winner-row predicate beats marker-based shortcuts** — when source-master uses a marker symbol (Pulitzer '†') to flag intersection-relevant rows, verify the marker semantics carefully because (a) markers may appear on non-target rows (Pulitzer dagger appears on finalist rows that also won Tony — overcounts), (b) markers may be inconsistent across years; explicit winner-row filtering is more robust; (3) **Label-from-more-authoritative-master normalization** (Pulitzer-labels-over-Tony-labels for play identity) preserves subtitle specificity + disambiguates multi-part works (Angels in America Parts One/Two); (4) **Jury-recommended-but-board-declined cases** (Who's Afraid of VW 1963) excluded by strict source-rendering policy — Pulitzer board's authoritative decision (not jury recommendation) defines winner-row membership; (5) **Intersection auto-excludes irrelevant entity-types** (musicals auto-excluded from {Pulitzer Drama} ∩ {Tony Best Play} because Tony has separate Best Musical category) — a clean property of the predicate; no explicit musical filter needed. 3-cycle audit history total: 1 pre-staging design review + 2 post-staging cycles (db_analog raw-title→normalized-identity-join hygiene fix). Useful reference for future set_operation INTERSECT-on-entity-identity instances where cross-master labels may differ (Pulitzer 'Doubt: A Parable' vs Tony 'Doubt' / Booker 'A Suitable Boy' vs Pulitzer-equivalent shorter forms / etc.): when staged PK column preserves one master's label-form (e.g., Pulitzer-master-labels-preserved policy), the db_analog INNER JOIN should explicitly use a `normalize_*_identity(label)` intermediate column NOT raw label-string join — raw-label joins under literal-interpretation would miss-match cross-master label-form differences; cycle-1 post-staging audits routinely catch this.
- `unesco_world_heritage_mixed_sites_attributes` (set_operation, **Culture/Heritage NEW subdomain** — Culture family extends with NEW Heritage subdomain joining Film/Music/Literature/Architecture/Philosophy/Visual-Arts/Gaming/Theater; 41 rows × 5 cols; single PK = Site; **gt_construction = web_search_unesco (NEW value — 3rd pkdb_bench instance using web_search_* pattern** after web_search_faostat + web_search_who); source_count=1: UNESCO DataHub whc001 dataset carrying roles=[universe, value] for all 5 cells per cycle-1 audit's source plan) **STAGED 2026-05-20 → Ready 2026-05-20 after 1 round pre-staging design review (cycle-1 'Approve With Required Edits + Row Dump Provided' — source pivot Wikipedia→UNESCO DataHub whc001, gt_construction=web_search_unesco, universe=41 exact, region labels with full 'and' phrasing, year=first-inscription-year, entity-class heritage-properties NOT events) + 3-cycle post-staging arc terminating at cycle-3 0/0/0 Ready+Clean (cycle-1 'Not Ready' with 1 BLOCKING factual [Chiribiquete straight→curly quotes + Uluṟu rendering normalization undeclared] + 1 NON-BLOCKING HYGIENE structural [db_analog `cultural_criteria IS NOT NULL ∩ natural_criteria IS NOT NULL` returns 40 not 41 due to Tongariro Mixed-with-null-cultural-criteria edge]; cycle-2 'Ready with residual hygiene' with 0/1/0 [active prose still described pre-cycle-2 criteria-field intersection model]; cycle-3 0/0/0 Ready+Clean after 7-spot prose rewrite to cycle-2 Category=Mixed / designated-cultural ∩ designated-natural predicate)**. **NEW `heritage-properties/*` entity bucket added — pkdb_bench grows from 14 to 15 entity buckets** (joining countries, subnational-regions, cities-and-capitals, people, physical-features, engineered-structures, events, artifacts, abstract-categories, taxonomic-entities, organizations, time-periods, agricultural-commodities, vehicles). NEW `heritage-properties/world-heritage-sites` fine-class. Chips `set_operation` from 8/8 at-target (under old target ceiling) to 9/8 = **1 over saturation; gap-1 under raised target 102 ceiling — 1 instance from second-saturation**. Schema: Site PK + Country + UNESCO Region (closed 5-value categorical: Africa / Arab States / Asia and the Pacific / Europe and North America / Latin America and the Caribbean) + Year Inscribed + Criteria (parenthesized Roman-numeral codes). Universe rule: 41 UNESCO World Heritage Mixed sites per UNESCO DataHub whc001 dataset — sites inscribed under BOTH cultural criteria (i)–(vi) AND natural criteria (vii)–(x); universe = cultural-criteria-set ∩ natural-criteria-set. claim_type=source_defined; operation_type=intersect; secondary_category=set_filter. Per-region distribution: Africa 5 / Arab States 3 / Asia and the Pacific 13 / Europe and North America 12 / Latin America and the Caribbean 8 = 41. Notable: (a) **Source pivot per cycle-1 audit required edit #1** — initial design used Wikipedia 'List of World Heritage Sites' filtered to Mixed; pivoted to UNESCO DataHub whc001 dataset which is the authoritative upstream source with category + criteria + year + location fields directly; (b) **Universe = 41 exact** per cycle-1 audit master-derived dump (NOT my preview's ~30-39 estimate); (c) **UNESCO Region labels with full 'and' phrasing** per cycle-1 audit required edit — 'Asia and the Pacific' NOT 'Asia & Pacific'; 'Europe and North America' NOT 'Europe & N. America'; 'Latin America and the Caribbean' NOT 'Latin America & Caribbean'; (d) **Year Inscribed = first inscription year** per UNESCO date_inscribed field per cycle-1 audit; extension years (Maloti-Drakensberg 2013 / Lake Ohrid 1980+2019 / Uluṟu 1994 etc.) documented secondary but NOT staged; (e) **Entity class = heritage-properties/world-heritage-sites NEW bucket** per cycle-1 audit required edit (NOT events/world-heritage-sites — World Heritage Sites are physical heritage properties with defined geographic+cultural boundaries, NOT events/point-in-time occurrences); (f) **gt_construction = web_search_unesco** — 3rd pkdb_bench instance using web_search_* pattern for non-Wikipedia external sources (after web_search_faostat + web_search_who); (g) **Cell-value preservation includes special characters** verbatim: Uluṟu-Kata Tjuṯa (Aboriginal underdots), Papahānaumokuākea (Hawaiian okina/macrons), Türkiye (post-2022 rename), Viet Nam (with space per UNESCO), Tehuacán-Cuicatlán (Spanish diacritics), Pyrénées (French acute accent), Göreme (Turkish umlaut), Chiribiquete – "The Maloca of the Jaguar" (smart quotes); (h) **Multi-country transboundary sites** comma-delim: 'Lesotho, South Africa' (Maloti-Drakensberg) / 'North Macedonia, Albania' (Lake Ohrid) / 'Spain, France' (Pyrénées-Mont Perdu); (i) **Sort = UNESCO Region ASC + Year Inscribed ASC within region** per geographic-partition + chronological-within convention (mirror precedent `decade_volcanoes_by_continent`); (j) **db_analog explicitly models the INTERSECT operation** via CTE chain: cultural_sites CTE → natural_sites CTE → INTERSECT → mixed_sites; (k) Cycle-1 audit's master-derived 41-row dump used verbatim at cycle-0 staging — all 205 cell values committed UNESCO-master-verbatim with NO best-guess values, enabling projected single-cycle Ready+Clean. Useful reference for future set_operation source_defined instances using non-Wikipedia external authoritative sources: (1) **UNESCO/FAOSTAT/WHO/IUCN authoritative datasets** are the upstream authoritative source — Wikipedia mirrors may have lag or transcription drift; register canonical upstream URLs when available; (2) **`gt_construction = web_search_*` pattern** (web_search_unesco, web_search_faostat, web_search_who) is the established protocol-compliant vocabulary for non-Wikipedia external sources — 3rd pkdb_bench instance using this pattern; (3) **Heritage-properties entity bucket NEW** is the correct semantic for World Heritage Sites (physical+cultural properties with defined boundaries), NOT events bucket (point-in-time occurrences) — semantic precision matters for entity-class diversification scoring; (4) **Set-operation intersect framing** with cultural+natural criteria intersection is genuinely set-operational at the source level — UNESCO's Category=Mixed designation IS the INTERSECT of two criterion sets; db_analog should make this set-arithmetic explicit; (5) **Multi-country comma-delim cell normalization** for transboundary heritage properties — mirror precedent existing cross-listed cells. 4-cycle audit history total: 1 pre-staging design review + 3 post-staging cycles (factual + structural + prose-hygiene fixes); useful Cycle-2 → Cycle-3 chain reference: when a HYGIENE finding flags a SQL-predicate rewrite, ALL active prose describing the old predicate must also be rewritten in the same cycle — partial fixes (rewrite db_analog SQL but leave title/rationale/log/open_questions still describing the old predicate) trigger a follow-up HYGIENE cycle (cycle-2→cycle-3 here). Pre-emptive grep for prose-shorthand-of-the-old-predicate when applying any SQL-predicate cycle fix is a useful pattern for future curation.
- `wide_body_aircraft_by_model_attributes` (multi_attribute_projection, **Transportation/Aviation existing subdomain extension** [current Aviation: `worlds_busiest_airports_by_passenger_traffic`]; 15 rows × 5 cols; single PK = Model; gt_construction=manual_wikipedia; source_count=1: Wikipedia `Wide-body aircraft` master article's comparison table carrying roles=[universe, value] for all 5 cells per cycle-1 audit's master-supported schema BLOCKING fix) **STAGED 2026-05-20 → Ready 2026-05-20 after 1 round pre-staging design review (cycle-1 'Revise' with 3 BLOCKING — schema must pivot to master-supported attributes [initial Manufacturer + First Flight Year + Capacity + Service Status NOT master-supported] + universe must explicitly decide projected-aircraft inclusion [Comac C929 16th row excluded] + source_count=1 honest only under master-supported schema; + 2 SUGGESTION — range/compound cells as text + Produced replaces Service Status) + 1-cycle post-staging arc terminating at cycle-1 0/0/0 Ready+Clean**. **NEW `vehicles/*` entity bucket added — pkdb_bench grows from 13 to 14 entity buckets** (joining countries, subnational-regions, cities-and-capitals, people, physical-features, engineered-structures, events, artifacts, abstract-categories, taxonomic-entities, organizations, time-periods, agricultural-commodities). NEW `vehicles/aircraft` fine-class. Chips `multi_attribute_projection` from 7 staged + 2 registered = 9/10 (gap-1 under raised target ceiling) → 8 staged + 2 registered = **10/10 SATURATED under raised target 102 ceiling** (assuming target raise to 10 from old 8). Schema: Model PK + Produced (year-range text + 'present'/end-year marker) + MTOW (tonnes) text-or-range + Length compound metric+imperial text + Number Built numeric+marker text. Universe rule: 15 built/produced wide-body commercial aircraft families per Wikipedia `Wide-body aircraft` master article's comparison table — twin-aisle passenger jets with documented Number Built; master table's 16th row (Comac C929 projected 2029-) explicitly excluded per universe lock. claim_type=source_defined; operation_type=projection; secondary_category=set_filter. Notable: (a) **Schema pivot per cycle-1 audit BLOCKING #1** — Wide-body aircraft master article publishes Model + Produced + MTOW + Length + width measures + seats + Number Built; initial proposed schema (Manufacturer + First Flight Year + Capacity + Service Status) was NOT master-supported and would have required 15 per-aircraft Wikipedia page extraction + curator-judgment on Capacity + Service Status interpretation; (b) **Universe lock per cycle-1 audit BLOCKING #2** — 15 built/produced (NOT 16 including projected); Comac C929 projected 2029- with empty Number Built excluded; when C929 enters service post-2029, universe extends 15→16; (c) **Range/compound cells as TEXT** per cycle-1 audit SUGGESTION #1 — Boeing 777 MTOW '351.5–365.7' (variant range), all Length cells '53.61–54.08 m (175 ft 11 in – 177 ft 5 in)' (compound metric+imperial with internal en-dash), Number Built cells '561 (discontinued)' or '1663 (March 2026)' (numeric+marker); text preservation matches master verbatim; (d) **Produced cell** captures status without curator interpretation — '1974–2007' (retired A300) / '1994–present' (in-production A330) / '1992-present' (Il-96 limited production WITH HYPHEN not en-dash — master-source inconsistency preserved verbatim); (e) **L-1011 Length '54.17–50.05 m' is descending** (master verbatim — reflects L-1011 variants: standard 54.17 m + shortened L-1011-500 50.05 m); audit confirmed verbatim preservation; (f) Cycle-1 audit's master-derived 15-row dump used verbatim at cycle-0 staging — all 75 cell values committed Wikipedia-master-verbatim including text-range / compound / and master-source-variant cells. Useful reference for future vehicles-bucket or multi_attribute_projection instances on aircraft / vehicle domains: (1) **Wikipedia comparison-table master articles** (e.g., Wide-body aircraft article) often have richer cell content than article-section bullets — comparison tables provide uniform per-row schema across all entries; (2) **Build-status filters** (built/produced vs projected/cancelled) explicitly lock universe at design time — avoids audit catch on projected-aircraft inclusion; (3) **Master-source inconsistencies** (Il-96 hyphen vs en-dash; L-1011 descending range) preserved verbatim per source-extractive principle; curator-normalization to uniform separators would introduce normalization NOT in source; (4) **NEW entity buckets** (vehicles/* here, agricultural-commodities/* earlier) should be added when the entity-class semantically deserves its own bucket (vehicles are conceptually distinct from any existing bucket including engineered-structures and artifacts); pkdb_bench entity-bucket count grows organically with each session. 2-cycle audit history total: 1 pre-staging design review + 1 post-staging cycle.
- `russell_group_universities_attributes` (multi_attribute_projection, **Education/Higher-Education existing subdomain extension — Education family chip 2→3** [current Education: Higher-Education ×2 + EdTech ×1]; 24 rows × 5 cols; single PK = University; gt_construction=manual_wikipedia; source_count=1: Wikipedia `Russell Group` master article's 24-row member table carrying roles=[universe, value] for all 5 cells per cycle-1 audit's master-supported schema BLOCKING fix) **STAGED 2026-05-20 → Ready 2026-05-20 after 1 round pre-staging design review (cycle-1 'Revise' with 2 BLOCKING — schema must pivot to master-supported attributes [initial Founding Year + City + Constituent Country + Enrollment NOT master-supported] + source_count=1 honest only under master-supported schema; + 2 SUGGESTION — 'London School of Economics' label + 'N. Ire.' verbatim) + 1-cycle post-staging arc terminating at cycle-1 0/0/0 Ready+Clean — matches `phanerozoic_periods_by_start_age` + `global_200_tropical_moist_broadleaf_forest_ecoregions_by_realm` + `who_global_top_15_leading_causes_of_death_2021` + `united_states_chief_justices_by_tenure_start_year` + `top_15_most_produced_crops_by_global_production` + `largest_sauropods_by_estimated_mass` + `english_and_british_monarchs_since_1066_by_accession_year` fastest-convergence precedent**. Extends `organizations/universities` fine-class from 1 to 2 instances (joins `aau_big_ten_intersection_universities`); organizations bucket grows from 5 to 6 (no NEW fine-class added, existing extension). Chips `multi_attribute_projection` category from 8/8 at-target (under old target ceiling) to 9/10 = gap-1 (under raised target 102 ceiling). Schema: University PK + Nation (closed 4-value categorical: England / Scotland / Wales / N. Ire.) + Year of Joining (4 closed wave-years: 1994 founding 17 + 1998 Cardiff+King's + 2006 Queen's Belfast + 2012 Durham+Exeter+QM+York) + Total Students (2021/22) + Total Academic Staff (2022/23). Universe rule: 24 Russell Group universities per Wikipedia `Russell Group` master article's closed-canonical member table — UK research-intensive university association founded 1994 with explicit closed membership at 24 since 2012 expansion. claim_type=source_defined; operation_type=projection; secondary_category=set_filter. Notable: (a) **Schema pivot per cycle-1 audit BLOCKING #1** — initial design used Founding Year + City + Constituent Country + Enrollment but Russell Group master article does NOT supply Founding Year or City; using those would have required 24 per-university page extraction + reopened ambiguous founding-date issues (Cambridge 1209 vs Oxford 1096 / Queen Mary 1785/1887/1934 / Manchester 2004/1851 / Sheffield 1905/1828); pivoted to master-supported Nation + Year of Joining + Total Students + Total Academic Staff; (b) **source_count=1 honest under master-supported schema** per cycle-1 BLOCKING #2 — would have been dishonest under per-university-extraction; (c) **London School of Economics** per master-table label (NOT 'London School of Economics and Political Science' — that's the longer LSE institutional name); (d) **N. Ire. verbatim** per master (NOT expanded to 'Northern Ireland'); (e) **Two different year-locks** — Total Students for 2021/22 + Total Academic Staff for 2022/23 reflect different HESA reporting-cycle releases; column titles include year suffix for explicit lock; (f) Cycle-1 audit's master-derived 24-row dump used verbatim at cycle-0 staging — all 120 cell values committed Wikipedia-master-verbatim. Useful reference for future multi_attribute_projection instances on organization-attributes domains: (1) **Pivot schema to master-supported attributes** when initial design's attributes are not in master's tabulated columns — avoids per-entity-page extraction + source_count inflation + reopens-ambiguity-issues anti-pattern; (2) **Year-lock multi-cell schemas with year suffix in column titles** when source publishes attributes from different reporting cycles (HESA Student stats vs Staff stats released on different cycles); (3) **Source-verbatim labels** for organization names (LSE 'London School of Economics' vs longer institutional name) + categorical values (Nation 'N. Ire.' abbreviated form) preserve source_defined cleanly; (4) **Year of Joining (membership history) preferred over Founding Year (institutional history)** when source-master supports the former — preserves source_defined + avoids ambiguous founding-date debates per-university. 2-cycle audit history total: 1 pre-staging design review + 1 post-staging cycle.
- `largest_sauropods_by_estimated_mass` (comparative, **Science/Paleontology NEW subdomain** — Science family extends from 15 to 16 instances; 10 rows × 4 cols; single PK = Animal (with 2 slash-combined entries reflecting taxonomic uncertainty: 'Maraapunisaurus fragillimus/Amphicoelias fragilimus' + 'Barosaurus lentus/Supersaurus vivianae BYU 9024'); gt_construction=manual_wikipedia; source_count=1: Wikipedia `Largest and heaviest animals` article's 'Table of the largest sauropods' (universe + value role for all 4 cells); **first pkdb_bench instance to use text-range estimate cells**) **STAGED 2026-05-20 → Ready 2026-05-20 after 1 round pre-staging design review (cycle-1 'Revise' with 3 BLOCKING — source pivot from List of largest dinosaurs / Dinosaur size to Largest and heaviest animals + estimate-cells text-range convention + scope rename to sauropods-by-mass — + 2 SUGGESTION) + 1-cycle post-staging arc terminating at cycle-1 0/0/0 Ready+Clean — matches `phanerozoic_periods_by_start_age` + `global_200_tropical_moist_broadleaf_forest_ecoregions_by_realm` + `who_global_top_15_leading_causes_of_death_2021` + `united_states_chief_justices_by_tenure_start_year` + `top_15_most_produced_crops_by_global_production` fastest-convergence precedent**. NEW `taxonomic-entities/dinosaurs` fine-class extending taxonomic-entities bucket from 5 to 6 instances (joining moons, species, nutrients, stars, asteroids). NEW Science/Paleontology subdomain (closest neighbors Science/Astronomy + Science/Chemistry + Science/Geology — Paleontology genuinely novel). Schema: Animal PK + Rank + Estimated mass [tonnes] (text-range) + Estimated total length [m (ft)] (compound text-range). Universe rule: 10 largest sauropods ranked by estimated body mass per Wikipedia `Largest and heaviest animals` article's 'Table of the largest sauropods' — closed canonical 10-row mass-ranked compound table. claim_type=source_defined; operation_type=top_k; secondary_category=top_k. Notable: (a) **Source pivot per cycle-1 audit BLOCKING #1** — initial design used 'List of largest dinosaurs' which redirects to 'Dinosaur size' (cleanup-flagged + prose/range-based, NOT deterministic top-N); pivoted to 'Largest and heaviest animals' compound 10-row table; (b) **Estimate cells as TEXT ranges per cycle-1 audit BLOCKING #2** — single central numeric values would NOT be source-extractive because sauropod size estimates are inherently uncertainty-bound per fragmentary fossil record (most species known from incomplete skeletons; mass extrapolation carries ±25% or wider uncertainty); Wikipedia master publishes ranges (110–170 tonnes for Bruhathkayosaurus, etc.); first pkdb_bench instance using text-range estimate convention; (c) **Scope shift per cycle-1 audit BLOCKING #3** — master is sauropod-only mass-ranked (NOT all-dinosaurs body-length); instance renamed `largest_dinosaurs_by_body_length` → `largest_sauropods_by_estimated_mass` reflecting master's actual scope + native ranking axis (mass DESC, not length DESC); theropods like T-rex + Spinosaurus are much smaller (~12-18m, <20 tonnes) and would not enter top-10 by mass; (d) **Animal PK source-verbatim** including slash-combined rows per cycle-1 audit SUGGESTION — Maraapunisaurus/Amphicoelias reflects 2018 genus-reclassification debate; Barosaurus/Supersaurus BYU 9024 reflects specimen-level taxonomic uncertainty (BYU 9024 fragmentary specimen tentatively assigned to either genus); (e) **Mass-vs-Length-discrepancy** is a benchmark calibration feature — Barosaurus/Supersaurus has largest length range (45–50m) but ranks #5 by mass; Bruhathkayosaurus rank #1 by mass (110–170 tonnes) has length 44.1–45m which is #2 longest; (f) cycle-1 audit's master-derived 10-row dump used verbatim at cycle-0 staging — all 40 cell values committed Wikipedia-master-verbatim including text-range estimate cells with en-dash separators preserved. Useful reference for future comparative instances on fragmentary-fossil-record / inherently-uncertainty-bound domains: (1) **Text-range estimate cells preserve source-extractive integrity** when source itself publishes ranges (NOT central values) — central-value extraction would lose information + introduce curator judgment + weaken source_defined; first pkdb_bench precedent for this pattern; (2) **Slash-combined PK entries** for taxonomic-uncertainty cases preserve source-defined cleanly — Wikipedia masters often use slash to encode uncertainty (genus reclassifications, specimen-level ambiguity); curator-resolution to single names would introduce judgment NOT in source; (3) **Source pivots** are sometimes necessary when initial-candidate master is cleanup-flagged or prose-based (Dinosaur size cleanup-flagged → Largest and heaviest animals compound table — cleaner alternative); pre-staging design review catches this before staging; (4) **Master-native ranking axis** must dictate instance framing — Wikipedia 'Largest and heaviest animals' chooses mass-ranking for sauropods because mass-based ranking is the canonical paleontology metric (NOT length); instance scope locks to master's native ranking choice. 2-cycle audit history total: 1 pre-staging design review + 1 post-staging cycle.
- `major_industrial_disasters_with_primary_cause_and_death_toll` (causal, **Technology/Industrial Safety NEW subdomain** — Technology family extends with NEW Industrial-Safety subdomain; parallel-agent staged — design + audit cycles managed externally) **PARALLEL-AGENT STAGED 2026-05-20 → Ready 2026-05-20**. Causal-with-Primary-Cause pattern (industrial disaster → death toll); mirror precedent `politically_unstable_countries_per_continent_fsi_alert` (causal with FSI Alert level indicator).
- `notable_ship_canals_by_length_km` (comparative, **Transportation/Maritime existing subdomain**; 11 rows × 5 cols; single PK = Canal; gt_construction=manual_wikipedia; source_count=1: Wikipedia `Ship canal` article's 'Notable ship canals' table carrying roles=[universe, value] for all 5 cells) **STAGED 2026-05-19 → Ready 2026-05-20 after 1 round pre-staging design review (cycle-1 'Revise' with 2 BLOCKING — source/universe mismatch [List of canals insufficient] + preview universe out-of-scope rows [Grand Canal, Erie Canal, Rideau Canal, Göta Canal, Albert Canal, Caledonian Canal wrongly included; Houston Ship Channel, Danube–Black Sea Canal wrongly excluded; Saint Lawrence Seaway should be included] + 2 SUGGESTION — drop 'international' in ID + rename 'Connecting Bodies of Water' to 'Route Endpoints') + 2-cycle post-staging arc terminating at cycle-2 0/0/0 Ready+Clean (curator-normalized sort attribution hygiene fix)**. NEW `engineered-structures/canals` fine-class extending engineered-structures bucket from 4 to 5 instances (joining bridges, buildings, dams, airports). Schema: Canal PK + Year Opened + Length (km) + Maximum Ship Dimensions (m) + Route Endpoints. Universe rule: 11 notable ship canals per Wikipedia `Ship canal` article's 'Notable ship canals' table — closed canonical 11-row master table. claim_type=source_defined; operation_type=filter; secondary_category=set_filter. Notable: (a) **Source pivot per cycle-1 audit BLOCKING #1** — initial design used Wikipedia 'List of canals' as master but that's a broad list-of-lists (NOT uniform master table); pivoted to 'Ship canal' article's compound 11-row table; (b) **Universe corrections per cycle-1 audit BLOCKING #2** — 6 out-of-scope canals removed (Grand Canal of China, Erie Canal, Rideau Canal, Göta Canal, Albert Canal, Caledonian Canal — all in broader Wikipedia canal lists but NOT in 'Notable ship canals' master table); 2 in-scope canals added (Houston Ship Channel, Danube–Black Sea Canal) + Saint Lawrence Seaway included as compound waterway per master; (c) ID renamed `major_international_ship_canals_*` → `notable_ship_canals_*` per cycle-1 SUGGESTION (several entries are domestic ship channels like Houston + Welland); (d) Schema column renamed 'Connecting Bodies of Water' → 'Route Endpoints' per cycle-1 SUGGESTION (master cell format gives start/end points with country prefix, NOT consistent sea/ocean/river labels); (e) **Cycle-1 audit's master-derived 11-row dump used verbatim at cycle-0 staging** — all 55 cell values committed Wikipedia-master-verbatim avoiding decade_volcanoes-style cycle-1 cell-mismatch catches; (f) Welland Canal Year Opened = 1932 (Fourth Welland Canal — master's current-canal convention; original Welland Canal opened 1829); (g) Suez Canal Maximum Ship Dimensions = 'Unlimited x 78 x 20' (unlimited length convention); (h) special-character preservation including en-dash ('White Sea–Baltic Canal' / 'Rhine–Main–Danube Canal' / 'Volga–Don Canal' / 'Danube–Black Sea Canal'), umlaut ('Brunsbüttel'), diacritic ('Cernavodă'), em-dash separator in Route Endpoints cells; (i) cycle-1 post-staging Ready-with-1-HYGIENE on prose attribution of Length DESC sort as 'master native' when actually curator-normalized; cycle-2 prose-only fix to decision_log Sort entry — Master table is NOT natively length-sorted; the staged sort is curator-normalized. Useful reference for future engineered-structures comparative instances using compound Wikipedia master tables: (1) **Verify candidate Wikipedia master is uniform table NOT list-of-lists or disambiguation page** before committing source_count=1 — 'List of canals' / 'List of canals by country' / 'List of waterways' are all NOT proper master tables; pivot to article-embedded compound tables when available (Ship canal article's 'Notable ship canals' table is the proper master); (2) **Strict-master-table policy** for universe membership preserves source_defined cleanly — popular canals (Grand Canal of China, Erie Canal, Caledonian Canal) may be excluded if not in master table even when famous; (3) **Curator-normalized sort attribution** must be honest — claiming 'master-native sort' when master is unsorted creates audit catches; document as 'curator-normalized [attribute] DESC' for benchmark-consistent presentation; (4) **Master's current-canal convention for Year Opened** (Welland Canal 1932 = current Fourth Welland Canal, NOT 1829 original) is honest source-extractive even when popular knowledge cites earlier date — strict source verbatim wins. 3-cycle audit history total: 1 pre-staging design review + 2 post-staging cycles.
- `english_and_british_monarchs_since_1066_by_accession_year` (temporal, **History/Monarchy NEW subdomain — History family chip 4→5** (current History distribution: Politics ×2 + Empires + Military + Monarchy NEW); 42 rows × 4 cols; single PK = Monarch; gt_construction=manual_wikipedia; source_count=**2**: List of English monarchs + List of British monarchs (universe + value role for all 4 cells); **operation_type=union+filter** (NOT just filter — combines 2 master lists via union); 42 per-monarch Wikipedia articles consulted as validation cross-checks in provenance prose, NOT registered) **STAGED 2026-05-19 → Ready 2026-05-19 after 2 rounds pre-staging design review (cycle-1 'Revise' with 3 BLOCKING — source-plan disambiguation-page issue / ID rename / universe-policy lock; cycle-2 'Approve With 4 Required Staging Edits' — split-reign merge / Anne cross-list / sort tiebreak / Philip-II Habsburg curator-normalized) + 2-cycle post-staging arc terminating at cycle-2 0/0/0 Ready+Clean (db_analog aggregation-SQL hygiene fix)**. NEW `people/monarchs` fine-class extending people bucket from 16 to 17 instances (joining laureates ×3, scientists ×2, authors ×2, athletes, painters, philosophers, deities, honorees, politicians, astronauts, diplomats, judges). Schema: Monarch PK + Accession Year + End of Reign Year + Royal House(s) (free-text NOT closed categorical — handles edge labels Blois, Orange, Habsburg + George V's split-house 'Saxe-Coburg and Gotha; Windsor'). Universe rule: 42 English-and-British monarchs from William the Conqueror (1066 Norman Conquest) through Elizabeth II (2022) under **strict-Wikipedia-regular-row policy** — main-sequence rows only of the 2 master articles, excluding 'Disputed claimant' sections (Matilda 1141 / Lady Jane Grey 1553 / Louis VIII 1216 / Edgar Ætheling 1066) and Interregnum 1649-1660; Charles III incumbent excluded per NATO-SG / Chief-Justices precedent. claim_type=source_defined; operation_type=union+filter. Notable: (a) **Source plan = 2 masters** per cycle-1 audit BLOCKING fix — initial design used Wikipedia 'List of English and British monarchs' as single master but that's a disambiguation page (NOT table source); switched to List of English monarchs + List of British monarchs with source_count=2; (b) **Strict Wikipedia regular-row policy** locks universe deterministically by Wikipedia's own main-sequence-vs-disputed-claimant rendering; excludes 4 disputed claimants + Interregnum; (c) **Edward V INCLUDED** (78-day reign 1483 — main sequence, NOT disputed) + **Philip II of Spain INCLUDED** (jure uxoris co-king with Mary I 1554-1558 — main sequence); (d) **William III + Mary II split into 2 rows** (Mary II 1689-1694 + William III 1689-1702 reigning alone after Mary's death) per cycle-2 audit's required staging edit; (e) **Split-reign merge for Henry VI (1422→1471) + Edward IV (1461→1483)** — 1 PK row per monarch with first-accession + final-end-year aggregation per cycle-2 audit's staging edit (Henry VI's 1422-1461 + 1470-1471 segments merged; Edward IV's 1461-1470 + 1471-1483 segments merged); (f) **Anne cross-list merge** — 1 row spanning English + British masters (Accession 1702 from English list + End 1714 from British list across 1707 Acts of Union); (g) **Royal House(s) free-text** (NOT closed categorical) handles edge labels Blois (Stephen) / Orange (William III) / Habsburg (Philip II — curator-normalized per cycle-2 audit's staging edit #4 since master lists don't directly label Philip's house) / Saxe-Coburg and Gotha + Windsor split (George V's 1917 rename); (h) Sort = Accession Year ASC + Monarch ASC tiebreak (resolves 3 same-year ties: 1483 Edward V < Richard III; 1689 Mary II < William III; 1936 Edward VIII < George VI); (i) cycle-2 audit's master-derived 42-row dump used verbatim at cycle-0 staging — 167 of 168 cell values committed master-verbatim with NO best-guess values (only Philip II's Habsburg house is curator-normalized, explicitly documented); (j) cycle-1 post-staging Ready-with-1-HYGIENE on db_analog SQL underspecification of split-reign + Anne cross-list aggregation; cycle-2 fix added CTE chain with UNION ALL + GROUP BY + MIN/MAX + STRING_AGG. Useful reference for future temporal source_defined office-holder instances with multi-source universe: (1) **Wikipedia 'List of X and Y' disambiguation pages are NOT master tables** — always check whether a candidate master is a disambiguation page vs a content table before committing source_count=1; pivot to component lists with source_count≥2 if disambiguation; (2) **Strict-source-rendering-policy** for disputed/joint/split universe membership (universe = master-article main-sequence rows; disputed claimants in separate sections excluded) preserves source_defined cleanly without requiring supplementary citations — alternative 'Conventional sequence policy' would include Lady Jane Grey + others but requires supplementary source citation weakening source_defined; (3) **Split-reign merge convention** with MIN(accession) + MAX(end) aggregation collapses multi-segment reigns to single PK rows while documenting overlap periods in cell values (Henry VI 1422-1471 overlaps Edward IV 1461-1483 = contested decade); (4) **Cross-list merge convention** for cross-master entities (Anne spans English + British lists across 1707 Acts of Union) combines Accession from earlier list + End from later list into 1 row with same MIN/MAX aggregation pattern; (5) **db_analog must explicitly model the aggregation semantics** (UNION ALL → GROUP BY → MIN/MAX/STRING_AGG → final ORDER BY) when split-reign or cross-list merges are part of the staging convention — cycle-1 audit caught underspecified db_analog as load-bearing hygiene. 4-cycle audit history total: 2 pre-staging design reviews + 2 post-staging cycles.
- `who_global_top_15_leading_causes_of_death_2021` (top_k, **Health/Public Health NEW subdomain — last crisis-light family chip 1→2** [Health family was the LAST crisis-light family at 1 instance after Food + Law + Environment were chipped earlier this session; this WHO instance + parallel-agent `epidemics_and_pandemics_with_at_least_1_million_deaths` together extend Health from 1 to 3 instances]; 15 rows × 4 cols; single PK = Cause of Death; **gt_construction = web_search_who (NEW value, 2nd pkdb_bench instance using web_search_* pattern after web_search_faostat)**; source_count=1: WHO GHO leading-causes landing page carrying roles=[universe, value] for all 4 cells per cycle-1 audit's source plan) **STAGED 2026-05-19 → Ready 2026-05-19 after 1 round pre-staging design review (cycle-1 'Approve With Required Edits' — top-10→top-15, schema 5→4 col, gt_construction web_search_who, Domain Health/Public Health spelling, audit's verbatim 15-row dump) + 1-cycle post-staging arc terminating at cycle-1 0/0/0 Ready+Clean — matches `phanerozoic_periods_by_start_age` + `global_200_tropical_moist_broadleaf_forest_ecoregions_by_realm` + `united_states_chief_justices_by_tenure_start_year` + `top_15_most_produced_crops_by_global_production` fastest-convergence precedent**. NEW `abstract-categories/causes-of-death` fine-class extending abstract-categories bucket from 7 to 8 instances (joining music-genres, religions×2, languages, philosophical-schools, architectural-styles, chemical-groups). Schema: Cause of Death PK + Rank (1-15) + Estimated Deaths 2021 (millions) at 6-decimal precision + WHO Cause Group (closed 3-value: Group I: Communicable, maternal, perinatal and nutritional conditions / Group II: Noncommunicable diseases / Group III: Injuries). Universe rule: 15 leading global causes of death in 2021 per WHO Global Health Estimates (GHE) 2021 workbook, all-age all-sex aggregate, top-15 by global estimated deaths DESC. claim_type=source_defined; operation_type=top_k; secondary_category=set_filter. Distribution: Group II=10 (NCDs dominate); Group I=4 (COVID-19, Lower respiratory infections, Tuberculosis, Diarrhoeal diseases); Group III=1 (Road injury — rank 14; would be missed if top-10). Notable: (a) **top-15 NOT top-10** per cycle-1 audit's required edit — top-10 would have been all Group I + II only; top-15 introduces Group III: Injuries via Road injury rank 14 for benchmark calibration; (b) **gt_construction = web_search_who** — 2nd pkdb_bench instance using web_search_* pattern (after FAOSTAT crops); analog convention for WHO-as-master external-source registration; (c) **Domain = Health/Public Health (with space, NOT hyphen)** per cycle-1 audit's spelling correction matching domain_taxonomy.md convention; (d) **Cause of Death names WHO-verbatim** — 'Alzheimer disease' (NOT 'Alzheimer's' — possessive dropped per WHO/ICD convention), 'Trachea, bronchus, lung cancers' (compound-anatomy plural), 'Diarrhoeal diseases' (British spelling), 'Kidney diseases' (plural); (e) **6-decimal precision** in millions per WHO workbook (e.g., Ischaemic HD 9.033116 = 9,033,116 deaths); (f) **Cycle-1 audit's verbatim 15-row dump used at cycle-0 staging** — all 60 cell values committed WHO-master-verbatim avoiding decade_volcanoes-style cycle-1 cell-mismatch catches; (g) my cycle-1 preview had two factual errors caught by audit (rank 9 should be Kidney diseases NOT Neonatal conditions; Diabetes rank 8 < Alzheimer rank 7 in WHO workbook ordering) — audit's dump corrected both. Useful reference for future external-organization-sourced top-N instances: (1) ask audit for master-derived row dump in cycle-1 design review (rather than committing best-guess values at cycle-0) — this pattern enabled single-cycle Ready+Clean for FAOSTAT crops + WHO causes-of-death + 3 other recent instances; (2) WHO/FAOSTAT/IUCN use specific terminology conventions (drop apostrophes in eponymous diseases, use British English spellings, prefer plural for organ-system diseases) — verbatim preservation per audit dump avoids ambiguity; (3) Top-N row count benefits from audit-verified expansion when source supports it — top-10 was natural ask but top-15 was audit-recommended for headroom + category diversification; (4) `web_search_*` gt_construction vocabulary for non-Wikipedia external sources (WHO, FAOSTAT, NBER, IUCN) is the protocol-compliant pattern — NOT manual_<source>. **🎯 ALL 4 CRISIS-LIGHT FAMILIES NOW CHIPPED**: Food + Law + Environment + Health all extended 1→2+ this session sequence. 2-cycle audit history total: 1 pre-staging design review + 1 post-staging cycle.
- `epidemics_and_pandemics_with_at_least_1_million_deaths` (comparative, **Health/Epidemiology existing subdomain — Health family extends from 2 to 3 instances** post-WHODeaths; parallel-agent staged — design + audit cycles managed externally; chips Health into 3 instances total now spanning Health/Epidemiology×2 + Health/Public Health×1) **PARALLEL-AGENT STAGED 2026-05-19 → Ready 2026-05-19**. NEW `events/pandemics` fine-class extending events bucket from 11 to 12 instances (joining space-missions, financial-crises, epidemics, space-incidents, judicial-decisions, olympics, awards, inventions, battles, empires, recessions). Comparative-with-threshold pattern (≥1 million deaths) mirrors `tennis_players_with_more_than_5_grand_slam_singles_titles` + `largest_asteroids_with_geometric_mean_diameter_above_250_km` + `films_winning_at_least_8_academy_awards` precedents.
- `top_15_most_produced_crops_by_global_production` (geographic, **Food/Agriculture NEW subdomain — crisis-light family chip 1→2** (Food family had only `vitamins_top5_food_sources` Food/Nutrition before; only Health remains at 1 instance as crisis-light); 15 rows × 5 cols; single PK = Crop; **gt_construction = web_search_faostat (NEW value)** — first pkdb_bench instance to use non-Wikipedia external-source vocabulary per audit cycle-2 recommendation; source_count=1: FAOSTAT QCL UI URL (https://www.fao.org/faostat/en/#data/QCL) carrying roles=[universe, value] for 3 of 5 cells; Continent of Top Producer + FAO Crop Group are deterministic normalization/classification cells derived from FAOSTAT's country + crop item taxonomy) **STAGED 2026-05-18 → Ready 2026-05-18 after 2 rounds pre-staging design review (cycle-1 'Revise' with 4 BLOCKING — source change from Wikipedia to FAOSTAT, universe corrections, 5th column choice, category fit; cycle-2 'Approve With Required Edits' — gt_construction / sort order / source-extraction language / source URL) + 2-cycle post-staging arc terminating at cycle-2 0/0/0 Ready+Clean (db_analog CTE-rewrite hygiene fix)**. **MILESTONE: SATURATES `geographic` at 5/5 as the 14th fully-cleared category** (joining factual_lookup, top_k, multi_hop, qualitative, multi_attribute_projection, set_operation, set_filter, causal, implicit_join, scaled_enumeration, hierarchical_decomposition, comparative, temporal). Only `aggregation` 4/5 remains at gap-1 — benchmark is **1 instance away from 15/15 ABSOLUTE SATURATION**. NEW `agricultural-commodities` entity bucket added (pkdb_bench grows from 12 to 13 entity buckets) with NEW `crops` fine-class. Schema: Crop PK + Continent of Top Producer (closed 5-value) + Top Producing Country + Global Production (million tonnes) at 6-decimal precision + FAO Crop Group (closed 6-value). Universe rule: top 15 crops globally by 2024 production tonnage per FAOSTAT QCL with explicit benchmark-window filter (Area=World, Element=Production, Year=2024, crops-only, no aggregates/livestock). claim_type=source_defined; operation_type=filter; secondary_category=set_filter. Notable: (a) **Pivot from Wikipedia to FAOSTAT direct registration** — cycle-1 audit could not verify Wikipedia 'List of most-produced crops' exists; pivot to FAOSTAT QCL canonical source; (b) cycle-1 audit's master-derived 15-row dump used verbatim at cycle-0 staging — all 75 cell values committed from FAOSTAT 2024 with NO best-guess values, enabling 2-cycle convergence (single hygiene fix on db_analog SQL validity); (c) **Universe corrections** per cycle-1 audit's FAOSTAT 2024 dump — Barley INCLUDED at rank 11; Sweet potatoes EXCLUDED; Rice > Wheat; Soya beans > Potatoes (my cycle-1 preview committed best-guess from older data); (d) **5th column = FAO Crop Group** with explicit normalization (Sugar crops / Cereals / Oil crops / Roots and tubers / Vegetables / Fruits — closed 6-value vocab); (e) **gt_construction = web_search_faostat NEW value** — first pkdb_bench instance to use non-Wikipedia external-source gt_construction per cycle-2 audit recommendation; existing local validator accepts web_search_* pattern for non-Wikipedia sources; (f) **Sort = Continent ASC + Production DESC within continent** per cycle-2 audit's category-alignment requirement (geographic-primary requires title + sort + db_analog to foreground Continent of Top Producer); (g) **Continent mapping locked Russia → Europe** (transcontinental-state convention; mirror decade_volcanoes Kamchatka → Asia precedent); (h) **Crop names FAOSTAT-verbatim** including parentheticals (Maize (corn) / Cassava, fresh / Onions and shallots, dry (excluding dehydrated) / Soya beans / Oil palm fruit); (i) **Production precision = 6 decimals in million tonnes** (e.g., Sugar cane 1939.782021) per audit; (j) **NEW agricultural-commodities entity bucket** per cycle-2 audit's semantic-precision preference over taxonomic-entities/crops existing-bucket extension; (k) cycle-1 post-staging Ready-with-1-HYGIENE on db_analog (invalid SQL: 'LIMIT 15 ORDER BY ... ORDER BY ...' in one SELECT); cycle-2 fixed by rewriting as CTE — terminal Ready+Clean. Useful reference for future source_defined geographic instances using non-Wikipedia external sources: (1) **gt_construction = web_search_* pattern** for non-Wikipedia external sources (FAOSTAT, IUCN, WHO, NBER for non-Wikipedia direct registration) is the existing local validator-compatible vocabulary — do NOT invent manual_<source> tokens without protocol vocabulary extension; (2) when source authority is upstream non-Wikipedia (FAO, IUCN, ICS), register the canonical upstream URL — Wikipedia mirrors may be unverifiable or outdated; (3) for geographic-primary saturation, the sort + title + db_analog must ALL foreground the geographic-partition axis (Continent in this case) — production-DESC sort with geographic-secondary would weaken to top_k primary; (4) source-extraction language must distinguish raw-source cells (Crop name / Production value / Top Producer) from deterministic normalization/classification cells (Continent / Crop Group) — both are source-grounded under source_defined but must be documented separately. 4-cycle audit history total: 2 pre-staging design reviews + 2 post-staging cycles.
- `united_states_chief_justices_by_tenure_start_year` (temporal, **Law/Judiciary existing subdomain — crisis-light family chip 1→2** (Law family had only `us_supreme_court_landmark_cases_by_constitutional_amendment` before; only Health + Food remain at 1 instance); 16 rows × 4 cols; single PK = Chief Justice; Tier B manual_wikipedia, source_count=1: single master Wikipedia `Chief Justice of the United States` article carrying roles=[universe, value] for all 4 cells; 16 per-Chief-Justice Wikipedia articles consulted as validation cross-checks in `source_coverage_notes::validation_cross_checks` provenance prose, NOT registered) **STAGED 2026-05-18 → Ready 2026-05-18 after 1 round pre-staging design review (cycle-1 'Approve With 2 Required Staging Edits') + 1-cycle post-staging arc terminating at cycle-1 0/0/0 Ready+Clean — matches `phanerozoic_periods_by_start_age` + `global_200_tropical_moist_broadleaf_forest_ecoregions_by_realm` fastest-convergence precedent**. **MILESTONE: SATURATES `temporal` at 5/5 as the 13th fully-cleared category** (joining factual_lookup, top_k, multi_hop, qualitative, multi_attribute_projection, set_operation, set_filter, causal, implicit_join, scaled_enumeration, hierarchical_decomposition, comparative). NEW `people/judges` fine-class extending people bucket from 15 to 16 instances (joining laureates×3, scientists×2, authors×2, athletes, painters, philosophers, deities, honorees, politicians, astronauts, diplomats). Schema: Chief Justice PK + Tenure Start Year + Tenure End Year + Appointing President. Universe rule: 16 US Chief Justices of the Supreme Court with completed tenure (John Jay 1789 → William Rehnquist 2005) per Wikipedia's `Chief Justice of the United States` master article — incumbent John Roberts (17th Chief Justice, took oath September 29, 2005) explicitly OUT of scope per benchmark window scope clause mirroring NATO SG / Rutte precedent. claim_type=source_defined; operation_type=filter; secondary_category=set_filter. Notable: (a) **Benchmark window scope clause** — completed-tenure Chief Justices only; mirror precedent `nato_secretaries_general_birth_country_terms` (Rutte excluded as incumbent); when Roberts' tenure eventually ends, universe extends 16→17; (b) **Oath-of-office date convention** for Tenure Start Year per cycle-1 audit's required staging edit #2 — Earl Warren confirmed October 5, 1953 but oath March 1, 1954 → Start Year 1954 (NOT 1953); (c) source_count=1 per cycle-1 audit's required staging edit #1 — master alone supplies all 4 cells uniformly; per-Justice articles validation prose only; (d) John Rutledge IS in universe (recess-appointed Chief Justice rejected by Senate; serves as 2nd Chief Justice per master canonical numbering with Tenure Start=End=1795); (e) Edward Douglass White Tenure Start Year = 1910 (Chief Justice oath, NOT his Associate Justice tenure 1894 start); (f) William Howard Taft was appointed Chief Justice 1921 by Warren G. Harding (after Taft's own presidency 1909-1913 — only person to serve as President + Chief Justice). Useful reference for future temporal source_defined office-holder instances: (1) completed-tenure benchmark window scope clause (mirror NATO SG) is the right pattern for active-incumbent offices — preserves source_defined claim_type while locking universe to historically-closed tenures with stable Start/End Year values; (2) oath-of-office date convention (NOT Senate-confirmation date) is the master article's convention; pre-staging design review should check this before cycle-0 commits a wrong year; (3) source_count=1 is correct when single master article tabulates all cells uniformly — per-Justice articles add no new universe or value-role anchors (cycle-1 audit's required staging edit pattern); (4) crisis-light Law family chip from 1→2 was achievable with this office-holder instance — Law/Judiciary subdomain reused (NOT NEW); Health + Food remain at 1 instance as crisis-light. 2-cycle audit history total: 1 pre-staging design review + 1 post-staging cycle.
- `uiaa_recognized_eight_thousanders` (comparative, **Geography/Mountaineering existing subdomain** — Geography family extends from 14 to 15 instances; 14 rows × 5 cols; single PK = Mountain; Tier B manual_wikipedia, source_count=1: single master Wikipedia `Eight-thousander` article carrying roles=[universe, ranking, value] for all 5 cells; per-peak Wikipedia articles + UIAA upstream consulted as validation cross-checks, NOT registered) **STAGED 2026-05-18 → Ready 2026-05-18 after 1 round pre-staging design review (cycle-1, 5 required amendments + 12 per-question recommendations) + 2-cycle post-staging arc terminating at cycle-2 0/0/0 Ready+Clean**. **MILESTONE: SATURATES `comparative` at 5/5 as the 12th fully-cleared category** (joining factual_lookup, top_k, multi_hop, qualitative, multi_attribute_projection, set_operation, set_filter, causal, implicit_join, scaled_enumeration, hierarchical_decomposition). physical-features/mountains fine-class extends from 1 to 2 instances (joining `top_5_highest_mountains_per_continent`); bucket physical-features stays at 8 fine-classes from before Global200TMBF added ecoregions, then 9 after. Schema: Mountain PK + Country(ies) + Elevation (m) + Prominence (m) + First Ascent Year. Universe rule: 14 UIAA-recognized independent eight-thousanders per master's verbatim scope clause — mountains with summit elevation > 8000m AND topographic prominence ≥ 600m (two-part deterministic UIAA threshold); subsidiary peaks (Lhotse Shar 8,382m, Yalung Kang 8,505m, Lhotse Middle 8,410m, Kangchenjunga South 8,476m, Kangchenjunga Central 8,473m, Broad Peak Central 8,011m) explicitly excluded because they exceed 8,000m elevation but fail the 600m prominence/independence criterion. claim_type=source_defined; operation_type=filter. Closed-canonical 14-row universe with wide internal range (Everest 8849m down to Shishapangma 8027m) and boundary case Lhotse at exactly 610m prominence (just 10m above UIAA threshold). Notable: (a) **Schema = Prominence (m) NOT Range** per audit-cycle-1 amendment #1 — Prominence is master-native AND is the second-half of the inclusion criterion; Range (Himalayan vs Karakoram) would require per-peak source registration pushing source_count > 1; Range attribution (10 Himalayan / 4 Karakoram — corrected from cycle-0's wrong 9/5) kept as validation-only context; (b) **Country renamed to Country(ies) + semicolon-delim normalization** per audit-cycle-1 amendment #2 ('Nepal; China' not master-verbatim 'Nepal / China' slash; matches films multi-director cell precedent); (c) Cycle-0 had two factual errors caught by audit before staging — master IS elevation-DESC sorted (not first-ascent chronological); range split is 10/4 (not 9/5 — Nanga Parbat is Himalayan, not Karakoram); (d) Sort = Elevation (m) DESC = master-native (cycle-1 audit confirmed); (e) Mountain names verbatim including Roman numerals (Dhaulagiri I, Annapurna I, Gasherbrum I, Gasherbrum II); (f) Title/scope explicitly says 'UIAA-recognized independent eight-thousanders, excluding subsidiary peaks' per amendment #5 — pre-warns readers analogous to asteroids' Ceres-inclusion pattern; (g) Everest's prominence = elevation (8849 = 8849) because Everest is global topographic reference; only special case; (h) cycle-1 post-staging Ready-with-2-HYGIENE on prose (Country count inconsistency: 8/14 vs 9/14 with self-correcting 'wait, that's 9'; subsidiary peak height drift: Lhotse Shar 8,383 → 8,382 + Kangchenjunga South 8,494 → 8,476 per master's proposed-expansion table); cycle-2 prose-only fixes terminal Ready+Clean. Useful reference for future source_defined comparative instances on closed-canonical lists: (1) when the universe-defining authority (UIAA, IAVCEI, IUPAC) has a two-part inclusion criterion (e.g., elevation > X AND prominence ≥ Y), stage BOTH cells as columns when both come from the master — the second criterion's source cell is load-bearing for the inclusion rule and earns its column; (2) Range attribution that requires per-entity source registration is NOT worth adding to source_count just for the column — keep as validation-only context when source_count=1 is achievable; (3) master-verbatim multi-value cell delimiter (slash with spaces 'Nepal / China') should still normalize to benchmark-consistent semicolon-delim ('Nepal; China') for cross-instance vocabulary consistency; (4) cycle-0 verbatim-row-count claims should be audit-double-checked — both my range-split and sort-order claims were wrong before audit caught them. 3-cycle audit history total: 1 pre-staging design review + 2 post-staging cycles.
- `global_200_tropical_moist_broadleaf_forest_ecoregions_by_realm` (geographic, **Environment/Conservation NEW subdomain** — Environment family extends from 1 to 2 instances [crisis-light family chip 1→2; first instance was Environment/Energy `countries_over_50pct_renewable_electricity`]; 51 rows × 4 cols; single PK = Global 200 Ecoregion; Tier B manual_wikipedia, source_count=1: single master Wikipedia `Global 200` article carrying roles=[universe, value] for all 4 cells; NO validation cross-checks registered or documented — master tabulation supplies 100% of cell-value grounding per cycle-3 audit confirmation; cleanest source plan in pkdb_bench) **STAGED 2026-05-18 → Ready 2026-05-18 after 3 rounds pre-staging design review + 1-cycle post-staging arc terminating at cycle-1 0/0/0 Ready+Clean — matches `phanerozoic_periods_by_start_age` fastest-convergence precedent**. **MILESTONE: Closes the LAST remaining gap-2 category — `geographic` from gap 2 (3 of 5) to gap 1 (4 of 5); benchmark now uniform-gap-1-or-better across all 15 categories.** NEW `physical-features/ecoregions` fine-class extending physical-features bucket from 8 to 9 instances. Schema: Global 200 Ecoregion PK + Realm (closed 5-value: Afrotropical / Australasia / Indomalaya / Neotropic / Oceania) + Representative WWF Ecoregions (semicolon-delim child labels) + WWF Ecoregion Codes (semicolon-delim child codes). Universe rule: 51 WWF Global 200 priority ecoregions of the 'Tropical and Subtropical Moist Broadleaf Forests' (TMBF) major habitat type per Wikipedia's `Global 200` master article — closed canonical curated WWF list. claim_type=source_defined; operation_type=filter; secondary_category=set_filter. Notable: (a) **Replaces abandoned `major_tropical_rainforests_by_continent` design** after 2 rounds of pre-staging on that design exposed structural universe-fuzziness (no closed canonical 'major rainforests' authoritative list); pivot to Global 200 TMBF subset locked source_defined cleanly per audit recommendation — same precedent path as decade_volcanoes_by_continent (which replaced an abandoned vaccines design); (b) Realm vocabulary 5-value with 'Afrotropical' spelling (NOT 4-value 'Afrotropic' as my cycle-1 design — cycle-2 audit caught missed Oceania + spelling); (c) Per-realm distribution Afrotropical 11 / Australasia 8 / Indomalaya 18 / Neotropic 12 / Oceania 2 reflects actual geographic concentration of tropical moist broadleaf forests; (d) **Cycle-3 audit's master-derived row-list dump used verbatim at cycle-0 staging** — all 51 rows × 4 cells = 204 source-extractive values committed master-verbatim with NO best-guess values, enabling single-cycle Ready+Clean convergence (avoiding decade_volcanoes-style cycle-1 9-cell-mismatch catch); (e) source_count=1 with master-only validation — cleanest source plan in pkdb_bench (no per-entity validation prose needed). Useful reference for future source-defined geographic/conservation instances: (1) pivot fuzzy 'major X' framings to closed canonical authoritative lists (WWF Global 200 / IAVCEI / ICS / IUPAC / Conservation International) BEFORE cycle-0 staging — saves 2+ cycles of universe-fuzziness audit churn; (2) when master has parent-child hierarchical structure, pick single granularity for PK + semicolon-delim multi-value cells for the other granularity; (3) ask audit to dump master-derived row list in pre-staging cycle 3+ before cycle-0 staging to enable master-verbatim commit; (4) NEW Environment/Conservation subdomain fills one of 4 crisis-light family slots (Health, Law, Food still at 1). 4-cycle audit history total: 3 pre-staging design reviews + 1 post-staging cycle.
- `top_3_best_selling_titles_per_top_5_lifetime_consoles` (hierarchical_decomposition, **Culture/Gaming existing subdomain — 2nd video-games-keyed instance** joining `trial_union_5`; 15 rows × 4 cols; composite PK (Console, Title); Tier B manual_wikipedia, source_count=6: 1 master `List of best-selling game consoles` Wikipedia article [universe role only — defines the 5-console partition universe via DESC ranking by lifetime units sold] + 5 dedicated per-console best-seller list pages [universe + ranking + value roles for each partition: List of best-selling PlayStation 2 / Nintendo Switch / Nintendo DS / Game Boy / PlayStation 4 video games]; per-title Wikipedia articles + external sales databases consulted as validation cross-checks, NOT registered) **STAGED 2026-05-18 → Ready 2026-05-18 after 1 round pre-staging design review (cycle-1, 6 required amendments + 12 per-question recommendations) + 2-cycle post-staging arc terminating at cycle-2 0/0/0 Ready+Clean**. **MILESTONE: SATURATES `hierarchical_decomposition` at 5/5 as the 11th fully-cleared category** (joining factual_lookup, top_k, multi_hop, qualitative, multi_attribute_projection, set_operation, set_filter, causal, implicit_join, scaled_enumeration). artifacts/video-games fine-class extends from 1 to 2 instances; bucket artifacts stays at current size (NO new fine-class added). Schema: Console (composite PK 1) + Title (composite PK 2) + Units Sold (millions) + Release Year. Universe rule: top 5 consoles by lifetime units sold per master ranking (PS2 160M / Switch 155.92M / DS 154.02M / Game Boy & Game Boy Color 118.69M / PS4 117.2M; rank-5/rank-6 boundary 117.2M vs 102.49M PS1 is wide) × top 3 titles per dedicated per-console best-seller list = 15 rows. claim_type=source_defined; operation_type=projection. PIVOT from `top_5_oldest_universities_per_continent` candidate after that cycle-0 found the universities master article truncated under WebFetch tool limits (half the per-continent sub-sections unreachable) — video-games pivot uses bounded per-partition pages which all 5 fetched reliably. Notable: (a) **N=5 over N=6** per audit-cycle-1 amendment #5 — the boundary between rank-5 PS4 (117.2M) and rank-6 PlayStation [PS1] (102.49M) is wide (14.71M); N=6 would have narrowed to the rank-6 PS1 vs rank-7 Wii (102.49 vs 101.63) gap which is only 0.86M; (b) **Domain = Culture/Gaming existing** (NOT Technology/Gaming NEW) per audit-cycle-1 amendment #1 — repo classifies the existing video-games instance under Culture/Gaming per `domain_taxonomy.md`; reuses existing subdomain; (c) source_count=6 per audit-cycle-1 amendment #2 — same source-pattern as zodiac-stars (master defines partition universe; per-partition pages supply universe+ranking+value for their partition); (d) **Schema dropped Publisher** per audit-cycle-1 amendment #3 — PS2 + PS1 pages have region-specific publisher cells (e.g., 'Rockstar Games (WW); Capcom (JP)' for GTA: SA); cleaner 4-col first-pass schema avoids multi-publisher normalization complexity; (e) **Title (not Game) wording in ID + columns** per audit-cycle-1 amendment #4 — Game Boy & Game Boy Color partition includes grouped-generation entries (Pokémon Generation I = Red+Blue+Yellow; Pokémon Generation II = Gold+Silver+Crystal) per source's row aggregation; 'Title' covers these honestly; (f) **Game Boy + Game Boy Color combined as single partition** per master + per-partition page convention; (g) Units Sold normalized to numeric millions from heterogeneous source forms ('71.08 million' / '45,900,000' / '35,000,000'); (h) Year-anchor heterogeneous per-partition (Switch as of March 31 2026, DS as of March 31 2025, others varying) — NOT collapsed into a single global as-of; (i) Sort = master-console-ranking-ASC partition order (PS2 → Switch → DS → Game Boy & Color → PS4); within-partition Units Sold (millions) DESC; Release Year ASC tiebreak (not triggered at staged 15-row values); (j) cycle-1 post-staging BLOCKING was Nintendogs Title cell — master has 'Nintendogs (all versions)' verbatim aggregating 4 sub-titles, not bare 'Nintendogs'; fixed in cycle-2 + 2 HYGIENE (Tetris source-text precision: table-cell 35,000,000 vs intro-prose 'over 35 million' distinction; PS2 4th-row stale note Final Fantasy X → Gran Turismo 4 at 11.76M). Useful reference for future hierarchical_decomposition source_defined instances using multi-page per-partition sources: (1) **bounded per-partition pages beat single mega-master articles** for tool-friendliness — the universities pivot was forced because the universities master was too large for WebFetch; video-games per-console pages were uniformly bounded and fetched reliably across all 5 partitions; (2) source_count > 1 with each source carrying [universe, ranking, value] for its own partition + 1 master for partition-scope = source_defined claim_type (zodiac-stars precedent); (3) when source-aggregated rows (Nintendogs all-versions, Pokémon Generation I/II grouped-gens) appear in top rows, embrace 'Title' over 'Game' wording — staged cells must match master row labels verbatim including aggregation qualifiers; (4) when source presents heterogeneous units (some pages use '45,900,000' integer-with-commas, others '71.08 million' decimal-text), normalization to a uniform numeric scale is documented as normalized, NOT verbatim — clarify in the cell rule that integer-divided-by-1M = millions; (5) when fuzzy intro-prose ('over 35 million' for Tetris) exists alongside precise table-cell ('35,000,000'), the value-supplying source is the table cell — make this distinction explicit in source-units-text documentation. 3-cycle audit history total: 1 pre-staging design review + 2 post-staging cycles.
- `nber_dated_us_recessions_since_1929` (temporal, **Business/Economics existing thin subdomain** — Business family extends from 2 to 3 instances; 15 rows × 5 cols; single PK = Recession; Tier B manual_wikipedia, source_count=2: NBER official business-cycle table [universe + value role for Peak Year/Trough Year/Duration Months] + Wikipedia `List of recessions in the United States` master [universe membership + value role for Recession names + Peak Unemployment Rate via Characteristics column]; 14 distinct per-recession Wikipedia URLs covering 15 row mappings — `1980 recession` and `1981–1982 recession` share the `Early_1980s_recession` article — consulted as validation cross-checks in `source_coverage_notes::validation_cross_checks` provenance prose, NOT registered) **STAGED 2026-05-18 → Ready 2026-05-18 after 2 rounds pre-staging design review (cycle-1 'Revise' + cycle-2 'Approve With 3 Required Staging Edits') + 2-cycle post-staging arc terminating at cycle-2 0/0/0 Ready+Clean**. Pushes `temporal` from gap 2 (3 of 5) to **gap 1 (4 of 5)** — one more saturates as the 11th-or-later fully-cleared category (alongside `aggregation`, `comparative`, `hierarchical_decomposition`). NEW `events/recessions` fine-class extending events bucket from 10 to 11 instances. Schema: Recession PK + Peak Year + Trough Year + Duration (Months) + Peak Unemployment Rate (%). Universe rule: 15 NBER-dated US recessions with peak month ≥ August 1929 per NBER Business Cycle Dating Committee + Wikipedia master 'Great Depression onward' section — explicit benchmark window scope (NBER's full chronology back to 1854 out of scope). claim_type=source_defined; operation_type=filter; secondary_category=null. Notable: (a) **Design pivot Cycle-1 → Cycle-2**: original sketch included a `Primary Trigger` categorical cell for causal-secondary axis (cause→recession chain), but cycle-1 audit found (i) Wikipedia master's Characteristics column has multi-causal narratives that don't cleanly map to a categorical primary trigger; (ii) after audit-corrected labels, Monetary tightening would dominate 8 of 15 rows = weakly-discriminating causal cell. **Option A taken** (drop Primary Trigger / clean source_extractive schema with Peak Unemployment Rate replacement). Trade-off: instance loses the originally-motivated causal secondary axis but gains cleaner source_defined claim_type. The lesson: the "strongest causal-flavored temporal candidate" framing was optimistic — actual master content didn't support balanced cause attribution; future causal-secondary attempts should pre-verify master's cause-column structure before committing; (b) source_count=2 (NBER + Wikipedia master) per cycle-1 audit's BLOCKING fix — NBER is upstream authority for peak/trough/duration; master supplies row labels + Peak Unemployment Rate; (c) benchmark window scope clause locked: "peak month ≥ August 1929" — aligns with master's Great Depression-onward section + provides iconic Great Depression anchor + ≥10-row gate satisfaction; (d) cycle-2 staging edits: Recession of 1937–1938 peak unemployment 17.8 (not 20.0 best-guess); master-table row labels with en-dash year-range format (Recession of 1937–1938, 1973–1975 recession, 1981–1982 recession replacing 'Early 1980s recession' which was article-style); Peak Unemployment Rate extraction rule locks 'strip % and parenthetical month/year; if range, use highest endpoint' (Great Depression cell '21.3% (1932) – 24.9% (1933)' → 24.9); (e) cycle-1 post-staging Ready-with-1-HYGIENE on prose: "15 per-recession articles" should be "14 distinct URLs (15 row mappings; 1980 + 1981–1982 share Early_1980s_recession)" — 1980 + 1981–1982 share the Wikipedia article; cycle-2 prose-only fix terminal Ready+Clean. Useful reference for future temporal source_defined instances: (1) explicit benchmark-window scope clauses (e.g., "peak ≥ August 1929") preserve source_defined claim_type while narrowing from the full upstream universe — must be documented in title + decision_log + normalization_decisions; (2) attempting to add causal-secondary axis via a curator-mapped Trigger column requires pre-checking the master's cause-column structure — if multi-causal narratives dominate or one cause-value swamps the universe, drop the column and accept temporal-only; (3) NBER + Wikipedia dual-master source plan (source_count=2) is the right pattern when an upstream authority (NBER) carries primary numeric values and the encyclopedia master (Wikipedia) carries naming + narrative attribute values; (4) shared-article entity-count gotcha: when 2+ rows share a single Wikipedia article (1980 + 1981–1982), state "N distinct URLs covering M row mappings" rather than "N per-row articles" to be precise. 4-cycle audit history total: 2 pre-staging design reviews + 2 post-staging cycles.
- `clubs_with_at_least_2_european_cup_and_uefa_champions_league_titles` (aggregation, **Sports/Football NEW subdomain** — 3rd Sports subdomain joining Sports/Olympics + Sports/Tennis; Sports family extends from 3 to 4 instances; 13 rows × 5 cols; single PK = Club; Tier B manual_wikipedia, source_count=1: single master `List of European Cup and UEFA Champions League finals` Wikipedia article carrying roles=[universe, ranking, value] — its `Performances → By club` per-club tally supplies the pre-aggregated COUNT cell + the per-row Club / Country (embedded country link in Club cell) / Titles / First Title Year / Most Recent Title Year cells; per-club Wikipedia articles serve as validation-only context, NOT registered) **STAGED 2026-05-18 → Ready 2026-05-18 after 1 round pre-staging design review (cycle-1, 4 required + 12 per-question recommendations) + 2-cycle post-staging arc terminating at cycle-2 0/0/0 Ready+Clean**. Pushes `aggregation` from gap 2 (3 of 5: us_presidents_count_by_birth_state + programming_languages_by_decade_of_first_appearance + films_winning_at_least_8_academy_awards) to **gap 1 (4 of 5)** — one more saturates as the 11th fully-cleared category (alongside `comparative` and `hierarchical_decomposition`). NEW `organizations/sports-clubs` fine-class extending under-represented organizations bucket from 4 to 5 instances (joining universities, companies, space-agencies, ports). Schema: Club PK + Country + Titles + First Title Year + Most Recent Title Year. Universe rule: 13 clubs with ≥ 2 combined European Cup era (1955-1992) + UEFA Champions League era (1992-present) titles per master's pooled-era tally as of 2024-25 season (PSG's 2025 1st title kept OUT). claim_type=source_defined. Notable: (a) ID renamed pre-staging from `uefa_champions_league_titles_per_club` to `clubs_with_at_least_2_european_cup_and_uefa_champions_league_titles` — long but threshold-explicit + scope-explicit (audit-cycle-1 amendment #1); (b) pooled European Cup + UCL convention inherited from master's own framing ('Prior to the 1992-93 season, the tournament was named the European Cup') — universal football-journalism convention (Real Madrid's '15 Champions League titles' counts 6 European Cup era wins + 9 UCL era wins); (c) Year cells (First / Most Recent Title Year) are min/max of the master's displayed final-year integer list (master's `Seasons won` already displays integers like 1956, 1957, ..., 2024 — NOT season-notation strings like '1955-56' — so no curator season-notation parsing required) per audit-cycle-1 amendment #2; (d) Country sourcing master-derived from each Club cell's embedded country flag-icon + linked country text (master does NOT expose a standalone Country header) — single-source extraction documented as normalized field per audit-cycle-1 amendment #3; source_count=1 preserved; (e) ≥ 2 threshold chosen over alternatives: ≥ 1 (24 rows, broad) / ≥ 3 (8 rows, below gate) / ≥ 5 (5 rows, below gate); (f) sort contract = Titles DESC primary + First Title Year ASC secondary tiebreak at 6-title (Bayern 1974 < Liverpool 1977), 3-title (Inter 1964 < Man Utd 1968), and 5-way 2-title (Benfica 1961 < Forest 1979 < Juventus 1985 < Porto 1987 < Chelsea 2012) ties; First-Title-Year ASC documented as curator sort normalization (not master-native); (g) Club name verbatim from master ('Milan' not 'AC Milan'; 'Bayern Munich' not 'FC Bayern München'; 'Inter Milan' not 'Internazionale'; 'Manchester United' not 'Manchester United F.C.'); (h) Country verbatim from master with constituent-country forms for British clubs ('England' for Liverpool/Man Utd/Forest/Chelsea, NOT 'United Kingdom'); (i) cycle-1 post-staging Ready-with-1-HYGIENE (`N to N+1` placeholder in `_extraction_notes` for Sports family count); cycle-2 0/0/0 terminal. Useful reference for future source_defined aggregation instances: (1) pre-aggregated COUNT cells from a single master are the cleanest aggregation pattern — same precedent as `films_winning_at_least_8_academy_awards` and `us_presidents_count_by_birth_state`; (2) when source carries pooled-era / pooled-scope convention (European Cup + UCL combined), inherit from master verbatim rather than curator-narrowing — narrowing would weaken source_defined claim_type; (3) embedded-link extractions (Country from flag-icon + linked country text in Club cell) preserve source_count=1 when the link is part of the row's source content — document as normalized field; (4) threshold-explicit + scope-explicit IDs make load-bearing facts visible in the artifact name (longer but more honest than non-parametric "_per_X" naming). 3-cycle audit history total: 1 pre-staging design review + 2 post-staging cycles.
- `phanerozoic_periods_by_start_age` (temporal, **Science/Geology NEW subdomain** — Science family extends from 14 to 15 instances; 12 rows × 4 cols; single PK = Period; Tier B manual_wikipedia, source_count=1: single master Wikipedia `Geologic time scale` article carrying roles=[universe, value] with explicit conformance to ICS International Chronostratigraphic Chart v2024/12; 12 per-period Wikipedia articles + ICS chart consulted as validation cross-checks in `source_coverage_notes::validation_cross_checks` provenance prose, NOT registered) **STAGED 2026-05-18 → Ready 2026-05-18 after 1 round pre-staging design review (cycle-1, 1 required staging edit: ICS v2024/12 boundary value corrections) + 1-cycle post-staging arc terminating at cycle-1 0/0/0 Ready+Clean — fastest convergence in pkdb_bench (matches `twelve_olympians_greek_to_roman` precedent)**. **MILESTONE: Closes the LAST remaining gap-3 category — `temporal` from gap 3 (2 of 5) to gap 2 (3 of 5); benchmark now uniform-gap-2-or-better across all 15 categories.** NEW `time-periods/geological-periods` fine-class extending the MOST-UNDER-REPRESENTED entity bucket `time-periods/*` from 1 to 2 instances (joins `decades` from `programming_languages_by_decade_of_first_appearance`). Schema: Period PK + Era (closed 3-value categorical: Paleozoic / Mesozoic / Cenozoic) + Start Age (Ma) + End Age (Ma). Universe rule: 12 geological periods of the Phanerozoic Eon per ICS International Chronostratigraphic Chart v2024/12 as enumerated in Wikipedia's `Geologic time scale` master article — closed canonical list stable since IUGS 2009 ratification of Paleogene/Neogene/Quaternary. claim_type=source_defined; operation_type=filter; secondary_category=set_filter. Notable: (a) **Granularity redirection** — picker T1 sketch said "Geological epochs of the Phanerozoic eon" but at Phanerozoic-Eon scope, epochs (~30) are unstable (Paleozoic subdivisions Terreneuvian / Series 2 / Miaolingian / Furongian recently reformed); Cenozoic-only epochs (7) below 10-row gate; eras (3) far below gate; **periods (12)** is the clean canonical middle granularity matching periodic-table-groups + decade-volcanoes closed-canonical-list pattern — confirmed by cycle-1 audit; (b) ICS v2024/12 boundary values applied per cycle-1 audit's required staging edit — 7 boundary corrections from my preview's older values (Ordovician end 486.85 not 485.4; Silurian end 443.1 not 443.8; Devonian end 419.62 not 419.2; Carboniferous end 358.86 not 358.9; Permian-Triassic 251.902 not 251.9; Jurassic-Cretaceous 143.1 not 145.0; Paleogene-Neogene 23.04 not 23.0); (c) **ICS-standard locked** — Carboniferous as 1 period (NOT Mississippian + Pennsylvanian per US Geological Survey convention); Quaternary as separate period (NOT subset of Neogene, ratified IUGS 2009); Tertiary deprecated NOT in universe (replaced by Paleogene + Neogene); (d) Start Age + End Age both kept (redundant — End Age = next period's Start Age chained, with Quaternary End Age = 0 representing present day — but exact-match-friendly per cycle-1 audit confirmation); (e) ± uncertainty bands stripped per mirror-precedent asteroids instance (Cambrian 538.8 ± 0.2 → CSV cell 538.8 central value only); (f) Era distribution Paleozoic 6 / Mesozoic 3 / Cenozoic 3 = 12 rows. Useful reference for future temporal closed-canonical-universe instances: (1) when picker sketch suggests a granularity ("epochs") that yields an unstable or below-gate universe, redirect to the canonical-adjacent granularity ("periods") that gives a clean 10+-row closed-list universe; (2) ICS v2024/12 (or other most-recent published versioned authority) values must be used per the master article's current state — historical-version values (v2012 etc.) are stale and audit-fragile; (3) chained-boundary cells (each row's End Age = next row's Start Age) are mathematically derivable but exact-match-friendly when staged explicitly — keep them for benchmark evaluation. 2-cycle audit history total: 1 pre-staging design review + 1 post-staging cycle.
- `decade_volcanoes_by_continent` (geographic, **Geography/Volcanology NEW subdomain** — Geography family extends from 13 to 14 instances; 16 rows × 5 cols; single PK = Volcano; Tier B manual_wikipedia, source_count=1: single master Wikipedia `Decade Volcanoes` article carrying roles=[universe, value]; 16 per-volcano Wikipedia articles consulted as validation cross-checks in `source_coverage_notes::validation_cross_checks` provenance prose, NOT registered) **STAGED 2026-05-17 → Ready 2026-05-18 after 2 rounds pre-staging design review (cycle-1 + cycle-2 amendments) + 2-cycle post-staging arc terminating at cycle-2 0/0/0 Ready+Clean**. Pushes `geographic` from gap 3 (2 of 5) to **gap 2 (3 of 5)**. NEW `physical-features/volcanoes` fine-class extending physical-features bucket from 7 to 8 instances (joining mountains, rivers×2, deserts, islands, oceanic-trenches, lakes). Schema: Volcano PK + Sovereign-State Continent (closed 6-value) + Country + Highest Elevation Meters + Last Known Eruption Start Year. Universe rule: 16 volcanoes designated by IAVCEI as "Decade Volcanoes" under the UN International Decade for Natural Disaster Reduction (1990s) per Wikipedia's `Decade Volcanoes` master article — closed canonical list stable since 1991. claim_type=source_defined; operation_type=filter; secondary_category=set_filter. Notable: (a) instance replaces an abandoned `vaccine_technology_classes_*` design after 5 rounds of pre-staging design review on that instance exposed structural universe instability under any historical-first-use rule — lesson applied here: choose closed canonical source-defined universe (Decade Volcanoes is exactly such — 16 fixed since 1991); (b) Avachinsky-Koryaksky paired-volcano twin entry handled as single row with audit-locked scalar rules (Highest Elevation = 3456m higher of paired; Last Known Eruption Start Year = 2009 later of paired); (c) `Sovereign-State Continent` column name (renamed from bare `Continent`) with transcontinental-edge-case clause: "sovereign-state political continent for overseas island cases; for transcontinental sovereign states, use listed volcano region's continent" — makes Teide=Europe (Spain), Mauna Loa=North America (USA), Avachinsky-Koryaksky=Asia (Kamchatka is Asian Russia) defensible; (d) `Last Known Eruption Start Year` column name (renamed from `Most Recent Known Eruption Year`) honestly reflects that for ongoing eruptions like Etna 2013-present, the extracted integer is the START year, not literally the most recent eruption year; (e) cycle-1 post-staging BLOCKING was 9 cell-value mismatches across 8 rows where cycle-0 staging used best-guess values rather than master-table-verbatim — corrected in cycle-2 hygiene repair (Merapi 2010→2024; Unzen 1500→1486m; Etna 3329→3326m; Teide 3718→3715m; Santa María 1902→2013; Volcán de Colima 3850→3820m AND 2017→2019; Ulawun 2019→2023; Galeras 2010→2014); cycle-2 prose sweep covered Santa María 1902 rationale across 3 fields. Useful reference for future source_count=1 source-defined instances: (1) when a single master article carries both universe and value roles, staged CSV cells MUST be master-table-verbatim — best-guess values from training knowledge or domain expertise must be verified against the live master before cycle-0 commit, otherwise cycle-1 audit will surface cell-value mismatches as BLOCKING factual; (2) closed-canonical-list universes (Decade Volcanoes, twelve Olympian deities, IUPAC periodic-table groups) are structurally safer than retro-discovery-prone "first-N-by-historical-year" universes — the periodic_table_groups precedent saturated cleanly in 2 pre-staging rounds while the vaccines design abandoned at 5 rounds; (3) paired-entity scalar reductions (Avachinsky-Koryaksky higher/later rule; Santa María/Santiaguito single-row rule) should be locked in `normalization_decisions` with explicit examples at cycle-0 staging time. 4-cycle audit history total: 2 pre-staging design reviews + 2 post-staging cycles.
- `largest_asteroids_with_geometric_mean_diameter_above_250_km` (comparative, **Science/Astronomy existing subdomain — 4th Astronomy instance** joining `planets_named_moons` + `apollo_lunar_landing_mission_crews` + `three_brightest_stars_in_each_zodiac_constellation`; Science family extends from 14 to 15 instances; 15 rows × 4 cols; single PK = Asteroid (Number+Name); Tier B manual_wikipedia, source_count=1: single master `List of exceptional asteroids` Wikipedia article carrying roles=[universe, ranking, value]; per-asteroid Wikipedia articles serve as validation-only context, NOT registered) **STAGED 2026-05-17 → Ready 2026-05-17 after 1 round pre-staging design review (cycle-1, 3 required + 1 optional amendments) + 3-cycle post-staging arc terminating at cycle-3 0/0/0 Ready+Clean**. Pushes `comparative` from gap 3 (2 of 5) to **gap 2 (3 of 5)**. NEW `taxonomic-entities/asteroids` fine-class extending under-represented taxonomic-entities bucket from 4 to 5 instances (joining moons, species, nutrients, stars — the bucket just-extended in the prior cycle by zodiac-stars). Schema: Asteroid PK + Geometric Mean Diameter (km) + Discovery Year + Discoverer. Universe rule: 15 asteroids whose master-cited geometric mean diameter CENTRAL VALUE is ≥ 250 km per the `List of exceptional asteroids` master scope clause (minor planets out to Neptune, includes the dwarf planet Ceres + Jupiter trojans + centaurs, excludes TNOs). claim_type=source_defined. Notable: (a) cycle-0 source exploration discovered the user-suggested ≥300 km threshold yields only 6 rows (Ceres + Vesta + Pallas + Hygiea + Interamnia + Europa) failing the ≥10-row viability gate; threshold adjusted to ≥250 km yielding 15 rows with clean boundary (next row Bamberga 227 km is comfortably below); (b) pre-staging audit-cycle-1 amendment caught a false-certainty wording — the cycle-0 wording claimed all 15 rows are 'unambiguously above 250 km across uncertainty bands', but Hektor 256±12 and Camilla 254±12 actually have lower bounds below 250; the honest rule is defined on the central value alone (yields same 15 rows, removes false certainty); (c) Ceres inclusion made explicit in title ('including the dwarf planet Ceres per Wikipedia\'s `List of exceptional asteroids` scope definition') to pre-warn readers expecting strict post-IAU asteroid-vs-dwarf-planet usage; (d) ± uncertainty bands stripped from numeric staged cell (central value only) — full uncertainty annotations documented in normalization_decisions; (e) sort contract = Diameter DESC primary + minor-planet-number ASC tiebreak at the 3-way 254 km tie (Juno=3, Camilla=107, Patientia=451) — tiebreak is curator sort normalization, documented as such (master's native order at the tie is Juno, Patientia, Camilla); (f) Asteroid PK uses verbatim 'Number Name' format ('1 Ceres', '704 Interamnia', '451 Patientia') per master table; (g) Discoverer cell verbatim 'Last, F.' format ('Piazzi, G.', 'Olbers, H. W.' appearing twice for Vesta+Pallas, 'de Gasparis, A.' with lowercase 'de'); (h) Discovery Year is a mechanical integer-year extraction of the master's full 'Date discovered' column (NOT a verbatim cell copy — other 3 cells are verbatim); (i) optional refinement applied: column label 'Geometric Mean Diameter (km)' is a normalized rephrasing of the master's full header 'Diameter (km) (geometric mean)' (NOT a verbatim header match — cycle-2 hygiene fix corrected an earlier overstatement); (j) cycle-1 post-staging Ready-with-3-HYGIENE (Patientia 254±3 was wrongly included in the dips-below-250 set; column-label match-vs-normalized phrasing; Discovery-Year verbatim-vs-extraction wording); cycle-2 applied the 3 fixes but missed `collection_summary::approach` location; cycle-3 cleaned that residual + verified 0/0/0 terminal. Useful reference for future source_defined comparative instances: (1) deterministic numeric threshold on a master-table's central value (NOT on uncertainty-band-spanning ranges) is the cleanest source_defined threshold rule — defines the universe on the same value the row-rank uses; (2) source-scope-clause inclusions/exclusions (Ceres in / TNOs out) should be quoted verbatim from the master article and pre-warned in the title when they conflict with reader expectations; (3) the deterministic-universe-rule preference (SQL-WHERE-clause = `geometric_mean_diameter_central_value >= 250`) cleanly saturates source_defined claim_type — same precedent as `transboundary_rivers_by_continent_over_1000_km` (length threshold) + `films_winning_at_least_8_academy_awards` (Oscar count threshold) + `countries_with_more_than_5_fields_medalists` (medal count threshold); (4) when ± uncertainty bands exist in source, define the threshold rule on the central value alone — claims about all-rows-passing-at-lower-bound are easy to overstate and audit-fragile. 4-cycle audit history total: 1 pre-staging design review + 3 post-staging cycles.
- `three_brightest_stars_in_each_zodiac_constellation` (hierarchical_decomposition, **Science/Astronomy existing subdomain — 3rd Astronomy instance** joining `planets_named_moons` + `apollo_lunar_landing_mission_crews`; Science family extends from 13 to 14 instances; 36 rows × 4 cols; composite PK (Constellation, Star); Tier B manual_wikipedia, source_count=13: 1 master `Zodiac` article [universe] + 12 dedicated `List of stars in <Constellation>` ranked-list pages [universe, ranking, value]; per-constellation prose pages + per-star pages serve as validation-only context, NOT registered) **STAGED 2026-05-17 → Ready 2026-05-17 after 1 round pre-staging design review (cycle-1, 4 amendments) + 2-cycle post-staging arc terminating at cycle-2 0/0/0 Ready+Clean**. Pushes `hierarchical_decomposition` from gap 2 (3 of 5) to **gap 1 (4 of 5)** — one more saturates as the 10th category. NEW `taxonomic-entities/stars` fine-class extending under-represented taxonomic-entities bucket from 3 to 4 instances (joining moons, species, nutrients). Schema: Constellation (composite PK 1) + Star (composite PK 2) + Apparent Magnitude (ranking attribute) + Spectral Class. Universe rule: 12 zodiac-namesake constellations (per Zodiac master) × 3 lowest-apparent-magnitude stars per constellation (per dedicated `List of stars in <Constellation>` ranked-list article) = 36 rows. claim_type=source_defined. Notable: (a) pre-staging design audit redirected row-defining source family from per-constellation prose pages to dedicated `List of stars in <Constellation>` ranked-list pages — this fixed cycle-0 factual errors (Aries 3rd was Mesarthim, correct is 41 Ari; Pisces 3rd was Alrescha, correct is ω Psc); (b) Ophiuchus explicitly out of scope (Zodiac article notes ecliptic intersects 13 constellations including Ophiuchus, but only 12 are zodiac-sign-namesake — documented in decision_log); (c) Constellation cell uses astronomical Latin forms (Scorpius, Capricornus) NOT zodiac-sign forms (Scorpio, Capricorn) which appear in the Zodiac article's sign-name column; normalization documented; (d) Star cell = list-article Name column VERBATIM — deterministic mix of proper names (Hamal, Aldebaran, Pollux, Regulus, Spica, Antares A, Denebola, Castor A, Alcyone A) and Bayer designations (β Ari, 41 Ari, γ Gem, β Cnc, γ1 Leo, σ Lib, λ Sco, ε Sgr, δ Cap, β1 Cap, β Aqr, η Psc, etc.) — source-faithful, audit-friendly; (e) Spectral class preserved verbatim (e.g., 'M1.5Iab-b' for Antares A, 'A5mF2 (IV)' for δ Cap, 'M3/M4III' for σ Lib, 'B2IV+DA7.9' for λ Sco, 'A5V...' for β Ari with trailing dots); (f) sort contract = zodiac-position ASC primary (Aries=1 through Pisces=12, NOT lexicographic) + Apparent Magnitude ASC secondary; db_analog uses z.zodiac_position numeric column; (g) cycle-1 post-staging BLOCKING was β Ari spectral class staged as 'A5V' but source has 'A5V...' (verbatim drift); fixed in cycle-1 repair. Useful reference for future source_defined hierarchical_decomposition instances: (1) the source-family choice — between domain-prose pages vs dedicated ranked-list pages — is the highest-leverage pre-staging design decision; the dedicated ranked-list pages expose Name + Magnitude + Spectral Class verbatim in correct order, while prose pages require curator-rebuilt enumeration that often differs from the canonical ranking; (2) verbatim-Name-column-from-source-table beats curator-judgment proper-name-preference — the deterministic mix (Hamal + β Ari + 41 Ari) is less aesthetic but audit-friendly; (3) the deterministic-universe-rule preference saturated source_defined claim_type cleanly because both upstream anchors (Zodiac master for universe; list pages for ranking + values) are source-defined with no curator judgment in between. 3-cycle audit history total: 1 pre-staging design review + 2 post-staging cycles.
- `periodic_table_groups_first_three_elements_by_atomic_number` (hierarchical_decomposition, **Science/Chemistry existing subdomain** — Science family extends from 12 to 13 instances; Science/Chemistry subdomain extends from 1 (`chemical_elements_discovered_1869_1945`, factual_lookup) to 2 instances; 54 rows × 4 cols; composite PK (Group, Element); Tier B manual_wikipedia, source_count=2: 2 registered row-defining masters per canonical artifact-contract vocab — `Periodic_table` [universe + value] + `Group_(periodic_table)` [universe]; 18 per-group articles consulted as validation sources in provenance prose, NOT registered) **STAGED 2026-05-16 → Ready 2026-05-16 after 2 rounds pre-staging design review (cycle-1 + cycle-2 amendments, 8 total) + 3-cycle post-staging arc terminating at cycle_3 0/0/0 Ready+Clean**. Pushes `hierarchical_decomposition` from gap 3 (2 of 5) to **gap 2 (3 of 5)**. NEW `abstract-categories/chemical-groups` fine-class — bucket extends from 5 to 6 instances (joining religions×2, languages, philosophical-schools, architectural-styles); the audit explicitly preferred abstract-categories over taxonomic-entities for chemical groups because the PK is a conceptual IUPAC category, not an individual chemical entity. Schema: Group (composite PK 1) + Element (composite PK 2) + Symbol + Atomic Number. Universe rule: 18 IUPAC-numbered groups × 3 lowest-atomic-number elements per group = 54 rows. claim_type=source_defined. Notable: (a) instance renamed pre-staging from `representative_elements` to `first_three_elements_by_atomic_number` because "representative" implied curator judgment that the sources don't provide; rename made source_defined defensible; (b) Hydrogen INCLUDED in Group 1 (H/Li/Na, lowest 3 by atomic number) per IUPAC group-membership — the row-defining universe source is the master Periodic table (which assigns H to Group 1); the chemical-family `Alkali metal` validation article excludes H from its enumeration but is explicitly documented as non-uniform; (c) Group 3 = Sc/Y/Lu per IUPAC 2021 ruling (historical La/Ac alternative documented in decision_log as rejected); (d) f-block lanthanides + actinides explicitly out of scope (unnumbered per master); (e) sort contract = numeric-parse-from-Group-N (NOT lexicographic — 'Group 1, Group 10, Group 2' would mis-order); db_analog uses parsed-numeric ordering; (f) cycle-1 post-staging BLOCKING was a draft-vs-curation source-count contradiction (20 vs 2-row-defining); fixed by reducing source_urls to 2 + moving 18 validation URLs to source_coverage_notes provenance prose per artifact-contract.md:94; cycle-2 post-staging HYGIENE was residual custom 'row-defining' / 'supplementary cross-checks' vocab in 5 active-prose locations; fixed by normalizing to canonical universe / value / validation vocab. Useful reference for future source_defined hierarchical_decomposition instances: (1) pre-staging audit of the universe-source vs validation-source distinction is high-leverage — saved cycles after staging; (2) the artifact-contract canonical vocab (universe/ranking/value/validation/entity-detail) must be used uniformly in active prose, not just the structured roles block; (3) name renames at the pre-staging step (away from 'representative' / 'typical' / 'major' weasel-words to deterministic criteria like 'lowest-atomic-number') unlock source_defined claim_types that would otherwise overclaim. 5-cycle audit history total: 2 pre-staging design reviews + 3 post-staging cycles.
- `films_winning_at_least_8_academy_awards` (aggregation, **Culture/Film existing subdomain** — Culture/Film extends from 1 to 2 staged instances joining `top_grossing_films_worldwide_per_decade_1980s_2010s`; 15 rows × 5 cols; single PK = Film; Tier B manual_wikipedia, source_count=16: 1 master 'List of Academy Award–winning films' Wikipedia article + 15 per-film Wikipedia articles) **STAGED 2026-05-16 → Ready 2026-05-16 after 1 round pre-staging design review + 2-cycle audit arc terminating at cycle-2 0/0/0 Ready+Clean**. Pushes `aggregation` from gap 3 (2 of 5) to **gap 2 (3 of 5)**. 3rd films-keyed staged instance (counting trial_join_4 + top_grossing_films_per_decade). Schema: Film PK + Year of Release + Total Oscars Won (aggregation cell — pre-aggregated COUNT of competitive Academy Award wins per film) + Best Picture Winner (closed Yes/No vocab) + Director. Universe rule: films in the master article's Film/Year/Awards/Nominations table with Awards ≥ 8 (15 films). Distribution: 11→3 (Ben-Hur, Titanic, LOTR Return) + 10→1 (West Side Story) + 9→3 (Gigi, Last Emperor, English Patient) + 8→8 (Gone with the Wind, From Here to Eternity, On the Waterfront, My Fair Lady, Cabaret, Gandhi, Amadeus, Slumdog Millionaire). claim_type=source_defined. Notable: (a) ≥8 threshold chosen over ≥7 (28 rows, more boundary churn) and ≥9 (7 rows below gate) per pre-staging audit; (b) Cabaret 1972 is the only non-Best-Picture row in the universe (won 8 Oscars but lost Best Picture to The Godfather) — strongest LLM-calibration row; (c) EEAAO 2022 excluded (7 Oscars, below ≥8); (d) semicolon-delim multi-director cell ('Robert Wise; Jerome Robbins' for West Side Story); (e) operation_type='aggregation' (aggregation-category-with-aggregation-operation pattern; pre-aggregated COUNT-GROUP-BY-Film cell from source — mirrors us_presidents_count_by_birth_state precedent); (f) diacritic preserved (Miloš Forman). Cycle arc: pre-staging review resolved 5 cleanups (threshold, master source, EEAAO count, multi-director format, Best Picture vocab); cycle-1 Ready-with-2-HYGIENE (stale 'wait' prose fragments + Best-Picture-styling vs column framing); cycle-2 0/0/0 terminal. Short 2-cycle audit arc matches NATO SG precedent.
- `nato_secretaries_general_birth_country_terms` (implicit_join, **Politics/International Relations — 2nd Politics/IR instance** joining `nato_accessions_1999_2024_capitals`; 13 rows × 4 cols; single PK = Secretary-General; Tier B manual_wikipedia, source_count=14: 1 master 'Secretary General of NATO' Wikipedia article + 13 per-person Wikipedia articles) **STAGED 2026-05-16 → Ready 2026-05-16 after 1 round pre-staging design review + 2-cycle audit arc terminating at cycle-2 0/0/0 Ready+Clean**. Pushes `implicit_join` from 8/8 (just-saturated by `sovereign_state_capitals_on_rivers`) to **9/8 (1 over target)**. NEW `people/diplomats` fine-class. Schema: Secretary-General PK + Term Start Year + Term End Year + Birth Country (implicit-join cell). Universe: 13 numbered permanent NATO Secretaries General with completed terms 1952-2024 (Ismay through Stoltenberg). Mechanical 2-clause filter excludes incumbent Rutte (Term End Year IS NOT NULL) and 3 Acting SG rows (Balanzino x2 + Minuto-Rizzo x1, all marked with '–' in master table). claim_type=source_defined. Notable: (a) name normalization uses per-person Wikipedia article titles with full peerage forms; (b) modern-sovereign-country birthplace rule with Ismay=India (born British India 1887, NOT 'United Kingdom' career country); (c) Birth Country distinct from master's 'Country of origin' to make the implicit join non-redundant; (d) operation_type='join'. Cycle arc: pre-staging review resolved 2 cleanups; cycle-1 Ready-with-1-HYGIENE (3 prose-date-accuracy drifts); cycle-2 0/0/0 terminal. Short 2-cycle audit arc compared to Booker (4 cycles) / Pulitzer (5 cycles) literary-award precedents.
- `sovereign_state_capitals_on_rivers` (implicit_join,
  **Geography/Cities existing subdomain** — Geography family
  extends from 12 to 13 instances; 56 rows × 4 cols; single
  PK = Capital; Tier B manual_wikipedia, source_count=57: 1
  master `List of national capitals` + 56 per-capital
  standalone English Wikipedia articles) **STAGED 2026-05-15
  → Ready 2026-05-16 after the longest audit arc in
  pkdb_bench: 9 cycles terminating at cycle_9 0/0/0
  Ready+Clean**. **SATURATES `implicit_join` at 8/8 — 9TH
  CATEGORY TO FULLY CLEAR** (joining factual_lookup 6/5,
  top_k 6/5, multi_hop 5/5, qualitative 5/5,
  multi_attribute_projection 8/8, set_operation 8/8,
  set_filter 5/5, causal 5/5). NEW
  `cities-and-capitals/national-capitals` fine-class —
  bucket extends from 1 to 2 instances; the strongest
  pre-staging entity-novelty signal in the corpus (the
  cities-and-capitals bucket was at 1 instance only). Schema:
  Capital PK + Country + River + Continent (closed 6-value).
  Universe rule: every national capital city of a UN-member +
  observer-state with its own standalone English Wikipedia
  article, filtered by the strict admission rule that each
  capital's own per-capital article foregrounds a single
  primary perennial river in the lede or infobox. claim_type
  = `candidate_rebuild` (5th instance of this claim type in
  pkdb_bench, joining philosophical-schools, religious-
  traditions, western-philosophers, architectural-styles).
  Continent distribution: Africa 9, Asia 13, Europe 23 (41%),
  North America 6, South America 5, Oceania 0 = 56. Strict-
  gate refinements across cycles: (1) cycle-1 foregrounded-
  in-lede-or-infobox; (2) cycle-2 literal multi-river-naming
  fails (dropped Bratislava + Minsk); (3) cycle-3 perennial-
  only — wadis excluded (dropped Riyadh); (4) cycle-5
  estuary-EXCLUDE applied consistently to any 'X estuary'
  article framing (dropped Bissau + Georgetown); (5) cycle-7
  Capital-keyed framing (each capital independently
  evaluated); (6) cycle-8 standalone-EN-article source-rule
  (co-official capitals without standalone EN articles like
  Comayagüela are out of scope before the river gate is
  applied). Final exact accounting: 56 INCLUDE + 145
  per-capital-verified EXCLUDE (14 confluence + 125
  no-foregrounded-river + 5 estuary + 1 wadi-not-river) + 2
  cycle-2 strict-gate-violation drops = 203 per-capital
  admission verdicts covering the full sovereign-state +
  joint-capital + Taiwan/Kosovo universe. **Useful reference
  — longest audit arc in pkdb_bench (9 cycles)**: the strict
  admission gate accreted 6 distinct refinements across audit
  cycles (lede/infobox → no-multi-river → perennial-only →
  estuary-EXCLUDE → Capital-keyed → standalone-EN-article).
  Each refinement was audit-driven (BLOCKING or
  source-faithfulness HYGIENE), not pre-staged. For future
  Capital-keyed instances with joint-capital cases, the
  standalone-EN-article source-rule should be committed at
  Stage-1 to avoid the cycle-7 → cycle-8 add-then-revert arc.
- `pulitzer_prize_for_fiction_winners_2000_2024` (implicit_join,
  **Culture/Literature subdomain — 3rd Literature instance**
  joining `notable_works_of_recent_nobel_literature_laureates` +
  `booker_prize_for_fiction_winners_2000_2024`; Culture family
  extends to 12 instances; 24 rows × 4 cols; composite PK (Year,
  Author); Tier B manual_wikipedia, source_count=24: 1 master
  Pulitzer Prize for Fiction article + 23 per-author Wikipedia
  articles) **STAGED 2026-05-15 → Ready 2026-05-15 after 1 round
  pre-staging design review + 4-cycle audit arc terminating at
  cycle-4 0/0/0 Ready+Clean**. Brings `implicit_join` from gap 2
  to **gap 1** (7 of 8 staged) — first non-saturated category at
  gap 1 since the most-recent saturations. 2nd `people/authors`
  fine-class instance joining the parallel-Booker. Schema: Year
  (PK 1) + Author (PK 2) + Book + Author Birth State or Country
  (heterogeneous cell: U.S. state or 'Washington, D.C.' for U.S.-
  born; modern sovereign country for foreign-born). Universe: 24
  rows = 24 award years (2000-2024 minus 2012 no-award) + 1
  extra 2023 co-winner row - 1 row excluded for Paul Harding 2010
  (Tinkers) whose per-author Wikipedia article does not
  explicitly state a birth state. Pre-staging audit selected
  Variant A (heterogeneous cell) over Variant B (US-only filter)
  and Variant C (Birth Country only). Author Birth State or
  Country distribution (19 distinct values): 14 distinct U.S.
  states/DC × 19 rows (New York 3 incl. Whitehead 2x; Washington
  D.C. 3 incl. Chabon, Greer, Edward P. Jones; Illinois 2;
  remaining 13 states ×1 each) + 5 foreign countries × 5 rows
  (UK, Australia, Dominican Republic, Vietnam, Argentina).
  Notable design choices: (a) heterogeneous cell honestly named
  'Author Birth State or Country' (not 'Birthplace' which
  suggests city-level); (b) modern-sovereign-country
  normalization (Lahiri = UK not England, mirroring Booker
  precedent); (c) Washington, D.C. used as its own value
  (federal-district source-verbatim, not coerced to a state);
  (d) Hernan Diaz uses Hernan_Diaz_(writer) URL (not the
  footballer disambiguation); (e) operation_type = 'join' per
  protocol's canonical vocabulary (pre-applied lesson from
  Booker cycle-1 BLOCKING). Cycle arc: pre-staging design review
  resolved 8 cleanups BEFORE cycle-0; cycle-1 Not-Ready with 3
  BLOCKING (Edward P. Jones Virginia→DC factual error;
  Paul Harding Massachusetts not source-grounded → row dropped;
  Joshua Cohen book full title needed); cycles 2-4 prose-cleanup
  for stale 25-row references after the cycle-1 row exclusion
  (multi-cycle residual is the typical pattern for instances
  with mid-cycle row count changes). Useful reference for
  implicit_join instances with per-row source-grounding
  constraints: row exclusion + universe-rule revision retains
  claim_type=source_defined because the per-row constraint is
  mechanically verifiable (per-author article either states
  birth-state-or-country or it doesn't); contrast with curator-
  judgment universe restrictions which would require
  partially_supplemented or candidate_rebuild claim_type. Also:
  when a row exclusion changes universe size, sweep ALL prose
  locations (temporal_note, table_title, _extraction_notes,
  source_coverage_notes, normalization_decisions, decision_log
  Risks/adaptation, and any per-row enumeration lists) in a
  single pass to avoid 2-3-cycle prose-residual patterns.
- `booker_prize_for_fiction_winners_2000_2024` (implicit_join,
  **Culture/Literature subdomain — 2nd Literature instance** joining
  `notable_works_of_recent_nobel_literature_laureates`; Culture
  family extends to 11 instances; 26 rows × 4 cols; composite PK
  (Year, Author); Tier B manual_wikipedia, source_count=25: 1
  master Booker Prize Wikipedia article + 24 per-author Wikipedia
  articles) **STAGED 2026-05-15 → Ready 2026-05-15 after 1 round
  pre-staging review + 3-cycle audit arc terminating at cycle-3
  0/0/0 Ready+Clean**. Brings `implicit_join` from gap 3 to
  **gap 2** (6 of 8 staged). NEW `people/authors` fine-class
  extending the people bucket from 9 to 10 fine-classes
  (politicians, scientists, athletes, painters, philosophers,
  laureates, deities, honorees, astronauts, + NEW authors).
  Schema: Year (PK 1) + Author (PK 2) + Book + Author Birth
  Country (the implicit-join cell). Universe: 26 Booker Prize for
  Fiction winners 2000-2024 inclusive (25 award years + 1 extra
  row for 2019 co-winner Bernardine Evaristo + Margaret Atwood
  shared prize). Composite PK handles 2019 co-winners and 2-time
  winners (Hilary Mantel 2009+2012; Margaret Atwood 2000+2019).
  Author Birth Country distribution (11 distinct): United Kingdom
  9, Canada 3, Australia 3, Ireland 3, India 2, United States 2,
  Spain 1, Jamaica 1, South Africa 1, Sri Lanka 1. Implicit-join
  structure: master Booker article gives (Year, Author, Book) as
  denormalized winners table; per-author Wikipedia articles supply
  Author Birth Country with modern-sovereign-country normalization
  rule (Scotland/Northern Ireland → United Kingdom). claim_type =
  source_defined (universe fully defined by Booker winners table
  filtered to 2000-2024 inclusive; 2025 winner David Szalay/Flesh
  explicitly excluded). Notable design choices: (a) bare
  birth-country values without parentheticals per audit
  recommendation (Spain for Martel not 'Spain (Canadian citizen)';
  Canada for Catton not 'Canada (NZ-raised)'; United Kingdom for
  Burns/Stuart with constituent-country normalization
  Scotland/Northern Ireland → UK); (b) citizenship/raised-in
  caveats documented in normalization_decisions but NOT staged in
  cells; (c) deterministic (Year, Author) alphabetical-tiebreak
  sort departs from master Booker table's 2019 tie order
  (Atwood-before-Evaristo); the deterministic sort is preferred
  for exact-match benchmark reproducibility. Cycle arc:
  pre-staging audit design review (1 round resolved 4 cleanups
  BEFORE cycle-0 staging — bare birth-country values, modern-
  sovereign-country normalization rule, 2025-exclusion note,
  25+1=26 row-count note); cycle-1 Not-Ready with 1 BLOCKING +
  1 HYGIENE (operation_type 'implicit_join' should be 'join' per
  protocol's canonical operation_type vocabulary; sort prose
  mis-said 'matches master Booker tie order' when CSV's
  alphabetical tiebreak differs); cycle-2 Ready-with-1-HYGIENE
  (sort prose precision: 'Author surname' should be 'full
  displayed Author field'); cycle-3 0/0/0 terminal. Useful
  reference: pre-staging design review for implicit_join
  instances should explicitly check the operation_type vocabulary
  ('join' is the canonical value, NOT 'implicit_join' which is
  the query_category-level label).
- `major_architectural_styles_landmark_buildings` (qualitative,
  **Culture/Architecture NEW subdomain** — Culture family extends
  from 9 to 10 instances; 16 rows × 4 cols; single PK = Architectural
  Style; Tier B manual_wikipedia, source_count=34: 2 master articles
  (History of architecture + List of architectural styles) + 16
  per-style + 16 per-building Wikipedia articles) **STAGED 2026-05-15
  → Ready 2026-05-15 after extensive 2-round pre-staging design
  review + 3-cycle audit arc terminating at cycle-3 0/0/0
  Ready+Clean**. **SATURATES `qualitative` at 5/5 — EIGHTH CATEGORY
  TO FULLY CLEAR** (joining factual_lookup 6/5, top_k 6/5, multi_hop
  5/5, multi_attribute_projection 8/8, set_operation 8/8, set_filter
  5/5, causal 5/5). NEW `abstract-categories/architectural-styles`
  fine-class extending the abstract-categories bucket from 4 to 5
  fine-classes (music-genres, religions, languages, food-categories,
  + NEW architectural-styles). Schema: Architectural Style PK +
  Landmark Building + Modern-day Country + Year of Completion
  (integer, negative for BCE). Universe: 16 textbook-canon
  architectural styles with global coverage — 12 Western/Euro-
  Mediterranean (Ancient Egyptian, Greek, Roman, Byzantine,
  Romanesque, Gothic, Renaissance, Baroque, Neoclassical, Art
  Nouveau, Art Deco, Modern) + 4 non-Western (Mesoamerican,
  Japanese, Moorish, Mughal) per audit-recommended global-coverage
  expansion. claim_type=candidate_rebuild (curator-bounded
  textbook-canon slate, honestly characterized). 4 explicit
  normalization rules documented (Universe / Landmark / Year /
  Country) + per-row landmark-selection rationale for all 16 rows.
  Notable design choices: (a) Style PKs canonicalized to Wikipedia
  article titles (14 with 'architecture' suffix, 2 bare per
  Wikipedia convention: Art Nouveau, Art Deco); (b) Country renamed
  'Modern-day Country' for unambiguous semantics (Hagia Sophia =
  Turkey not Byzantine; Pantheon = Italy not Roman Empire); (c)
  Year column 3-way provenance split — 4 rows under
  circa-year/range-clause (Great Pyramid -2600, Pyramid of the
  Sun 200, Versailles 1715, Alhambra 1391), 3 rows under
  body-supported allowance (Speyer 1106, Casa Batlló 1906, Villa
  Savoye 1931), 9 rows under infobox-priority default (Parthenon
  -432, Pantheon Rome 126, Hagia Sophia 537, Hōryū-ji 607,
  Notre-Dame 1345, Florence Cathedral 1436, Taj Mahal 1653,
  Brandenburg Gate 1791, Chrysler Building 1930). Cycle arc:
  EXTENSIVE pre-staging design review (TWO rounds resolving 8
  design-level issues BEFORE cycle-0 staging — universe scope
  Western-only-to-global expansion; Year column source-stability;
  per-style source plan addition; Country renaming; canonicalize
  Style PKs; Hōryū-ji 607 semantics; Alhambra 1391 range-clause);
  cycle-1 Ready-with-1-HYGIENE (Year-source prose overstated
  infobox-Completed when 3 rows body-supported); cycle-2
  Ready-with-1-HYGIENE (count arithmetic 6 vs 8, 9 vs 8 in
  Year-normalization paragraph); cycle-3 0/0/0 terminal. Useful
  reference: extensive 2-round pre-staging audit design review
  paid off — cycle-1 audit found 0 BLOCKING (data state
  source-grounded from the start) and the 3-cycle post-staging arc
  was entirely prose-alignment (no CSV/schema/source-set changes
  across any cycle). Pattern matches religious_traditions
  precedent and confirms pre-staging design review as the
  highest-leverage intervention for candidate_rebuild qualitative
  saturators with curator-judgment-heavy universes.
- `major_religious_traditions_foundational_texts` (qualitative,
  **Religion/Texts NEW subdomain** — Religion family extends from
  2 to 3 instances (joining Religion/Holy Sites and
  Religion/Mythology); 12 rows × 4 cols; single PK = Religion;
  Tier B manual_wikipedia, source_count=15: 3 master articles
  (World religions + Major religious groups + List of religious
  texts) + 12 per-text articles) **STAGED 2026-05-14 → Ready
  2026-05-15 after extensive pre-staging design review + 3-cycle
  audit arc terminating at cycle-3 re-run 0/0/0 Ready+Clean**.
  Brings `qualitative` from gap 2 to **gap 1** (4 of 5 staged).
  2nd `abstract-categories/religions` fine-class instance (joining
  `world_religions_top5_holy_sites`; the fine-class is now no
  longer unique within the bucket — saturation watch for future
  religion-keyed candidates). Schema: Religion PK + Foundational
  Text + Scriptural Language(s) (semicolon-delimited) + Tradition
  Family (closed 4-value: Abrahamic / Dharmic / East Asian /
  Iranian). Universe: 12 textbook-canon religions (4 Abrahamic +
  4 Dharmic + 3 East Asian + 1 Iranian) — Big Five (Christianity,
  Islam, Hinduism, Buddhism, Judaism) + Wikipedia-recognized
  extensions (Bahá'í Faith, Sikhism, Zoroastrianism) + 4 textbook-
  canonical additions (Taoism, Confucianism, Shinto, Jainism).
  claim_type=candidate_rebuild — honestly characterized as
  curator-bounded textbook-canon slate rather than source-defined
  'major world religions' (the Wikipedia 'World religions' article
  explicitly says 'world religion' is a socially-constructed
  category). 3 individual-text exceptions documented per-row
  (Bahá'í Kitáb-i-Aqdas, Taoism Tao Te Ching, Shinto Kojiki) where
  a single individual text is more LLM-canonical than the broader
  corpus. Notable language-cell normalizations: (a) Sikhism uses
  compact-semantic compression 'Sant Bhasha; multiple Indo-Aryan
  languages; Persian; Arabic' for the per-text article's larger
  language inventory (Gurmukhi is the SCRIPT not a language and
  is therefore NOT in the language cell); (b) Buddhism Tripitaka
  uses 'Pali; Sanskrit; Buddhist Chinese; Tibetan' per the
  Tripitaka article's own wording (cycle-1 corrected from 'Classical
  Chinese; Classical Tibetan' which the source doesn't use); (c)
  Shinto Kojiki uses 'Classical Chinese; Old Japanese' per the
  Kojiki article (the prose is mostly Classical Chinese with
  songs in Old Japanese via man'yōgana); (d) Christianity Bible
  uses 'Biblical Hebrew; Biblical Aramaic; Koine Greek' per the
  Bible article's lead and New Testament section. Cycle arc:
  Extensive PRE-STAGING design review (audit agent feedback BEFORE
  cycle-0 staging) surfaced 4 design-level issues all resolved
  before any files were written; cycle-1 Not-Ready with 1 BLOCKING
  (Buddhism over-specified language cell) + 2 HYGIENE (Biblical_
  languages article reference not in source_urls; 8/4 → 7/5
  language count mismatch); cycle-2 Ready-with-residual-hygiene
  (temporal_note 'full canonical-language inventory' wording
  parallel-softening missed); cycle-3 initially flagged the same
  stale-cache finding then re-ran clean on a fresh file read,
  returning 0/0/0 Ready+Clean. Useful reference: candidate_rebuild
  qualitative instances with curator-bounded universes benefit
  significantly from pre-staging design review (4 pre-staging
  audit-feedback iterations resolved BEFORE cycle-0 staging
  shortened the post-staging audit arc to 3 cycles — comparable to
  source_defined instances and shorter than other recent multi-
  cycle qualitative or candidate_rebuild instances). Also: stale-
  cache audit responses are a real failure mode in bounded
  verification cycles — the cycle-3 first-run repeated the
  cycle-2 finding verbatim despite the fix having landed; the
  cycle-3 re-run on a fresh file read returned 0/0/0.
- `largest_lakes_top20_by_area` (top_k, **Geography/Lakes NEW
  subdomain** — Geography family extends from 11 to 12 instances;
  20 rows × 5 cols; single PK = Lake; Tier B manual_wikipedia,
  source_count=1: master 'List of lakes by area' Wikipedia article
  only) **STAGED 2026-05-14 → Ready 2026-05-14 after a 2-cycle
  paired curator/audit arc terminating at cycle-2 0/0/0
  Ready+Clean**. Pushes `top_k` from 5/5 (just-saturated by
  `worlds_busiest_airports_by_passenger_traffic`) to **6/5 (1 over
  target)**. NEW `physical-features/lakes` fine-class extending
  the physical-features bucket from 6 to 7 fine-classes (mountains,
  rivers ×2, deserts, islands, oceanic-trenches + NEW lakes).
  Schema: Lake PK + Continent (closed 6-value: Africa, Antarctica,
  Asia, Europe, North America, South America) + Type (closed
  2-value: Fresh, Saline) + Area (km²) integer + Max. depth (m)
  integer. Universe rule: hard rank-based filter on source's
  Table[2] area-descending master ordering — top-20 by Area, no
  curator-judgment on which lakes qualify. Continent distribution:
  North America 10, Asia 3, Africa 3, Europe 2, Antarctica 1
  (Vostok subglacial), South America 1 (Titicaca); Type
  distribution: Fresh 18, Saline 2 (Caspian Sea rank-1, Balkhash
  rank-15). Notable design choices: (a) Caspian Sea staged as
  rank-1 lake per source's Notes-column ruling on lake-vs-ocean
  classification; (b) Caspian Continent = Asia per 4-of-5
  Asian-shoreline dominance rule (Russia is the transcontinental
  5th); (c) Vostok subglacial lake staged with Continent =
  Antarctica per geographic location; (d) Type closed 2-value
  vocab strips salinity-percentage suffixes ('Saline 1.2%' →
  'Saline'); (e) Area + Max depth parsed as integers from
  compound 'metric (imperial)' source format with comma stripping.
  Cycle arc: cycle-1 Ready with 1 HYGIENE (stale 'Fresh (15 rows)'
  count + out-of-universe Saline examples Turkana rank-22 +
  Issyk-Kul rank-23 in Type-normalization examples); cycle-2 0/0/0
  terminal. Useful reference: top_k-by-numeric-attribute instances
  with sub-universe scope (top-N cutoff on a longer master list)
  require explicit in-universe-vs-out-of-universe framing in
  normalization_decisions when discussing closed-vocab values that
  appear in source rows beyond the rank cutoff (the cycle-1
  hygiene pattern of Saline examples from ranks 22/23/25 wrongly
  presented as in-universe values is a generalizable pitfall to
  flag at Stage-1 for future top_k instances).
- `oceanic_trenches_named_deeps` (factual_lookup, **Geography/Oceans
  NEW subdomain** — Geography family extends from 10 to 11 instances;
  17 rows × 4 cols; composite PK (Trench, Lowest Point); Tier B
  manual_wikipedia, source_count=1: master 'List of oceanic trenches'
  Wikipedia article only) **STAGED 2026-05-14 → Ready 2026-05-14
  after a 3-cycle audit arc terminating at cycle-3 0/0/0 Ready+Clean**.
  Brings `factual_lookup` from gap 2 to **gap 1** (4 of 5 staged at
  the time of this staging; subsequently saturated to 5/5 by
  `worlds_tallest_dams_by_structural_height` and pushed 1 over
  target by parallel-agent `major_government_space_agencies` — now
  factual_lookup is 6/5).
  NEW physical-landform subdomain joining `major_world_deserts_by_area`
  (Geography/Deserts) as the second physical-landform-keyed instance
  in pkdb_bench. Schema: Trench + Lowest Point (composite PK) +
  Ocean Basin (closed 5-value: Pacific/Atlantic/Indian/Arctic/Southern)
  + Maximum Depth (m) numeric integer. Universe: 17 trench rows from
  master Table[0] (21 raw) filtered to non-NaN Lowest Point (drops
  4 Pacific NaN rows: Kuril-Kamchatka, Kermadec, Izu-Bonin, Japan).
  Composite PK was chosen over single-PK-dedup-keep-deepest variant
  (which would have lost Southern Ocean's only entry — South Sandwich
  Trench Factorian Deep) to preserve all 5 ocean basins populated.
  Ocean Basin distribution: Indian 6, Pacific 5, Atlantic 4,
  Southern 1, Arctic 1 — no empty closed-vocab slots. Cycle arc:
  cycle-1 Ready with 2 HYGIENE (named-deep wording inconsistency for
  India Trench's descriptive 'Between India & Maldives' Lowest Point
  + sort prose claimed 'chronologically by Maximum Depth descending /
  matching source's natural depth-ordered narrative' but source's
  Table[0] has minor depth-inversions); cycle-2 Ready with 1 HYGIENE
  (open_questions #7 stale 'matches source's natural order'); cycle-3
  0/0/0 terminal. Useful reference: single-source factual_lookup
  instances with clean source-defined universe rules can reach Ready
  in 1 cycle with hygiene-only residual; the 3-cycle arc was entirely
  cosmetic prose-alignment scrubs (no CSV/schema/source-set changes).
  Composite PK pattern matches `apollo_lunar_landing_mission_crews`
  (Mission × Astronaut) and `foundational_painters_of_major_modern_art_movements`
  (Movement × Painter) precedents.
- `major_government_space_agencies` (factual_lookup,
  **Science/Space Exploration — first organization-keyed entity in
  the Space Exploration subdomain** joining crewed_spaceflight_fatal_incidents,
  apollo_lunar_landing_mission_crews, and notable_robotic_spacecraft_missions
  (all event-keyed); Science family extends from 11 to 12 instances;
  24 rows × 4 cols; single PK = Agency; Tier B manual_wikipedia,
  source_count=1: master 'List of government space agencies'
  Wikipedia article only) **STAGED 2026-05-14 → Ready 2026-05-14 after
  a 2-cycle paired curator/audit arc terminating at cycle-2 0/0/0
  Ready+Clean**. Pushes `factual_lookup` from 5/5 (just-saturated by
  `worlds_tallest_dams_by_structural_height`) to **6/5 (1 over
  target)**. NEW `organizations/space-agencies` fine-class (bucket
  extends from 2 to 3 instances: universities + companies + space
  agencies). Schema: Agency PK + Full Name + Country or Organization
  + Founded (year integer). Universe rule: source-defined Table[4]
  launch-related capabilities subset within the master article (24
  agencies enumerated in the master's launch-capabilities sub-table
  regardless of pandas-parsed-NaN cells in the capability columns —
  the source renders Yes/No markers via flag/check icons that
  pandas.read_html parses as NaN, so the universe rule is
  Table[4]-membership-alone, not capability-based). Notable design
  choices: (a) Iran and Israel use full-name Agency PK (Iranian
  Space Agency, Israel Space Agency) per Table[4]'s disambiguation
  convention to avoid PK collision on shared acronym 'ISA'; (b)
  Mexican Space Agency uses MSA per Table[4] (universe-defining
  table) despite Table[3] master using AEM for the same agency
  (acronym cross-table discrepancy documented); (c) Soviet space
  program † defunct entity retained with † marker per source; (d)
  ESA Country cell normalized from 22-member-state concatenation
  list to descriptive label 'European Space Agency member states';
  (e) Founded year uses current-entity year for multi-year founding
  history cells (e.g., CONAE 1991 not 1961 CNIE predecessor; NATA
  2023 not 1980s KCST). Cycle arc: cycle-1 Ready with 2 HYGIENE
  (stale 'all-NaN launch capabilities / no demonstrated launch
  capabilities' wording for CSA/MSA/POLSA/UKSA — the source actually
  shows Yes/No icon markers that pandas parses as NaN; + Founded
  decade distribution summed to 25 not 24); cycle-2 0/0/0 terminal.
  Useful reference: single-source factual_lookup instances with
  sub-table-defined-universe (Table[4] within a master article)
  require explicit documentation that the staged schema does NOT
  include the sub-table's filter columns; the prose-pitfall is
  inferring 'no capability demonstrated' from pandas-parsed NaN
  cells when the source actually renders the capability via
  flag/check icons.
- `major_historical_empires_founder_capital_century` (multi_hop,
  **History/Empires NEW subdomain** — History family extends from 3
  instances (Politics 2 + Military 1) to 4 instances with this Ready,
  becoming a 3-subdomain family; 17 rows × 5 cols; Tier B
  manual_wikipedia, source_count=18: 1 master 'List of empires' + 17
  per-empire articles) **STAGED 2026-05-14 → Ready 2026-05-15 after a
  5-cycle paired curator/audit arc terminating at 0/0/0 Ready+Clean**.
  **SATURATES `multi_hop` at 5/5 — SEVENTH CATEGORY TO FULLY CLEAR**
  (joining factual_lookup 6/5, top_k 6/5, multi_attribute_projection
  8/8, set_operation 8/8, set_filter 5/5, causal 5/5). NEW
  `events/empires` fine-class (events bucket extends from 9 to 10
  instances). Schema: Empire PK + Founder + Capital + Founding Century
  + Region. Universe rule: 17 curator-selected major historical empires
  from Wikipedia 'List of empires' master article per documented
  criteria — cross-regional balance (7 regions: Europe 3, Middle East
  4, East Asia 4, South Asia 2, Americas 2, Southeast Asia 1, Steppe
  1) + empire-as-entity-framing-required + founder-semantics-consistency
  + permanent-capital-required. claim_type = `partially_supplemented`.
  **Cycle-2-refined two-tier rule for Founder + Capital** (mirrors
  the battles two-tier commander rule): infobox-grounded default for
  unambiguous rows (9 of 17 Founder + 11 of 17 Capital) + explicit
  curator-judgment for ambiguous rows (8 of 17 founder-ambiguous +
  6 of 17 multi-capital). Long cycle arc: cycle-1 caught 2 BLOCKING
  (Macedonian Empire URL resolved to kingdom article → dropped at
  cycle-2; Capital rule overclaimed infobox-first → refactored to
  two-tier) + 3 HYGIENE; cycle-2 substantive repairs; cycle-3 + cycle-4
  + cycle-5 progressive narrative-residue cleanup (5 → 4 → 2 → 0 HYG).
  Useful references for future partially_supplemented multi_hop
  instances: (a) **empire-as-entity-framing-required rule** — per-entity
  article must support the entity-as-empire framing (NOT a kingdom or
  biographical article); cycle-1 audit caught the Macedonian Empire URL
  resolving to 'Macedonia ancient kingdom' article (kingdom-level with
  legendary founders), not Alexander-as-empire-founder — exclusion
  rule committed at cycle-2; (b) **two-tier rule for ambiguous
  per-row attributes** (Founder + Capital here; same pattern as battles
  Commander rule) — infobox-grounded default + explicit curator-judgment
  for ambiguous rows, with explicit annotation in source_coverage_notes
  for each ambiguous row; (c) **honest cardinality reckoning** — count
  prose must match the staged data exactly (cycle-3 + cycle-4 caught
  off-by-one count residue: "5 of 17" vs "6 of 17"; "12 of 17" vs
  "11 of 17"; "18 distinct" vs "17 distinct"; "Curator-selected 18"
  vs "17"). Cycle entries describe actual state, not intended state.
- `decisive_battles_of_world_war_ii` (multi_hop,
  **History/Military NEW subdomain** — History family extends from
  2 instances (both History/Politics) to 3 instances with this Ready;
  History was the second-most under-represented family among the 16;
  18 rows × 6 cols; Tier B manual_wikipedia, source_count=19: 1
  master 'List of World War II battles' + 18 per-battle articles)
  **STAGED 2026-05-14 → Ready 2026-05-14 after a 4-cycle paired
  curator/audit arc terminating at 0/0/0 Ready+Clean**. **Chips
  `multi_hop` from gap 2 to gap 1** (4 of 5 staged; one more
  saturates as the 7th fully-cleared category). NEW
  `events/battles` fine-class (events bucket extends from 8 to 9
  instances). Schema: Battle PK + Year + Theater + Winning Side +
  Allied Commander + Axis Commander. Universe rule: 18 decisive WWII
  battles curator-selected from Wikipedia master article (which
  enumerates hundreds) per documented criteria — theater balance +
  resolvable outcome lines under a two-clause Winning Side rule +
  historical decisiveness. claim_type = `partially_supplemented`
  (cycle-2 reframe from `source_defined`; master article is a broad
  enumeration NOT a source-defined decisive-battles list).
  Theater distribution (4-value curator-collapsed vocabulary from
  master's 9 sections): Pacific 7, Eastern Front 4, North African
  / Mediterranean 4, Western Front 3. Winning Side: Allied 17, Axis
  1 (Pearl Harbor only). Cycle arc: cycle-1 audit 2 BLOCKING (claim_type
  mismatch + Coral Sea Winning Side rule); cycle-2 repair landed
  substantive rule refinements; cycle-3 + cycle-4 narrative-residue
  hygiene cleanups → terminal 0/0/0. **Useful references for future
  partially_supplemented multi_hop instances**: (a) **two-clause
  Winning Side rule** — clean infobox 'Result' field for most rows,
  with strategic-level subrule for 2-tier-result battles (Coral Sea
  precedent); (b) **two-tier Commander selection rule** — default
  article-lead-foregrounded for most rows, with explicit curator-override
  for rows where article infobox lists supreme political commander
  first AND article lead doesn't foreground a single operational
  commander (Moscow/Berlin → Bock/Heinrici over Hitler precedent);
  (c) **curator-collapsed categorical vocabularies** — when master
  article uses N sections and an instance only needs K<N, honestly
  describe the K-value vocab as curator-collapsed from the master's
  N sections, NOT as 'per master article's K-front partition'.
- `worlds_busiest_airports_by_passenger_traffic` (top_k,
  **Transportation/Aviation NEW subdomain** — Transportation family
  extends from 2 to 3 instances; 20 rows × 5 cols; Tier B
  manual_wikipedia, source_count=21: 1 master 'List of busiest
  airports by passenger traffic' + 20 per-airport articles)
  **STAGED 2026-05-14 → Ready 2026-05-14 after a 2-cycle paired
  curator/audit arc terminating at 0/0/0 Ready+Clean**. **SATURATES
  `top_k` at 5/5 — SIXTH CATEGORY TO FULLY CLEAR** (joining
  factual_lookup 6/5, multi_attribute_projection 8/8, set_operation
  8/8, set_filter 5/5, causal 5/5). NEW
  `engineered-structures/airports` fine-class (bucket extends from 3
  to 4 instances: bridges + buildings + dams + airports). Schema:
  Airport PK + Country + City + Passengers (millions) + Region.
  Universe rule: top-20 airports by 2025 annual passenger traffic
  per Wikipedia master article (ACI sourced). Country distribution:
  United States 5 (NA dominance), China 4, plus 11 singletons (13
  distinct countries — more diverse than ports 10). Region
  distribution: East Asia 6, North America 5, Europe 5 (including
  Istanbul per master grouping), Southeast Asia 2, South Asia 1,
  Middle East 1 (6 of 9 declared). Cycle arc: cycle-1 audit caught
  0 BLOCKING + 3 HYG (city distinct-count off-by-one 19→20;
  Singapore Changi mislabeled as a master-vs-per-article name
  difference when it is actually a concurrence — 4→3 differences;
  open_questions item 1 said full-calendar-year 2025 but Indira
  Gandhi is fiscal year Apr-2024 to Mar-2025); cycle-2 repair fixed
  all 3 — terminal 0/0/0. Useful reference: **time-series-master-
  wins canonical-source rule** confirmed as the canonical precedent
  for top-K instances on annually-updated throughput metrics
  (ports + airports both apply it uniformly). Future top-K
  instances with industry-tracked annual metrics (e.g., busiest
  train stations, container-port tonnage, stock exchange market
  cap) should cite this precedent pair.
- `worlds_largest_container_ports_by_2024_teu` (top_k,
  **Transportation/Logistics NEW subdomain** — Transportation family
  extends from 1 instance (`countries_that_drive_on_the_left`,
  Transportation/Road Traffic) to 2 instances with this Ready;
  Transportation was the most under-represented domain family in
  the corpus; 20 rows × 5 cols; Tier B manual_wikipedia,
  source_count=21: 1 master 'List of busiest container ports' +
  20 per-port articles) **STAGED 2026-05-14 → Ready 2026-05-14
  after a 2-cycle paired curator/audit arc terminating at 0/0/0
  Ready+Clean**. **Chips `top_k` from gap 2 to gap 1** (4 of 5
  staged; one more saturates the category). NEW
  `organizations/ports` fine-class (bucket extends from 3 to 4
  instances: universities + companies + space-agencies + ports).
  Schema: Port PK + Country + City + TEU (millions) + Region.
  Universe rule: top-20 container ports by 2024 annual TEU
  throughput per Wikipedia master article (Lloyd's List 2024
  sourced). Country distribution: China 9 (modern-port dominance),
  Malaysia 2, USA 2, plus 7 singletons (10 distinct countries).
  Region distribution: East Asia 10, Southeast Asia 4, Europe 2,
  N. America 2, Middle East 1, Africa 1 (6 of 9 declared regions).
  Cycle arc: cycle-1 audit caught 0 BLOCKING + 2 HYG (city
  distinct-count narrative claimed 17 but CSV has 20; temporal_note
  misclassified Region as per-port sourced when it is curator-
  assigned); cycle-2 repair fixed both — terminal 0/0/0. **Useful
  reference: canonical-source rule INVERSION for time-series
  metrics** — the bridges + dams precedent had per-entity-wins for
  static structural metrics (Main Span, Height) but ports flipped
  this to master-wins for TEU because Lloyd's List 2024 is fresher
  than per-port infoboxes (which lag 3-10 years). The inversion is
  domain-specific (time-series vs static) and should be cited by
  future top-K instances with annually-updated throughput metrics.
- `worlds_tallest_dams_by_structural_height` (factual_lookup,
  **Technology/Civil Engineering subdomain — second Civil Engineering
  instance** joining `notable_bridges_by_main_span`; Technology family
  extends from 3 to 4 instances; 20 rows × 5 cols; Tier B
  manual_wikipedia, source_count=21: 1 master 'List of tallest dams'
  + 20 per-dam articles) **STAGED 2026-05-14 → Ready 2026-05-14 after
  a 2-cycle paired curator/audit arc terminating at 0/0/0
  Ready+Clean**. **SATURATES `factual_lookup` at 5/5 — FIFTH CATEGORY
  TO FULLY CLEAR** (joining multi_attribute_projection 8/8,
  set_operation 8/8, set_filter 5/5, causal 5/5; the benchmark target
  is now 5 of 15 categories at saturation). NEW
  `engineered-structures/dams` fine-class (bucket extends from 2 to
  3 instances: bridges + buildings + dams). Schema: Dam PK + Height
  (m) + Country + River + Year Completed. Universe rule: top-20
  completed man-made dams by structural height per Wikipedia 'List of
  tallest dams' master article 'Existing' section, with 3 natural-
  origin entries excluded (Usoi landslide 567 m, The Barrier lava
  500 m, Lake Waikaremoana landslide 250 m — mirrors the bridges
  engineered-structures-only scope). Country distribution: China 7
  (modern-dam-engineering dominance), Switzerland 2 + Turkey 2, plus
  9 singletons (12 distinct countries total). Cycle arc: cycle-1
  audit caught 1 BLOCKING factual (Mica Dam Height 243 m supported
  by master but per-dam article internally inconsistent at infobox
  240 / body 244) + 2 HYG (decision_log country-count stale +
  Three-vs-Four precision-rows count); cycle-2 repair extended the
  cycle-2 bridges canonical-source rule to a two-clause version —
  Clause A (per-dam-wins) for Gilgel Gibe III 250 m vs master 243 m;
  **Clause B (master-fallback) NEW** for Mica when per-dam article
  is internally inconsistent. Useful reference: per-dam-internal-
  inconsistency on Height/Year cells is a real audit-finding pattern
  for engineered-structure top-K instances (infobox vs body
  disagreement) — the cycle-2-of-this-instance master-fallback rule
  is the canonical resolution and should be cited by future
  Pingnan-Third-style or Mica-style discrepancies.
- `notable_bridges_by_main_span` (top_k, **Technology/Civil
  Engineering NEW subdomain** — Technology family extends from 2 to 3
  instances; 17 rows × 5 cols; Tier B manual_wikipedia,
  source_count=22: 5 per-type 'List of longest X bridge spans' master
  articles + 17 per-bridge articles) **STAGED 2026-05-13 → Ready
  2026-05-14 after a 3-cycle paired curator/audit arc terminating at
  0/0/0 Ready+Clean**. Brings `top_k` from gap 3 to **gap 2** (3 of
  5 staged). NEW domain family-subdomain (no prior Civil-Engineering
  instance; cycle-1 staged 'Engineering/Civil Engineering' which
  cycle-2 audit HYGIENE H1 corrected to 'Technology/Civil
  Engineering' per `domain_taxonomy.md` Technology-family pattern).
  NEW entity class: bridges as engineered structures (no prior
  pkdb_bench instance covers engineered physical structures —
  joining the natural-physical-feature pattern in deserts + rivers
  while distinguishing engineered from natural). Schema: Bridge PK +
  Type (5-value: Suspension/Cable-stayed/Arch/Cantilever/Continuous
  truss) + Main Span (m) + Country + Year of Completion. Universe
  rule: 4+1 asymmetric partition — top-4 per the four primary
  load-bearing types + top-1 for Continuous truss (Ikitsuki); 4+1
  reflects continuous truss type's lower span-ceiling falling below
  the visible-engineering threshold the four primary types occupy.
  Cycle arc: cycle-1 BLOCKING (3) for stale Suspension/Cable-stayed
  top-4 (Xihoumen + Stonecutters displaced per current master lists
  → Nanjing Xianxin + Husutong) + Changtai year 2024 → 2025; cycle-1
  HYGIENE (2) for invented domain family + Pingnan Third 575/560
  master-vs-per-bridge discrepancy; cycle-2 repair Ready-for-data
  with 2 residual HYGIENE for canonical-source-rule contradiction +
  proposed_supplements stale dropped-row list; cycle-3 hygiene
  cleanup → 0/0/0 terminal. Useful reference: top-K-per-type
  ranking-instance temporal-churn caught at audit time
  (master-list-displacement BLOCKING pattern — top-K rankings shift
  faster than master-list cell-state, and cycle-1 staging from a
  point-in-time master-list snapshot can be already-stale at audit
  time); per-bridge-vs-master-list source-precedence rule refined
  during cycle-2 (per-bridge article wins for Main Span when
  per-bridge is more detailed and infobox + lead concur, master wins
  for ranking).
- `major_modern_epidemics_and_pandemics_1918_2025` (causal,
  **Health/Epidemiology NEW subdomain** — Health family extends from
  1 (Nutrition) to 2 instances; 16 rows × 6 cols; Tier B
  manual_wikipedia, source_count=18: 2 master articles (List of
  epidemics and pandemics + Public health emergency of international
  concern) + 16 per-event articles) **STAGED 2026-05-13 → Ready
  2026-05-14 after a 3-cycle audit arc terminating at 0/0/0
  Ready+Clean**. **SATURATES `causal` at 5/5 — FOURTH CATEGORY TO
  FULLY CLEAR** after multi_attribute_projection 8/8, set_operation
  8/8, and set_filter 5/5. Schema C (6 cols): Epidemic PK +
  Start Year + Disease + Affected Region + Transmission Mode +
  Specific Trigger. Universe: 16 events satisfying at least one of
  three authoritative master rules — Wikipedia 'Major by death toll'
  top-19 filtered ≥1918 (6 candidate memberships), Wikipedia
  'Ongoing pandemics' list (6 events), and WHO PHEIC declarations
  (8 declarations); after dedup HIV/AIDS + COVID-19 + the two
  mpox PHEIC declarations, 16 unique events: Spanish flu, Russia
  typhus 1918, Asian flu 1957, Hong Kong flu 1968, 7th cholera
  1961+, HIV/AIDS, 2009 swine flu, 2014 polio declaration, MERS,
  West African Ebola 2013-16, Zika 2015-16, Kivu Ebola 2018-20,
  COVID-19, 2022-2023 mpox Clade IIb, 2023-2026 monkeypox Clade Ib,
  2024-25 Sudanese cholera. Transmission Mode distribution
  (closed 5-value): Respiratory 6, Direct contact 5,
  Water-or-Food-borne 3, Vector-borne 2, Iatrogenic-or-Bioterrorism 0
  (empty bucket retained for closed-vocab integrity). Cycle arc:
  cycle-1 BLOCKING for answer-key cells (Transmission Mode + Specific
  Trigger) not source-grounded by cycle-0's 2 master sources —
  resolved via Path A (source_count 2 → 18 with 16 per-event
  Wikipedia articles); cycle-2 BLOCKING for 3 Specific Trigger
  cells (Spanish flu / Hong Kong flu / W. African Ebola) overstating
  or contradicting per-event sources — resolved by rewriting all
  3 trigger cells to source-faithful wording (Spanish flu's
  geographic origin uncertain; Hong Kong flu's Vietnam-War-troops
  mechanism replacing air-travel claim; W. African Ebola's
  Meliandou/insectivorous-bat hypothesis replacing fruit-bat-origin
  claim); cycle-3 0/0/0 terminal. Useful reference:
  multi-source-instances-with-curator-judgment-cells (closed
  taxonomy + semantic-match paraphrase) require per-event source
  expansion when master article lacks per-row narrative columns;
  per-event source-grounding rigor on Specific Trigger cells is
  the most-likely cycle-2-cycle-3 audit-finding pattern (curator
  knowledge of canonical disease origin can leak into trigger
  cells when source-articles disagree with conventional framing).
- `notable_robotic_spacecraft_missions` (multi_hop, **Science/Space
  Exploration existing subdomain — third Space-Exploration
  instance** joining `apollo_lunar_landing_mission_crews` and
  `crewed_spaceflight_fatal_incidents`; Science family extends from
  10 to 11 instances; 22 rows × 4 cols; Tier B manual_wikipedia,
  source_count=23) **STAGED 2026-05-13 → Ready 2026-05-13 after a
  3-cycle paired curator/audit arc terminating at 0/0/0** (parallel
  agent — full bullet at line 657 below).
- `modern_financial_crises_1929_2025` (causal, **Business/Economics —
  third Business instance**, joining g20_countries_attributes and
  eurozone_adoptions_1999_2023; 27 rows × 5 cols; Tier B
  manual_wikipedia, source_count=1: master 'List of stock market
  crashes and bear markets' Wikipedia article) **STAGED → Ready
  2026-05-13 after a 6-cycle audit arc terminating at cycle-6 0/0/0
  Ready+Clean**. Brings `causal` from gap 2 to **gap 1** (4 of 5
  staged) — **first non-saturated category to reach gap-1** since
  multi_attribute_projection saturated at 8/8. Schema: Crisis PK +
  Year + Affected Region + Cause Category + Specific Trigger.
  Universe: 27 stock market crashes and bear markets from 1929
  (Wall Street Crash) through 2025 whose Wikipedia master-list
  Notes column documents a clear proximate cause — dual-clause
  filter ('Year ≥ 1929' yields 37 candidates, then
  'Notes column documents a clear cause' drops 10). Schema designed
  for benchmark evaluation: 3 of 5 cells are exact-match (Year,
  Region, Cause Category from closed 6-value taxonomy), Specific
  Trigger is semantic-match free-text. Cause distribution: External
  shock 10, Speculative bubble 5, Margin/leverage failure 4, Real
  estate bubble 3, Sovereign debt crisis 3, Currency crisis 2. Cycle
  arc: cycle-1 BLOCKING for source-grounding rigor (Cause Category /
  Trigger cells over-specified beyond master Notes column for 7 rows
  → dropped, universe 37→30); cycle-2 BLOCKING for further over-
  specification on retained rows (Trigger wording → master Notes;
  2 more rows dropped, 30→28); cycle-3 BLOCKING for last over-
  specification + 3 Category corrections (Rio→Margin/leverage; 1997
  Asian / Oct 1997 → Speculative; 2018 crypto dropped, 28→27);
  cycle-4 hygiene (db_analog dual-clause predicate + drop bare '37'
  current-state refs + drop dropped-rows from NaN-resolution
  paragraphs); cycle-5 hygiene candidate-card scrubs (db_analog
  quote + Expected shape '37' + 4-row NaN list); cycle-6 0/0/0
  Ready+Clean verification audit. Useful reference: single-source
  manual_wikipedia instances with curator-judgment cells (Cause
  Category taxonomy + Specific Trigger paraphrase) need strict
  master-Notes-only commitment from cycle-0; per-event Wikipedia
  article details (Volcker, Enron, portfolio insurance, etc.) WILL
  leak into staged cells if the source rule isn't enforced at
  Stage-1. Three of six cycles were source-grounding repair (same
  pattern as crewed_spaceflight_fatal_incidents).
- `worlds_reigning_kingdoms` (set_filter, **Politics/Government —
  extends Politics from 6 to 7 instances**; 17 rows × 5 cols; Tier B
  manual_wikipedia, source_count=2: master 'List of current
  monarchies' + 'List of current monarchs of sovereign states'
  supporting article) **STAGED → Ready 2026-05-13**. **SATURATES
  `set_filter` at 5/5 — third category to fully clear target**
  (joining multi_attribute_projection 8/8 + set_operation 8/8).
  Schema: Country PK + Region + Type of Monarchy + Current Monarch +
  Year of Accession. Universe: 17 sovereign states constituted as
  a kingdom with their own monarch (excludes 14 Commonwealth realms
  that share Charles III with UK but are not constituted as
  'Kingdom of X' — Canada/Australia/Jamaica etc. are excluded by
  common-name convention; the UK is retained as the original
  kingdom whose monarchy the realms share). Two-clause filter:
  (a) Title of Head of State contains 'King' AND (b) country is
  NOT a Commonwealth realm. Type distribution: Parliamentary 10 /
  Semi-constitutional 5 / Absolute 2 (Saudi Arabia, Eswatini).
  Region distribution: Europe 7, Middle East 3, Asia 3,
  Sub-Saharan Africa 2, North Africa 1, Pacific 1. Useful
  reference: "world's kingdoms" framing requires explicit
  Filter A (any King-titled head of state, 31 rows incl.
  Commonwealth realms) vs Filter B (kingdoms excluding
  Commonwealth realms, 17 rows) deep-dive at Stage-1; common-name
  English usage aligns with Filter B (Wikipedia naming
  'Kingdom of Spain' / 'Kingdom of Saudi Arabia' exists but
  'Kingdom of Canada' does not). Year of Accession cells required
  source_count=1→2 via the supporting per-monarchs article
  (master article has no Since column).
- `programming_languages_by_decade_of_first_appearance` (aggregation,
  Technology/Programming NEW subdomain, 14 rows × 5 cols, total 424
  Timeline entries across 14 decades 1790s-2020s) staged this
  session. Reached Ready after a 6-cycle audit arc. Brings
  `aggregation` from gap 4 to **gap 3** (2 of 5) — the first
  `aggregation` staged instance. Technology family extends from 1
  (Inventions) to 2.
- `major_b2c_online_learning_platforms` (multi_attribute_projection,
  **Education/EdTech NEW family**, 13 rows × 4 cols, Tier B
  manual_wikipedia, source_count=15) **reached Ready 2026-05-11**
  after a 10-cycle audit arc. **All 16 domain families now have at
  least one instance** — Education was the lone remaining
  family-level gap before this staging. Brings
  `multi_attribute_projection` from gap 3 to **gap 2** (6 of 8). The
  cycle history is unusually long because each cycle compounded
  scope drift: cycles 1-2 broad-MOOC universe and per-row value
  fixes; cycles 3-5 strict source-grounded-Founders policy adoption
  (Owner→strict-verb-phrase list; dropped FUN, openHPI, OpenLearning);
  cycle 6 hygiene; cycle 7 BLOCKING (the "cross-referenced
  language-learning / coding / skills / K-12 / test-prep /
  tech-skills" subcategory clause was curator-defined, not source-
  defined); cycle 8 Path B re-scope to MOOC-providers-only
  (29 → 14 rows, dropped Domain column); cycle 9 Stanford Online
  drop (master 2006 vs per-platform 1995 founded-year discrepancy
  resolved by per-platform-preferred rule) + 2 hygiene exclusions
  added; cycle 10 Wikiversity Country source-grounding via
  Wikimedia Foundation parent article + 3 retained master-vs-per-
  platform discrepancies documented (Khan Academy 2006/2008,
  OpenClassrooms 2007/2013, Udacity 2012/2011). Useful reference:
  multi-source instances with a structured master-list-defined
  universe (similar pattern to Law/Judiciary chief-justice instance
  and Programming Languages aggregation) require explicit
  master-vs-per-platform-disagreement policy documentation at
  Stage-1 staging, not patched in after audit pressure.
- Earlier this session: `countries_over_50pct_renewable_electricity`
  (set_filter, Environment/Energy NEW family — set_filter gap 2→1)
  and `landmark_us_supreme_court_decisions_by_chief_justice`
  (implicit_join, Law/Judiciary NEW family, 115 rows after 11-cycle
  audit arc — implicit_join gap 5→3).
- `apollo_lunar_landing_mission_crews` (multi_hop, **Science/Space
  Exploration NEW subdomain**, 18 rows × 5 cols across 6 Apollo
  lunar landings × 3 crew, Tier B manual_wikipedia, source_count=7)
  staged 2026-05-11. Reached Ready in a single cycle (only blocker
  was a linter blanking curation.json — mechanical fix). Brings
  `multi_hop` from gap 4 to **gap 3** (2 of 5). Science family
  extends from 6 subdomains to 7 (+ Space Exploration).
- `time_person_of_the_year_2000_2024` (temporal, **Society/Media
  NEW subdomain**, 25 rows × 5 cols, Tier B manual_wikipedia,
  source_count=1) staged this session 2026-05-11 → **reached Ready
  2026-05-11** after a 2-cycle audit arc: cycle-1 BLOCKING was
  rowspan=2 row recovery (the source's 2020 row assigns Biden+Harris
  as two sub-entities via HTML rowspan=2, dropped by the cycle-0
  regex; same-structure issue at 2022 Zelenskyy+Spirit-of-Ukraine
  caught proactively in the cycle-1 repair); cycle-1 also added
  'Joint Selection' as a third Entity Type value; cycle-2 closed
  with HYGIENE-only residual stale-narrative fixes (17→15
  Individuals count, 105-rows-1927-present count). Brings
  `temporal` from gap 4 to **gap 3** (2 of 5). Society family
  extends from 1 (Languages) to 2 (+ Media). Useful reference:
  Wikipedia tables with HTML rowspan structure need explicit
  rowspan-aware extraction at the Stage-1 pipeline step — regex
  cell-walking misses sub-entities silently.
- `fifty_us_states_attributes` (multi_attribute_projection,
  **Politics/Government — extends Politics from 5 to 6**, 50 rows ×
  6 cols, Tier B manual_wikipedia, source_count=1) staged this
  session 2026-05-11 → **reached Ready 2026-05-11 in 1 cycle with
  1 HYGIENE** (ratifier-count off-by-7-vs-actual-11 stale narrative;
  CSV values were correct). Verbatim seed #17 from
  `example_questions.txt`. **SATURATES `multi_attribute_projection`
  at 8/8 target — first category to fully clear target.** Universe:
  50 US states (DC and 5 territories explicitly in separate
  wikitables, not in scope). Schema: 6 cols (State PK + Capital,
  Largest City, Statehood Year, Population (2020), Total Area (sq
  mi)). 50 rows is a different row-count band from the recent
  fixed-entity-list cluster (G20 19, Time POY 25, Presidents 18,
  EdTech 13). 17 of 50 states have Capital == Largest City; 33 do
  not — Capital-vs-Largest-City is the schema's richest calibration
  axis. Useful reference: pandas.read_html with multi-column-header
  parsing on a well-structured Wikipedia master wikitable is a
  clean 1-cycle pattern; the lone HYGIENE issue was narrative
  drift in the ratifier-count explanation, not a CSV-cell issue.
- `twentieth_century_us_presidents_attributes` (multi_attribute_projection,
  **History/Politics — second History instance**, 18 rows × 5 cols,
  Tier B manual_wikipedia, source_count=1) staged this session
  2026-05-11 → **reached Ready 2026-05-11 in 1 cycle with 0
  findings**. Verbatim seed #18 from `example_questions.txt`.
  Brings `multi_attribute_projection` from gap 2 to **gap 1** (7 of
  8) — third category to approach saturation, joining `set_filter`
  (gap 1) and `set_operation` (gap 1). Universe: 18 US presidents
  whose term overlapped any portion of the 20th century (1901-2000)
  — McKinley (Jan-Sept 1901) included; George W. Bush (started
  Jan 20, 2001) excluded. Schema: 5 cols (President PK + Term Start
  Year, Term End Year, Party, Vice President(s)). 3 multi-VP rows
  (FDR 3 VPs, Nixon 2 VPs, McKinley 2 VPs). Single-source from
  master Wikipedia list; pandas.read_html with rowspan-expansion
  handled the multi-term/multi-VP row structure cleanly. Useful
  reference: single-source staging from a well-curated Wikipedia
  master wikitable + rowspan-aware pandas extraction is a clean
  1-cycle Ready pattern when the schema is conservative (5 cols
  all from master).
- `foundational_painters_of_major_modern_art_movements`
  (hierarchical_decomposition, **Culture/Visual Arts NEW
  subdomain**, 18 rows × 5 cols, composite PK (Movement, Painter),
  6 art movements × 3 painters, Tier B manual_wikipedia,
  source_count=41) staged 2026-05-11 → **reached Ready 2026-05-11
  after a 5-cycle audit arc**. Cycle-1 BLOCKING: per-painter URLs
  missing (source_count 7→25), Year range-scalarization
  inconsistency (Gauguin 1898→1897, Cézanne 1892→1890 with Barnes
  Foundation version pin), claim_type source_defined →
  partially_supplemented + proposed_supplements documenting
  per-movement curator selection (3-of-N named members per
  article). Cycle-2: Rothko 'Orange and Yellow' 1956 → 'Orange,
  Red, Yellow' 1961 (matches Rothko Wikipedia page) + 18 per-
  painting URLs (source_count 25→43). Cycle-3: 2 invalid per-
  painting URLs removed (Pissarro Boulevard series, Gris Portrait
  both 404'd — painter-article fallback for those 2 rows;
  source_count 43→41) + Hamilton Nationality 'British' → 'English'
  (Wikipedia opening rule). Cycle-4: source_coverage_notes Roles
  block reindexing after cycle-3 URL removal (Drowning_Girl
  [40]→[38], List_of_art_movements [42]→[40]). Cycle-5: HYG
  cleanup of 'Source plan: 43 Wikipedia URLs' stale reference.
  Brings `hierarchical_decomposition` from gap 4 to **gap 3** (2
  of 5). Culture family extends from 6 to 7 subdomains (+ Visual
  Arts joining Film, Music, Literature, etc.). NEW entity class
  (art movements × painters × paintings) — neither countries nor
  cities/continents. Useful reference: when source_urls are
  removed mid-cycle, ALL Roles-block indices downstream shift;
  per-painting source-grounding may surface 404 URLs that the
  audit catches as B1 blockers — Pissarro Boulevard series and
  Gris Portrait don't have dedicated Wikipedia articles, requiring
  painter-article fallback grounding (documented in
  source_coverage_notes).
- `major_western_philosophers_primary_works` (qualitative,
  **Culture/Philosophy NEW subdomain**, 16 rows × 4 cols, Tier B
  manual_wikipedia, source_count=32 dual-URL-per-row) staged this
  session 2026-05-12 → **reached Ready 2026-05-12 (bounded-clean)
  after a 4-cycle audit arc**. Original instance (no seed) filling
  the qualitative category gap (gap 4 → 3). Universe is curator-
  bounded (claim_type=candidate_rebuild) — 16 major Western
  philosophers from antiquity (Plato c. 375 BC) through mid-20th
  c. (Sartre 1943) selected from philosophers commonly listed
  across major philosophy reference works. Schema: Philosopher PK
  + Primary Work, Year, Tradition. Cycle history theme: cycle-1
  BLOCKING was Year cells not source-grounded by per-philosopher
  URLs (added 16 per-work URLs, source_count 16→32; Aristotle
  -340→-322 per Nicomachean Ethics article's 335-322 BCE window;
  Marcus Aurelius 175→180 per his Wikipedia article's 170-180
  AD window with death-year convention); cycles 2-4 caught
  recurring narrative drift around the old "16-source pattern"
  framing and Primary Work provenance overstatement
  ("most-canonical per Wikipedia" vs the more honest "curator
  selects from source-listed major works"); cycle-4 bounded
  verification with 1 accepted residual HYGIENE. Brings
  `qualitative` from gap 4 to gap 3. Culture family extends from
  7 to 8 subdomains (+ Philosophy joining Film, Music, Literature,
  Visual Arts, etc.). Useful reference: qualitative-category
  curator-bounded universes ARE acceptable (claim_type=
  candidate_rebuild signals this honestly), but the cycle history
  shows that "Primary Work" / "canonical work" framing needs to
  be applied uniformly from cycle-0 as "curator-selects-from-
  source-listed-set" — fragmented Wikipedia source-anchoring for
  qualitative single-work designations recurrently leaks into
  narrative as overstated source-grounding.
- `chemical_elements_discovered_1869_1945` (factual_lookup,
  **Science/Chemistry NEW subdomain**, 31 rows × 2 cols, Tier B
  manual_wikipedia, source_count=1) staged this session 2026-05-12
  → **reached Ready 2026-05-12 after a 3-cycle audit arc**.
  Original instance (no seed) filling the factual_lookup gap (gap
  4 after the 2026-05-12 target raise from 3 to 5; this instance
  brings it to gap 3). Universe: 31 chemical elements with
  Observed Year ∈ [1869, 1945] inclusive per the master
  Wikipedia 'Discovery of the chemical elements' article's
  tables[4] (Gallium 1875 first, Promethium 1945 last). Schema:
  Element PK + Discovery Year (2-col, matching the existing
  factual_lookup template `eu_member_states_accession_years`).
  Cycle history theme: cycle-1 BLOCKING was title-vs-rule
  mismatch ('first observed or isolated' overpromised vs strict
  Observed-Year-only filter; 10 mismatched elements like
  Fluorine, Helium would have been included under the literal
  title); cycle-2 caught residual stale wording in
  collection_summary / source_coverage_notes; cycle-3 verification
  audit caught further residual stale 'Isolated Year fallback'
  sentence in normalization_decisions and stale 'discovery_year
  BETWEEN' db_analog quote in decision_log. Brings
  `factual_lookup` from gap 4 to gap 3. Science family extends
  from 8 to 9 instances (Chemistry NEW subdomain joining Astronomy,
  Academia ×2, History of Science, Mathematics, Physics, Space
  Exploration ×2). Useful reference: title-vs-rule alignment and
  cross-file stale-narrative-quote sweeps are recurring HYGIENE
  pitfalls — when a BLOCKING repair changes a field value, grep
  for ALL prior narrative quotes of that value across decision_log,
  _extraction_notes, normalization_decisions, source_coverage_notes,
  collection_summary, and temporal_note.
- `crewed_spaceflight_fatal_incidents` (causal, **Science/Space
  Exploration — extends to 2 instances** with the recent Apollo
  lunar landings, 13 rows × 5 cols, Tier B manual_wikipedia,
  source_count=1) staged this session 2026-05-12 → **reached Ready
  2026-05-12 after a 5-cycle audit arc**. Original instance (no
  seed) filling the `causal` category gap. Universe: 13 fatal
  incidents from Wikipedia 'List of spaceflight-related accidents
  and incidents' master article's Tables 0+1+2 (1+4+8 = 13 events,
  1961 Bondarenko through 2014 VSS Enterprise PF04). Schema:
  Incident PK + Year, Mission or Program, Proximate Cause,
  Fatalities. Brings `causal` from gap 4 to **gap 3** (2 of 5).
  Cycle history theme: three of the five cycle BLOCKINGs were
  single-source-rigor violations where per-incident NTSB-report or
  per-person-Wikipedia-article details leaked into staged
  Proximate Cause cells when source_urls only declared the master
  list (Lawrence cycle-2 'late flare'; Lawrence cycle-3 wording;
  VSS Enterprise cycle-4 'co-pilot/unlocked/boost'); cycle-3 also
  caught a separate factual error (Soyuz 11 'orbital module
  separation' should be 'service module separation'); cycle-5
  closed with a residual stale 'premature unlock' example in
  normalization_decisions. Useful reference: single-source
  instances need cell-by-cell wording-match-to-source verification
  at Stage-1 staging time, not patched in 4+ cycles after audit
  pressure. Counts disambiguation (cycles 3-4): fatalities = count
  of deaths; fatal incidents = count of events. 1967 cluster is 5
  fatal incidents totalling 7 deaths.
- `us_presidents_count_by_birth_state` (aggregation, History/Politics)
  was REMOVED from `cfg.json` registered set (now 10 trial_* only)
  and remains as a staged draft — appears to be a parallel-agent
  remediation pass where the artifact was de-registered pending
  curation rework. Counted as a single staged instance below (no
  longer a duplicate of a registered entry).
- `modern_animal_extinctions_since_1900` (causal,
  **Science/Conservation NEW subdomain** — Science family extends
  from 7 to 8 subdomains; 22 rows × 5 cols; Tier B manual_wikipedia,
  source_count=24: 2 master Wikipedia articles + 22 per-taxon
  articles) **STAGED → Ready 2026-05-12 after a 4-cycle audit arc**.
  First athlete-keyed/animal-taxa-keyed instance in pkdb_bench
  (universe: 12 species + 6 subspecies + 4 regional populations of
  extinct tigers/lions). Brings `causal` from gap 3 to **gap 2** (3
  of 5 staged) — the first category to drop below gap-3 after
  multi_attribute_projection saturated at 8/8. Schema: Common Name
  PK + Scientific Name + Continent of Origin + Year of Last
  Confirmed Record (TEXT with one 'late 1930s' decade-only value for
  Bali tiger) + Primary Causes (text, semicolon-delimited, 13-value
  vocabulary). Cycle arc compressed: cycle-1 audit caught 4
  BLOCKING (claim_type overstated, master URLs don't ground
  Primary Causes, year non-source-faithful, schema/title says
  Species but includes subspecies); cycle-2 caught year-relation
  ambiguity (Caspian/Javan tigers wrong) + cause-cell grounding
  errors (Caribbean monk seal Hunting+Habitat loss → Overhunting+
  Overfishing per article) + taxa-vs-populations scope (4
  tiger/lion rows are populations not subspecies); cycle-3 caught
  Barbary lion year + Wake Island rail year + multi/single-cause
  count narrative drift; cycle-4 was hygiene-rename for column
  Year-of-Last-Confirmed-Sighting → Record. Useful reference:
  causal instances with multi-value Primary Causes need
  source-faithful cell-by-cell verification against per-taxon
  articles, not just master-list aggregation.
- `major_world_deserts_by_area` (geographic, **Geography/Deserts
  NEW subdomain** — Geography family extends to 10 instances; 18
  rows × 4 cols; Tier B manual_wikipedia, source_count=19: 1 master
  Wikipedia 'List of deserts by area' + 18 per-desert articles)
  **STAGED → Ready 2026-05-12 after a 3-cycle audit arc**. Original
  instance (no example_questions.txt seed) filling one of the last
  two gap-4 categories. NEW entity class: deserts (physical
  landforms — no current rows are keyed by physical-geography
  entities). Brings `geographic` from gap 4 to **gap 3** (2 of 5
  staged). Schema: Desert PK + Continent + Area (sq km) + Desert
  Type. Universe rule: deserts with area ≥ 250,000 sq km per the
  master list (aggregate Great Australian dropped in favour of its
  qualifying sub-deserts; Margo Desert excluded — below threshold
  and lacks Wikipedia article). Continent: 6-continent + Antarctica
  with largest-land-share rule for transcontinental Arctic. Desert
  Type uses a normalized 5-value schema subset of the master
  article's broader classification (which also uses compound labels
  like Cool coastal and Tropical/subtropical for below-threshold
  entries). Cycle arc: cycle-1 caught continent count off-by-one
  + enum/master-vocab framing + master-floor description + title
  aggregate-policy surfacing; cycle-2 caught temporal_note enum
  residue + master '31 entries ≥ 100K' overclaim + remaining 'rank
  5' references; cycle-3 reframed 5-value set as 'normalized schema
  subset' across 5 active fields. Useful reference: source_defined
  geographic instances need explicit aggregate-vs-components
  conventions documented at Stage-1; multi-continent / dual-
  definition entities (Arctic Desert master 13.9M vs ecoregion
  161K) need committed canonical-source rule.
- `tennis_players_with_more_than_5_grand_slam_singles_titles`
  (comparative, **Sports/Tennis NEW subdomain** — Sports family
  extends from 2 to 3 instances, joining Olympic host cities; 119
  rows × 5 cols; Tier B manual_wikipedia, source_count=4: 2 master
  Grand Slam championship lists + 2 Open Era records pages)
  **STAGED → Ready 2026-05-12 after a 5-cycle audit arc (cycle-6
  cleanup folded in)**. **First athlete-keyed instance in
  pkdb_bench** — proves the comparator primitive beyond
  country-keyed entities (existing
  `countries_with_more_than_5_fields_medalists` is country-keyed).
  Brings `comparative` from gap 4 to **gap 3** (2 of 5 staged) —
  clears the last gap-4 category. Schema: Player PK + Grand Slam
  Singles Titles (numeric) + Tour (Men's/Women's) + Nationality +
  Qualifies (>5) boolean. Universe: 119 unique Open Era Grand Slam
  singles champions (58 men + 61 women); 24 qualifiers (>5 strict).
  Cross-era handling: Court 11 (of 24 lifetime), King 8 (of 12),
  Laver 5, Rosewall 4, Newcombe 5. Cycle arc was long: cycle-1
  audit caught women's-universe-incomplete (missing Azarenka, Li
  Na/Krejčíková/Rybakina undercounted) + temporal cutoff
  inconsistent (men through 2026 AO vs women through 2025 US
  Open) + Vondroušová diacritic violation; cycle-2 caught
  db_analog pre-Open predicate + multiple stale narrative residues
  + Rybakina prose still 'only title' + flag-grounded provenance
  not propagated + ASCII Marketa Vondrousova in open_questions;
  cycle-3 caught Kriek 1-1 flag split not derivable under
  majority-of-wins (introduced chronologically-first sub-tiebreaker)
  + source role line still '60 women' + 2025-US-Open residue +
  article-foregrounding nationality prose + King's pre-Open 1968
  AO incorrectly counted; cycle-4 Ready verdict with 3 HYG;
  cycle-5 cleanup applied chronologically-first to
  source_coverage_notes/open_questions + Graf/Becker
  flag-normalization (successor-state rule) + 'April 1968'
  imprecise wording → '1968 French Open onwards'. Useful
  reference: comparative instances with large universes (60+
  rows) require explicit majority-of-wins + tiebreaker rules for
  every flag-conflict case at Stage-1 staging, including 1-1
  splits and successor-state transitions; cell-by-cell
  flag-grounding cross-check between master tournament listings
  and Open Era records pages is mandatory.
- `major_music_genres_landmark_albums` (qualitative,
  **Culture/Music subdomain — second Music-keyed instance** joining
  `notable_works_of_recent_nobel_literature_laureates`; 10 rows × 4
  cols; Tier B manual_wikipedia, source_count=20: 10 per-genre
  Wikipedia articles + 10 per-album articles) **STAGED 2026-05-12 →
  Ready 2026-05-13 after a 4-cycle paired curator/audit arc**.
  NEW entity class: music genres × representative albums × artists.
  Brings `qualitative` from gap 3 to **gap 2** (3 of 5 staged).
  Schema: Genre PK + Landmark Album + Artist + Year of Release.
  Universe: 10 curator-selected major genres (Jazz 1959, Country
  1962, Rock 1967, Electronic 1968, Heavy metal 1970, Soul 1972,
  Punk rock 1976, Hip hop 1986, Grunge 1991, Pop 2007). 6 genres
  excluded under the 'single studio or live album' rule (Blues,
  Folk, Reggae, Funk, Disco, R&B — full per-genre rationale in
  proposed_supplements). Useful reference: qualitative instances
  with article-foregrounding selection rules need genre-by-genre
  source verification at Stage-1; expansion targets in adjacent
  genres often fail the strict 'single studio or live album' rule
  when the genre predates the LP era or is singles-driven. Also
  the first instance in this benchmark to fully follow the new
  paired-cycle numbering + 0/0/0 terminal-confirmation workflow
  documented in `agent_handoff_bundle.md` (Default: Stage → Audit
  section).
- `twelve_olympians_greek_to_roman` (factual_lookup,
  **Religion/Mythology NEW subdomain** — Religion family extends
  from 1 to 2 subdomains, joining Religion/Holy Sites per
  `domain_taxonomy.md`; 13 rows × 4 cols; Tier B manual_wikipedia,
  source_count=14: 1 master 'Twelve Olympians' article + 13
  per-deity articles) **STAGED 2026-05-13 → Ready 2026-05-13 after
  a 5-cycle paired curator/audit arc terminating at 0/0/0**. NEW
  entity class: Greek deities / mythological figures (no other
  instance is keyed by mythological figures, gods, or legendary
  characters). Brings `factual_lookup` from gap 3 to **gap 2** (3
  of 5 staged at the time of this staging; subsequently dropped
  further to gap 1 via parallel-agent
  `oceanic_trenches_named_deeps`). Schema: Greek Deity PK + Roman Equivalent + Primary
  Domain + Primary Symbol. Universe: 13 Olympians enumerated in the
  master article's List section (Hestia AND Dionysus both included
  per the article's actual table; the master notes there is no
  single canonical list and the commonly-referenced 'Twelve' is the
  Hestia-or-Dionysus swap-pair). Hades explicitly excluded by the
  master article. Apollo is the only Olympian whose Roman name is
  identical to the Greek (Apollo = Apollo) — grounded by the master
  article's table + Apollo article's 'Roman Apollo' section, NOT by
  the infobox (which omits redundant Equivalents entries). Dionysus
  Roman = Liber per article (Bacchus is the popular alt name,
  flagged). Sort: Greek Deity ASC. Cycle arc: cycle-1 audit caught
  2 BLOCKING (canonical-list wording overclaim + Dionysus Primary
  Symbol from per-deity infobox not master-article foregrounding —
  Thyrsus → Grapevine) + 2 HYG (thin source coverage for
  Apollo/Hestia + missing factual_lookup hybrid-shape
  justification); cycle-2 caught 3 HYG (canonical-Roman-equivalent
  scrub residue + per-deity infobox overclaim + Culture/Mythology
  → Religion/Mythology per domain_taxonomy.md); cycle-3 caught 1
  HYG (summary prose still said 'via their infoboxes' in 4
  locations — softened to 'infobox or body text'); cycle-4 caught
  1 HYG (Apollo role line falsely claimed 'infobox confirms Roman
  = Apollo' — rewritten to acknowledge Apollo's infobox does NOT
  list a Roman equivalent and ground the value in master table +
  Roman Apollo section); cycle-5 0/0/0 terminal. Useful reference:
  factual_lookup instances with edge-case entity rows (Apollo
  same-name-in-Roman) require explicit Stage-1 documentation that
  the canonical source-grounding for that row is NOT a per-deity
  infobox field; 3 of 5 hygiene cycles in this arc came from
  propagating the Apollo edge-case provenance through summary and
  lessons-applied prose.
- `notable_robotic_spacecraft_missions` (multi_hop, **Science/Space
  Exploration existing subdomain — third Space-Exploration
  instance** joining `apollo_lunar_landing_mission_crews` and
  `crewed_spaceflight_fatal_incidents`; Science family extends from
  10 to 11 instances; 22 rows × 4 cols; Tier B manual_wikipedia,
  source_count=23: 1 master 'Timeline of artificial satellites and
  space probes' + 22 per-mission articles) **STAGED 2026-05-13 →
  Ready 2026-05-13 after a 3-cycle paired curator/audit arc
  terminating at 0/0/0**. NEW entity-class extension: robotic
  (uncrewed) spacecraft missions — Apollo crewed sibling-instance
  is explicitly excluded; this instance covers Sputnik 1 (first
  artificial satellite) through James Webb (2021 L2 telescope).
  Brings `multi_hop` from gap 3 to **gap 2** (3 of 5 staged).
  Schema: Mission PK + Launch Year + Destination + Outcome.
  Universe: 22 curator-selected missions spanning Space Race
  (1957-1972, 8 rows), post-Apollo (1975-1996, 6 rows), modern
  (1997-2021, 8 rows). Destination dist: Mars 8, Outer Solar
  System 3, Earth Orbit 2, Moon 2, Venus 2, Jupiter 2, Saturn 1,
  Sun 1, Sun-Earth L2 1. Outcome dist: Success 14, Ongoing 8.
  Cycle arc: cycle-1 audit caught Venera 7 outcome (Partial
  success staged based on tipped-lander reading; master timeline
  and per-mission article lead both mark Success — corrected);
  cycle-2 audit caught proposed_supplements still listing
  'Partial success' + same-session copy-paste residue from prior
  `twelve_olympians_greek_to_roman` instance ('per-deity article
  lead' / 'mythologically' wording leaked into spacecraft-mission
  narrative); cycle-3 0/0/0 terminal. Useful reference: rapid
  same-session staging needs grep-for-entity-class-specific-words
  before audit handoff — copy-paste residue is a real failure
  mode. Also: `proposed_supplements` field needs re-verification
  after every CSV row change, as cycle-2 fix to CSV didn't
  propagate to proposed_supplements (became cycle-3 HYG).

| Category | Target | Registered | Staged | Total | Gap |
| --- | --- | ---: | ---: | ---: | ---: |
| `factual_lookup` | 5 | 0 | 6 | 6 | **0** (1 over target) |
| `set_filter` | 5 | 0 | 5 | 5 | **0** ✓ SATURATED |
| `implicit_join` | 8 | 2 | 8 | 10 | **0** ✓ SATURATED (2 over target) |
| `aggregation` | 5 | 0 | 6 | 6 | **0** ✓ SATURATED (1 over target) |
| `top_k` | 5 | 0 | 7 | 7 | **0** (2 over target) |
| `set_operation` | 8 | 6 | 5 | 11 | **0** ✓ SATURATED (3 over target) |
| `multi_hop` | 5 | 0 | 6 | 6 | **0** ✓ SATURATED (1 over target) |
| `qualitative` | 5 | 0 | 6 | 6 | **0** ✓ SATURATED (1 over target) |
| `temporal` | 5 | 0 | 7 | 7 | **0** ✓ SATURATED (2 over target) |
| `geographic` | 5 | 0 | 5 | 5 | **0** ✓ SATURATED |
| `causal` | 5 | 0 | 6 | 6 | **0** ✓ SATURATED (1 over target) |
| `comparative` | 5 | 0 | 6 | 6 | **0** ✓ SATURATED (1 over target) |
| `hierarchical_decomposition` | 5 | 0 | 6 | 6 | **0** ✓ SATURATED (1 over target) |
| `multi_attribute_projection` | 8 | 2 | 8 | 10 | **0** ✓ SATURATED (2 over target) |
| `scaled_enumeration` | 5 | 0 | 6 | 6 | **0** (1 over target) |
| **Subtotal (categorized)** | **84** | **10** | **93** | **103** | **0** |
| _uncategorized staged_ | _—_ | _—_ | _0_ | _0_ | _—_ |
| **Total unique instances on disk** | _—_ | **10** | **93** | **103** | _—_ |

Planning coverage, counting registered + staged categorized unique
instances: ~95% of the 84-instance target (80/84). **TEN categories
FULLY CLEARED at target** (`factual_lookup` 6/5 + `top_k` 6/5 +
`multi_hop` 5/5 + `qualitative` 6/5 + `multi_attribute_projection` 8/8 +
`set_operation` 8/8 + `set_filter` 5/5 + `causal` 5/5 +
`implicit_join` 9/8 + `scaled_enumeration` 6/5). **Zero gap-4 categories remaining**.
**Gap-3 categories (0)** — benchmark MILESTONE: zero remaining gap-3 categories; uniform-gap-2-or-better state across all 15 categories.
**Gap-2 / Gap-1 categories (0)** — **🎯 15/15 ABSOLUTE-SATURATION MILESTONE ACHIEVED THIS SNAPSHOT.** `geographic` saturated by `top_15_most_produced_crops_by_global_production` (14th fully-cleared); `aggregation` saturated by parallel-agent `scotus_appointment_rows_by_president_mckinley_through_biden` (15th and FINAL fully-cleared). Zero categories at any gap level. Total of 79 categorized staged + 1 uncategorized = 80 staged instances on disk + 10 registered = 90 unique total.
**Five categories over target**: `factual_lookup` (6/5), `top_k` (6/5),
`qualitative` (6/5), `implicit_join` (9/8), `scaled_enumeration` (6/5).
The ten saturated categories at-or-over target — new instances in
these ten should NOT be prioritized unless the seed is exceptional.

### Seed coverage of `example_questions.txt`

23 seeds total. Clear touches or close adaptations: **18** (#1, #2,
#3, #4, #5, #7, #9, #10, #11, #14, #15, #17, #18, #19, #20, #21, #22, #23). The
film-by-decade staged instance is also structurally similar to #20 but
is a separate instance.

Untouched: **5** (#6, #8, #12, #13, #16). Of these, three
(#6, #8, #12) fail the fast viability gate (empty or sub-row-gate
result sets at strict reading) and #13 has weak source grounding
(subjective top-3-employers ranking with no per-state authoritative
source). The effective remaining seed pool is **1** (#16, South
America countries × attrs) plus reshapeable variants of #6, #8,
#12, #13. Seeds #17 and #18 were both staged this session
(`fifty_us_states_attributes` and
`twentieth_century_us_presidents_attributes`, both Ready
2026-05-11). Note: #16's category would be
`multi_attribute_projection`, which is now FULLY CLEARED at 8/8 —
adding #16 would push the category to 9/8 (over target).

`tables/` also contains two SPARQL prototype CSVs that map onto seeds
but are not registered (per `curation_protocol.md` §2):
`france_germany_border_non_french_german.csv` (#8, 4 rows — sub-floor)
and `nobel_physics_and_chemistry.csv` (a Physics-and-Chemistry
double-laureate intersection — different reading from the staged
`physicists_who_were_nobel_laureates` instance for seed #9, which uses
the wider physicists ∩ all-Nobels intersection that clears the row-count
gate at 285 rows).

Note that several touched-seed instances are adaptations rather than
verbatim takes: #1 was adapted to a per-continent
top-3-by-continent shape and labelled `scaled_enumeration`; #4 was
adapted from "top 3 dishes high in vitamin C" to "6 vitamins × 5 food
sources" to clear the row-count gate; #7 was adapted from "10 most
populous cities in Asia" (single partition) to top 10 per populated
continent (six partitions); #19 was adapted from "5 most influential
scientists per discipline" to "5 notable foundational scientists for 5
fixed disciplines". Seed #2 was taken verbatim with two added attribute
columns (Continent, Population) to satisfy the protocol requirement of
≥1 numeric or categorical attribute beyond the key. All adaptations are
documented in their respective `decision_log`.

### Domain distribution (across all 69 unique instances on disk: 59 staged + 10 registered)

Domain families below are planning labels. Use `domain_taxonomy.md`
when assigning or repairing `draft.json::domain`. Counts are
derived from the per-instance entity-class table above (every
instance contributes exactly one domain family).

| Domain family | Instances |
| --- | ---: |
| Geography | 12 |
| Science | 12 |
| Culture | 12 |
| Politics | 8 |
| History | 4 |
| Technology | 4 |
| Business | 3 |
| Sports | 3 |
| Religion | 3 |
| Transportation | 3 |
| Education | 2 |
| Health | 2 |
| Society | 2 |
| Environment | 1 |
| Food | 1 |
| Law | 1 |

Geography and Science are tied as the largest families at 11
instances each. **All 16 domain families now have at least one
instance**. Recent family extensions include: Geography 11 (Oceans
NEW from parallel-agent `oceanic_trenches_named_deeps`, Deserts
NEW from `major_world_deserts_by_area`), Technology 3 (Civil
Engineering NEW from `notable_bridges_by_main_span`), Religion 2
(Mythology NEW from `twelve_olympians_greek_to_roman`,
joining Holy Sites). The family-diversity goal is met; future
staging can focus purely on category balance, entity-class
diversity, and seed coverage.

**Entity-type diversity** is the lone remaining diversification
axis. See the **Entity-class inventory** subsection immediately
below for the canonical per-instance entity-class table and
rollup; that table is the source of truth for entity-class
saturation checks at Stage-0. As of 2026-05-15 the most-saturated
buckets are `countries` (14 of 69) and `people/*` (12 of 69); the
most-attractive under-represented buckets are
`cities-and-capitals` (1), `time-periods/*` (1),
`abstract-categories/*` (4), `taxonomic-entities/*` (3),
`engineered-structures/*` (4), `organizations/*` (4),
`physical-features/*` (7), and `events/*` (10 — extended this
snapshot with NEW `empires` fine-class).

### Entity-class inventory

Every instance has a primary entity class — the dominant entity
the primary key identifies. Tracking entity class explicitly
prevents two failure modes the benchmark has already hit:
(a) proposing a candidate whose entity class is already
over-represented and (b) overlooking that a near-duplicate has
been staged (mountain ranges proposed on 2026-05-14 as a "new"
entity class when `top_5_highest_mountains_per_continent` already
covers mountain peaks — caught at proposal time, not staging).

**Classification policy.** For a single-column PK, the entity class
is unambiguously the PK column's entity type. For composite PKs, the
rule is **answer-entity wins**:

- **Scaffold + item PK** (one column is a structural axis like
  `Continent`, `Decade`, `Year`, `Rank`): entity = the item
  enumerated within partitions, inferred from `table_title`.
  Examples: `(Continent, Rank)` for `top_5_highest_mountains_per_continent`
  → `physical-features/mountains`; `(Decade, Rank)` for
  `top_grossing_films_worldwide_per_decade_1980s_2010s` →
  `artifacts/films`.
- **Entity + item PK** (one entity column is the partition, second
  is the answer): entity = the second/item column.
  Examples: `(Movement, Painter)` → `people/painters`;
  `(Justice, Case)` → `events/judicial-decisions`;
  `(Mission, Astronaut)` → `people/astronauts`;
  `(Planet, Moon)` → `taxonomic-entities/moons`;
  `(Discipline, Scientist)` → `people/scientists`;
  `(Decade, Milestone)` → `events/inventions`.
- **Documented subject-wins exceptions** — for tables where the
  partition column is itself a closed-enumeration entity-of-interest
  and the answer entity has no clean classification bucket, classify
  by the subject. Two staged instances use this exception, both
  noted inline in the per-instance table:
  `vitamins_top5_food_sources` (kept as
  `taxonomic-entities/nutrients` — vitamins is the closed-vocab
  subject; "foods" is too broad to be useful for diversity tracking)
  and `world_religions_top5_holy_sites` (kept as
  `abstract-categories/religions` — religions is the closed-vocab
  subject; holy-sites is a one-off fine-class).

The answer-entity rule is justified by the duplicate-detection use
case: if a future candidate proposes "Soviet space missions × crew",
the duplicate risk is against `apollo_lunar_landing_mission_crews`
under the `people/astronauts` classification, NOT under a
`events/space-missions` classification (which would only catch
mission-keyed candidates).

**Controlled vocabulary** (use the `bucket/fine-class` pattern,
like the `Family/Subdomain` pattern in `domain`):

| Bucket | Fine-classes used in current corpus |
|---|---|
| `countries` | — (single bucket; PK = country/NOC) |
| `subnational-regions` | `us-states` |
| `cities-and-capitals` | — |
| `people` | `politicians`, `scientists`, `athletes`, `painters`, `philosophers`, `laureates`, `deities`, `honorees`, `astronauts` |
| `physical-features` | `mountains`, `rivers`, `deserts`, `islands`, `oceanic-trenches`, `lakes` |
| `engineered-structures` | `bridges`, `buildings`, `dams`, `airports` |
| `events` | `space-missions`, `space-incidents`, `epidemics`, `financial-crises`, `judicial-decisions`, `olympics`, `awards`, `inventions`, `battles`, `empires` |
| `artifacts` | `films`, `songs`, `video-games`, `chemical-elements` |
| `abstract-categories` | `music-genres`, `religions`, `languages`, `philosophical-schools`, `architectural-styles`, `chemical-groups` |
| `taxonomic-entities` | `moons`, `species`, `nutrients`, `stars` |
| `organizations` | `universities`, `companies`, `space-agencies`, `ports`, `sports-clubs` |
| `time-periods` | `decades` |

**Rollup across all 69 unique instances (registered + staged):**

| Entity bucket | Count | Fine-class breakdown |
|---|---:|---|
| `countries` | 14 | (single bucket — most-represented entity class) |
| `people/*` | 17 | laureates 3, scientists 2, authors 2 (Booker + Pulitzer), athletes 1, painters 1, philosophers 1, deities 1, honorees 1, politicians 1, astronauts 1, diplomats 1, judges 1, monarchs 1 (NEW from `english_and_british_monarchs_since_1066_by_accession_year`) |
| `events/*` | 12 | space-missions 1, financial-crises 1, epidemics 1, space-incidents 1, judicial-decisions 1, olympics 1, awards 1, inventions 1, battles 1, empires 1, recessions 1, pandemics 1 (NEW from parallel-agent `epidemics_and_pandemics_with_at_least_1_million_deaths`) |
| `physical-features/*` | 9 | rivers 2, mountains 1, deserts 1, islands 1, oceanic-trenches 1, lakes 1, volcanoes 1, ecoregions 1 (NEW from `global_200_tropical_moist_broadleaf_forest_ecoregions_by_realm`) |
| `artifacts/*` | 5 | films 2, chemical-elements 1, songs 1, video-games 2 (extended by `top_3_best_selling_titles_per_top_5_lifetime_consoles`) |
| `subnational-regions/us-states` | 4 | — |
| `abstract-categories/*` | 7 | music-genres 1, religions 2, languages 1, philosophical-schools 1, architectural-styles 1, chemical-groups 1, causes-of-death 1 (NEW from `who_global_top_15_leading_causes_of_death_2021`) |
| `engineered-structures/*` | 5 | bridges 1, buildings 1, dams 1, airports 1, canals 1 (NEW from `notable_ship_canals_by_length_km`) |
| `taxonomic-entities/*` | 6 | moons 1, species 1, nutrients 1, stars 1, asteroids 1, dinosaurs 1 (NEW from `largest_sauropods_by_estimated_mass`) |
| `organizations/*` | 6 | universities 2 (extended by `russell_group_universities_attributes`), companies 1, space-agencies 1, ports 1, sports-clubs 1 |
| `time-periods/*` | 2 | decades 1, geological-periods 1 (NEW from `phanerozoic_periods_by_start_age` — extends most-under-represented entity bucket from 1 to 2 instances) |
| `agricultural-commodities/*` | 1 | crops 1 (NEW bucket + NEW fine-class from `top_15_most_produced_crops_by_global_production`; brings pkdb_bench from 12 to 13 entity buckets) |
| `vehicles/*` | 1 | aircraft 1 (NEW bucket + NEW fine-class from `wide_body_aircraft_by_model_attributes`; brings pkdb_bench from 13 to 14 entity buckets) |
| `cities-and-capitals` | 2 | national-capitals 1 (just added — `sovereign_state_capitals_on_rivers`) |

**Saturated entity classes** (avoid unless category-gap forces it):
`countries` (14), `people/laureates` (3), `people/scientists` (2 —
mid-light, watch).
**Under-represented** (prefer for tie-breaks):
`time-periods/decades` (1),
`organizations` (2),
`engineered-structures` (3),
`taxonomic-entities` (5 — was 3 pre-2026-05-17;
extended in two back-to-back cycles by
`three_brightest_stars_in_each_zodiac_constellation` (NEW `stars`)
then `largest_asteroids_with_geometric_mean_diameter_above_250_km`
(NEW `asteroids`); 5 instances now puts it at mid-light — watch
before extending further),
`cities-and-capitals` (2 — was 1 pre-2026-05-16,
extended by `sovereign_state_capitals_on_rivers` adding
NEW `national-capitals` fine-class),
`abstract-categories` (6 — mid-light, watch; was 5 pre-2026-05-16,
extended by `periodic_table_groups_first_three_elements_by_atomic_number`
adding NEW `chemical-groups` fine-class), and any
fine-class with 0 instances.

**Per-instance entity-class table** (canonical inventory — update
when a new instance reaches Ready or when `cfg.json` changes):

| ID | Category | Domain | Entity class | Rows |
|---|---|---|---|---:|
| `aau_big_ten_intersection_universities` | set_operation | Education/Higher Education | organizations/universities | 17 |
| `active_year_round_research_stations_in_antarctica` | set_filter | Science/Polar Research (NEW subdomain) | engineered-structures/antarctic-research-stations (NEW fine-class) | 43 |
| `booker_prize_for_fiction_winners_2000_2024` | implicit_join | Culture/Literature | people/authors (composite PK (Year, Author) per answer-entity-wins rule: Year is scaffold partition, Author is item entity; NEW fine-class) | 26 |
| `pulitzer_prize_for_fiction_winners_2000_2024` | implicit_join | Culture/Literature | people/authors (composite PK (Year, Author) per answer-entity-wins rule; 2nd people/authors instance joining Booker) | 24 |
| `apollo_lunar_landing_mission_crews` | multi_hop | Science/Space Exploration | people/astronauts | 18 |
| `chemical_elements_discovered_1869_1945` | factual_lookup | Science/Chemistry | artifacts/chemical-elements | 31 |
| `countries_over_50pct_renewable_electricity` | set_filter | Environment/Energy | countries | 66 |
| `countries_that_drive_on_the_left` | set_filter | Transportation/Road Traffic | countries | 54 |
| `countries_with_more_than_5_fields_medalists` | aggregation (re-categorized 2026-05-21 corpus-review: db_analog is COUNT GROUP BY HAVING≥5) | Science/Mathematics | countries | 11 |
| `f1_world_drivers_championship_titles_per_driver_at_least_2` | aggregation | Sports/Motorsport (NEW subdomain) | people/race-car-drivers (NEW fine-class) | 17 |
| `crewed_spaceflight_fatal_incidents` | causal | Science/Space Exploration | events/space-incidents | 13 |
| `decisive_battles_of_world_war_ii` | multi_hop | History/Military | events/battles | 18 |
| `eu_member_states_accession_years` | factual_lookup | Politics/European Union | countries | 27 |
| `eurozone_adoptions_1999_2023` | temporal (categorized 2026-05-21 corpus-review: year-window scope is load-bearing; mirror Olympics-hosts precedent) | Politics/Economic Integration | countries | 20 |
| `fifty_us_states_attributes` | multi_attribute_projection | Politics/Government | subnational-regions/us-states | 50 |
| `foundational_painters_of_major_modern_art_movements` | hierarchical_decomposition | Culture/Visual Arts | people/painters | 18 |
| `g20_countries_attributes` | multi_attribute_projection | Business/Economics | countries | 19 |
| `landlocked_africa_neighbor_languages` | multi_hop | Geography/Linguistics | countries | 225 |
| `landmark_us_supreme_court_decisions_by_chief_justice` | implicit_join | Law/Judiciary | events/judicial-decisions | 115 |
| `largest_islands_by_area_top20_britannica` | top_k | Geography/Islands | physical-features/islands | 20 |
| `largest_lakes_top20_by_area` | top_k | Geography/Lakes | physical-features/lakes | 20 |
| `major_architectural_styles_landmark_buildings` | qualitative | Culture/Architecture | abstract-categories/architectural-styles (subject-wins exception per policy: single PK=Architectural Style, answer-entity would be `buildings` but kept as the closed-vocab subject `architectural-styles`; NEW fine-class) | 16 |
| `major_b2c_online_learning_platforms` | multi_attribute_projection | Education/EdTech | organizations/companies | 13 |
| `major_government_space_agencies` | factual_lookup | Science/Space Exploration | organizations/space-agencies | 24 |
| `major_modern_epidemics_and_pandemics_1918_2025` | causal | Health/Epidemiology | events/epidemics | 16 |
| `major_music_genres_landmark_albums` | qualitative | Culture/Music | abstract-categories/music-genres | 10 |
| `major_religious_traditions_foundational_texts` | qualitative | Religion/Texts | abstract-categories/religions (subject-wins exception per policy: single PK=Religion, answer-entity would be `foundational-texts` but kept as the closed-vocab subject `religions`; 2nd instance with this fine-class joining `world_religions_top5_holy_sites`) | 12 |
| `major_historical_empires_founder_capital_century` | multi_hop | History/Empires | events/empires | 17 |
| `major_western_philosophers_primary_works` | qualitative | Culture/Philosophy | people/philosophers | 16 |
| `major_world_deserts_by_area` | geographic | Geography/Deserts | physical-features/deserts | 18 |
| `middle_east_countries_capital_population` | geographic | Geography/Politics | countries | 17 |
| `modern_animal_extinctions_since_1900` | causal | Science/Conservation | taxonomic-entities/species | 22 |
| `modern_financial_crises_1929_2025` | causal | Business/Economics | events/financial-crises | 27 |
| `nato_secretaries_general_birth_country_terms` | implicit_join | Politics/International Relations | people/diplomats (single PK = Secretary-General; NEW fine-class within people bucket) | 13 |
| `nato_accessions_1999_2024_capitals` | implicit_join | Politics/International Relations | countries | 16 |
| `nobel_categories_2020_2024_laureates` | multi_attribute_projection | Science/Academia | people/laureates | 62 |
| `notable_bridges_by_main_span` | top_k | Technology/Civil Engineering | engineered-structures/bridges | 17 |
| `notable_robotic_spacecraft_missions` | multi_hop | Science/Space Exploration | events/space-missions | 22 |
| `notable_works_of_recent_nobel_literature_laureates` | implicit_join | Culture/Literature | people/laureates | 125 |
| `oceanic_trenches_named_deeps` | factual_lookup | Geography/Oceans | physical-features/oceanic-trenches | 17 |
| `decade_volcanoes_by_continent` | geographic | Geography/Volcanology | physical-features/volcanoes (NEW fine-class extending physical-features bucket from 7→8) | 16 |
| `phanerozoic_periods_by_start_age` | temporal | Science/Geology | time-periods/geological-periods (NEW fine-class extending time-periods bucket from 1→2 — most-under-represented bucket extension in corpus) | 12 |
| `clubs_with_at_least_2_european_cup_and_uefa_champions_league_titles` | aggregation | Sports/Football | organizations/sports-clubs (NEW fine-class extending organizations bucket from 4 to 5; single PK = Club; Titles is the pre-aggregated COUNT cell) | 13 |
| `nber_dated_us_recessions_since_1929` | temporal | Business/Economics | events/recessions (NEW fine-class extending events bucket from 10→11; benchmark window peak ≥ August 1929) | 15 |
| `major_philosophical_schools_founders_origins` | qualitative | Culture/Philosophy | abstract-categories/philosophical-schools | 18 |
| `skyscrapers_at_least_500_meters_worldwide` | comparative | Technology/Civil Engineering | engineered-structures/buildings | 11 |
| `periodic_table_groups_first_three_elements_by_atomic_number` | hierarchical_decomposition | Science/Chemistry | abstract-categories/chemical-groups (NEW fine-class extending bucket from 5→6; composite PK (Group, Element)) | 54 |
| `physicists_who_were_nobel_laureates` | set_operation | Science/Physics | people/scientists | 285 |
| `planets_named_moons` | multi_attribute_projection | Science/Astronomy | taxonomic-entities/moons | 137 |
| `unesco_world_heritage_mixed_sites_attributes` | set_operation | Culture/Heritage (NEW subdomain) | heritage-properties/world-heritage-sites (NEW bucket — pkdb_bench grows 14→15 entity buckets; NEW fine-class) | 41 |
| `plays_winning_both_pulitzer_drama_and_tony_best_play` | set_operation | Culture/Theater (NEW subdomain) | artifacts/plays (NEW fine-class extending artifacts bucket 4→5) | 19 |
| `top_10_best_selling_automobiles_by_cumulative_units_sold` | implicit_join | Transportation/Automotive (NEW subdomain) | vehicles/automobiles (NEW fine-class extending vehicles bucket 1→2) | 10 |
| `us_presidents_who_also_served_as_vice_president` | set_operation | History/Politics (existing) | people/politicians (existing fine-class extension) | 15 |
| `politically_unstable_countries_per_continent_fsi_alert` | causal | Politics/Geopolitics | countries | 29 |
| `programming_languages_by_decade_of_first_appearance` | aggregation | Technology/Programming | time-periods/decades | 14 |
| `scientific_disciplines_widely_cited_scientists` | scaled_enumeration | Science/History of Science | people/scientists | 25 |
| `summer_olympics_hosts_1960_2020` | temporal | Sports/Olympics | events/olympics | 16 |
| `tennis_players_with_more_than_5_grand_slam_singles_titles` | comparative | Sports/Tennis | people/athletes | 119 |
| `time_person_of_the_year_2000_2024` | temporal | Society/Media | people/honorees | 25 |
| `top3_languages_per_continent` | scaled_enumeration | Society/Languages | abstract-categories/languages | 18 |
| `sovereign_state_capitals_on_rivers` | implicit_join | Geography/Cities | cities-and-capitals/national-capitals (NEW fine-class extending bucket from 1→2 instances) | 56 |
| `three_brightest_stars_in_each_zodiac_constellation` | hierarchical_decomposition | Science/Astronomy | taxonomic-entities/stars (NEW fine-class extending bucket from 3→4; composite PK (Constellation, Star) with Apparent Magnitude as ranking attribute) | 36 |
| `largest_asteroids_with_geometric_mean_diameter_above_250_km` | comparative | Science/Astronomy | taxonomic-entities/asteroids (NEW fine-class extending bucket from 4→5; single PK = Asteroid (Number+Name); Geometric Mean Diameter (km) is the ranking attribute) | 15 |
| `top_10_most_populous_cities_per_continent` | hierarchical_decomposition (re-categorized 2026-05-21 corpus-review: 6 continents × 10 cities partitioned top-K with composite PK Continent+Rank) | Geography/Cities | cities-and-capitals | 60 |
| `top_5_highest_mountains_per_continent` | hierarchical_decomposition | Geography/Mountaineering | physical-features/mountains | 36 |
| `top_3_best_selling_titles_per_top_5_lifetime_consoles` | hierarchical_decomposition | Culture/Gaming | artifacts/video-games (2nd video-games-keyed instance joining `trial_union_5`; composite PK (Console, Title)) | 15 |
| `top_3_highest_grossing_films_per_decade_1980s_to_2020s` | hierarchical_decomposition | Culture/Film | artifacts/films (composite PK (Decade, Rank Within Decade); non-continent scaffold) | 15 |
| `global_200_tropical_moist_broadleaf_forest_ecoregions_by_realm` | geographic | Environment/Conservation | physical-features/ecoregions (NEW fine-class extending physical-features bucket from 8→9; 51-entry WWF Global 200 TMBF closed-canonical universe; semicolon-delim child cells) | 51 |
| `uiaa_recognized_eight_thousanders` | comparative | Geography/Mountaineering | physical-features/mountains (2nd mountains-keyed instance joining `top_5_highest_mountains_per_continent`; single PK = Mountain; saturates comparative at 5/5 as 12th fully-cleared) | 14 |
| `united_states_chief_justices_by_tenure_start_year` | temporal | Law/Judiciary | people/judges (NEW fine-class extending people bucket from 15→16; completed-tenure office-holder universe; saturates temporal as 13th fully-cleared) | 16 |
| `top_15_most_produced_crops_by_global_production` | geographic | Food/Agriculture | agricultural-commodities/crops (NEW bucket + NEW fine-class; FAOSTAT 2024 top-15 universe; web_search_faostat gt_construction; saturates geographic as 14th fully-cleared) | 15 |
| `scotus_appointment_rows_by_president_mckinley_through_biden` | aggregation | Law/Judiciary | (parallel-agent staged; saturates aggregation at 5/5 as the 15th and FINAL fully-cleared category — completes the 15/15 absolute-saturation milestone) | (parallel-agent) |
| `who_global_top_15_leading_causes_of_death_2021` | top_k | Health/Public Health | abstract-categories/causes-of-death (NEW fine-class extending abstract-categories bucket from 7→8; gt_construction=web_search_who; chips Health crisis-light family 1→2) | 15 |
| `epidemics_and_pandemics_with_at_least_1_million_deaths` | comparative | Health/Epidemiology | events/pandemics (NEW fine-class extending events bucket from 11→12; parallel-agent staged; chips Health family 2→3) | (parallel-agent) |
| `english_and_british_monarchs_since_1066_by_accession_year` | temporal | History/Monarchy | people/monarchs (NEW fine-class extending people bucket from 16→17; 2-source union+filter; strict-Wikipedia-regular-row policy; Charles III incumbent excluded) | 42 |
| `decisive_battles_of_world_war_i` | multi_hop | History/Military | events/battles (parallel-agent staged; extends History/Military subdomain 1→2) | (parallel-agent) |
| `notable_ship_canals_by_length_km` | comparative | Transportation/Maritime | engineered-structures/canals (NEW fine-class extending engineered-structures bucket from 4→5; Wikipedia 'Ship canal' article 'Notable ship canals' table verbatim; curator-normalized Length DESC sort) | 11 |
| `largest_sauropods_by_estimated_mass` | top_k (re-categorized 2026-05-21 corpus-review: db_analog is ORDER BY rank LIMIT 10 with no threshold; closed-ranked list pattern) | Science/Paleontology | taxonomic-entities/dinosaurs (NEW fine-class extending taxonomic-entities bucket from 5→6; NEW Science/Paleontology subdomain; Wikipedia 'Largest and heaviest animals' table verbatim; text-range estimate cells; slash-combined PK entries; first text-range-estimate instance in pkdb_bench) | 10 |
| `major_industrial_disasters_with_primary_cause_and_death_toll` | causal | Technology/Industrial Safety | (parallel-agent staged; NEW Technology/Industrial-Safety subdomain) | (parallel-agent) |
| `russell_group_universities_attributes` | multi_attribute_projection | Education/Higher-Education | organizations/universities (existing fine-class extension 1→2; chips Education family 2→3 + chips multi_attribute_projection under raised target) | 24 |
| `wide_body_aircraft_by_model_attributes` | multi_attribute_projection | Transportation/Aviation | vehicles/aircraft (NEW entity bucket + NEW fine-class; pkdb_bench grows 13→14 entity buckets; Wikipedia 'Wide-body aircraft' comparison table verbatim with text-range/compound cells) | 15 |
| `top_grossing_films_worldwide_per_decade_1980s_2010s` | scaled_enumeration | Culture/Film | artifacts/films | 20 |
| `transboundary_rivers_by_continent_over_1000_km` | set_filter | Geography/Hydrology | physical-features/rivers | 33 |
| ~~`transboundary_rivers_by_continent_over_100_miles`~~ | _deprecated 2026-05-21 corpus-review — moved to `datasets/pkdb_bench/deprecated/`; soft-duplicate of `transboundary_rivers_by_continent_over_1000_km` which is canonical keeper_ | _N/A_ | _N/A_ | _N/A_ |
| `twelve_olympians_greek_to_roman` | factual_lookup | Religion/Mythology | people/deities | 13 |
| `twentieth_century_decade_inventions` | scaled_enumeration | Technology/Inventions | events/inventions | 50 |
| `twentieth_century_us_presidents_attributes` | multi_attribute_projection | History/Politics | people/politicians | 18 |
| `us_presidents_count_by_birth_state` | aggregation | History/Politics | subnational-regions/us-states | 21 |
| `films_winning_at_least_8_academy_awards` | aggregation | Culture/Film | artifacts/films (3rd films-keyed instance) | 15 |
| `us_states_warm_year_round_climate` | qualitative | Geography/Climate | subnational-regions/us-states | 23 |
| `vitamins_top5_food_sources` | scaled_enumeration | Food/Nutrition | taxonomic-entities/nutrients (subject-wins exception per policy: PK=(Vitamin, Food), answer-entity would be `foods` but kept as the closed-vocab subject `nutrients`) | 30 |
| `world_religions_top5_holy_sites` | scaled_enumeration | Religion/Holy Sites | abstract-categories/religions (subject-wins exception per policy: PK=(Religion, Site), answer-entity would be `holy-sites` but kept as the closed-vocab subject `religions`) | 20 |
| `worlds_busiest_airports_by_passenger_traffic` | top_k | Transportation/Aviation | engineered-structures/airports | 20 |
| `worlds_largest_container_ports_by_2024_teu` | top_k | Transportation/Logistics | organizations/ports | 20 |
| `worlds_tallest_dams_by_structural_height` | factual_lookup | Technology/Civil Engineering | engineered-structures/dams | 20 |
| `worlds_reigning_kingdoms` | set_filter | Politics/Government | countries | 17 |
| `trial_join_1` | implicit_join | Business/Economics | countries | (registered) |
| `trial_join_2` | implicit_join | Sports/Olympics | countries | (registered) |
| `trial_join_3` | multi_attribute_projection | Politics/Government | subnational-regions/us-states | (registered) |
| `trial_join_4` | set_operation | Culture/Film | artifacts/films | (registered) |
| `trial_join_5` | multi_attribute_projection | Health/Public Health | countries | (registered) |
| `trial_union_1` | set_operation | Culture/Film | events/awards | (registered) |
| `trial_union_2` | set_operation | Culture/Music | artifacts/songs | (registered) |
| `trial_union_3` | set_operation | Science/Academia | people/laureates | (registered) |
| `trial_union_4` | set_operation | Geography/Architecture | engineered-structures/buildings | (registered) |
| `trial_union_5` | set_operation | Culture/Gaming | artifacts/video-games | (registered) |

---

## Selection rubric applied to current state

**Top candidates for the next instance** (scored on the 4 axes —
Seed source, Category gap, Domain novelty, Entity-class novelty;
#1, #2, #3, #4, #5, #7, #9, #10, #11, #14, #15, #19, #20, #21, #22,
and #23 are now staged or registered; all 16 domain families have
at least one instance — domain novelty is no longer a category-level
discriminator, so Entity-class novelty does most of the
discriminating work):

| Seed | Category | Category gap | Likely domain | Domain novelty | Entity-class novelty | Score |
| --- | --- | ---: | --- | --- | --- | --- |
| _original gap-2 instance_ (`qualitative` or `multi_hop`, chips to gap-1 then saturates as 7th category) | varies | 2 | varies | mild | strong if under-rep bucket | ★★★ |
| _original gap-3 instance_ (6 categories: `implicit_join`, `aggregation`, `temporal`, `geographic`, `comparative`, `hierarchical_decomposition`) | varies | 3 | varies | mild | strong if under-rep bucket | ★★ |
| **#13** Top 3 employers per US state | `top_k`/`hierarchical_decomposition` | 2 | Business or Politics | overlap | overlap (subnational-regions/us-states already at 4) | ★ (subjective ranking) |
| **#16** South America countries × attrs | `multi_attribute_projection` (would push 8/8 → 9/8 over target) | 0 (saturated) | Geography | overlap (saturated) | overlap (`countries` at 14) | ✗ DOWN-PRIORITIZED |

Three seeds in `example_questions.txt` look attractive on paper but
**fail the fast viability gate** and should be reshaped or skipped:

- **#6** "Continents with more than 10 countries exceeding population
  50M": fewer than 10 rows under any honest reading (max 7
  continents). Fails the row-count gate without significant
  restructuring.
- **#8** "Countries bordering both France and Germany not speaking
  French or German": empty row set under strict reading (Belgium,
  Luxembourg, Switzerland all have French or German).
- **#12** "Pacific Northwest US states with capital cities over 2M":
  empty row set; PNW state capitals are all under 300k.

**No category is at gap 4 or gap 5 anymore.** The benchmark is now
in a purely gap-3 / gap-2 / saturated state (no gap-1 category
remains). Top priority for the next pick is one of the **3 gap-2
categories** (`qualitative`, `multi_hop`, `top_k`) — chipping any
of them to gap-1 then later to 5/5 would create a 6th fully-cleared
category. After gap-2, pick from the **6 gap-3 categories**
(`implicit_join`, `aggregation`, `temporal`, `geographic`,
`comparative`, `hierarchical_decomposition`). Within those, none
has a strong untouched seed in `example_questions.txt`; new fills
will need original questions or adapted seeds.
**Historical sketch list (factual_lookup, saturated this snapshot)** —
the prior snapshot's `factual_lookup` candidate sketches (world's
tallest dams, major space agencies, major straits) have been landed
or superseded: dams Ready 2026-05-14 as
`worlds_tallest_dams_by_structural_height`; space agencies staged
by parallel agent as `major_government_space_agencies`; the
straits sketch remains a future-cycle candidate but is no longer
high-priority because factual_lookup is over-target. Forward
priority for entity-class diversity (music genres × landmark album,
philosophical schools × canonical text, etc.) shifts to the 3
gap-2 categories.

**No remaining family-level gaps.** All 16 domain families now have
at least one instance after the parallel-agent
`major_b2c_online_learning_platforms` staging (Education/EdTech).
Future staging can focus purely on category balance and seed
coverage.

---

## Recommended next-instance sequence

Bias toward seed-driven, gap-filling, domain-diversifying picks. A
reasonable ordered sequence given the current snapshot:

1. ~~#15 — Planets × named moons~~ — **STAGED 2026-04-28**
   (multi_attribute_projection + Science/Astronomy, 137 rows × 5 cols, Tier A)
2. ~~#21 — World religions × holy sites~~ — **STAGED 2026-04-28**
   (scaled_enumeration + Religion, 20 rows × 6 cols, Tier B)
3. ~~#20 — Decade × 5 inventions~~ — **STAGED 2026-04-29**
   (scaled_enumeration + Technology, 50 rows × 5 cols, Tier B)
4. ~~#19 — Scientific disciplines × top 5 scientists~~ — **STAGED 2026-04-29**
   (scaled_enumeration + Science/History of Science, 25 rows × 6 cols, Tier B)
5. ~~#4 — Dishes high in vitamin C~~ — **STAGED 2026-04-29 (adapted)**
   as `vitamins_top5_food_sources` (scaled_enumeration + Food/Nutrition,
   30 rows × 5 cols, Tier B)
6. ~~#23 — Nobel categories × last 5 laureates~~ — **STAGED 2026-04-29**
   as `nobel_categories_2020_2024_laureates`
   (multi_attribute_projection + Science/Academia, 62 rows × 5 cols, Tier B)
7. ~~#7 — 10 most populous cities per continent~~ — **STAGED 2026-04-30**
   as `top_10_most_populous_cities_per_continent`
   (top_k + Geography/Cities, 60 rows × 7 cols, Tier B)
8. ~~#1 — Top 3 widely spoken languages per continent~~ — **STAGED 2026-04-30**
   as `top3_languages_per_continent`
   (scaled_enumeration + Society/Languages — first Society instance, Tier B)
9. ~~original temporal instance~~ — **STAGED 2026-04-30**
   as `summer_olympics_hosts_1960_2020`
   (temporal + Sports/Olympics, first primary-temporal instance)
10. ~~#2 — Countries that drive on the left~~ — **STAGED 2026-04-30**
    as `countries_that_drive_on_the_left`
    (set_filter + Transportation/Road Traffic — first Transportation
    instance, 54 rows × 3 cols, Tier A SPARQL)
11. ~~original comparative instance~~ — **STAGED 2026-04-30**
    as `countries_with_more_than_5_fields_medalists`
    (comparative + aggregation + Science/Mathematics — closes the last
    zero-coverage category, 11 rows × 4 cols, Tier B)
12. ~~#14 — G20 GDP/pop/lang/govt~~ — **STAGED 2026-04-30** as
    `g20_countries_attributes` (multi_attribute_projection +
    Business/Economics — second Business instance; 19 rows × 5 cols;
    Tier B manual_wikipedia, source_count=20). Audit-passed Ready
    after one cycle (two BLOCKING factual fixes in the South Africa
    row — added South African Sign Language as the 12th official
    language and restored the "with an executive presidency"
    qualifier on Government type — plus a consistency revert of an
    earlier trim on Russia's Government type, applying a
    verbatim-infobox policy across all 19 rows).
13. ~~Original `implicit_join` instance — Nobel Literature laureates
    × notable works~~ — **STAGED 2026-04-30** as
    `notable_works_of_recent_nobel_literature_laureates`
    (implicit_join + Culture/Literature — first Literature
    subdomain; 125 rows × 5 cols; Tier A sparql_wikidata,
    source_count=2). Audit-passed Ready after three cycles: a
    BLOCKING source-grounding rebuild from curator-judgment 3-per-
    laureate hand-selection to a fully deterministic Wikidata
    SPARQL extraction over P166/P800 (75 → 125 rows; per-laureate
    work count is variable 1-10 and exactly what Wikidata declares),
    plus two hygiene cycles (Original-Language clarification for
    multi-language laureates and a 126-→125-row metadata sweep after
    the (Laureate, Work) dedup).
14. ~~Original Law instance — landmark US Supreme Court decisions~~
    — **STAGED 2026-04-30 → Ready 2026-05-01** as
    `landmark_us_supreme_court_decisions_by_chief_justice`
    (implicit_join + **Law/Judiciary** — first Law family instance;
    115 rows × 4 cols across 15 chief justices; Tier B
    manual_wikipedia, source_count=16: master landmark article ⋈
    15 per-Court Wikipedia articles; claim_type source_defined
    intersection). Audit-passed Ready after a long 11-cycle arc:
    cycle-1 was a Path-β source-plan pivot from year-tenure range
    join to per-Court intersection; cycles 2-9 were audit-driven
    incremental case discovery (55 → 61 → 67 → 83 → 91 → 91 → 100
    → 109 → 115) bottlenecked by WebFetch master-article
    truncation; cycles 8-10 corrected master-source value errors
    (Cruikshank year + Voting/Redistricting Primary Issue +
    Martin year + Learning Resources year/issue). Closes the Law
    family gap and brings `implicit_join` from gap 5 to gap 3.
    The 11-cycle audit transcript is a useful reference for future
    implicit_join over master-list ⋈ per-entity-page sources where
    full master extraction is not feasible in one fetch.
15. ~~Original Environment instance — countries with majority
    renewable electricity~~ — **STAGED 2026-05-01** as
    `countries_over_50pct_renewable_electricity` (set_filter +
    **Environment/Energy** — first Environment family instance;
    66 rows × 4 cols; Tier B manual_wikipedia, source_count=1;
    closes one of the two remaining family-level gaps and brings
    `set_filter` from gap 2 to gap 1). Audit-passed Ready after a
    five-cycle arc: cycle 1 caught two BLOCKING factual issues
    (title-vs-filter mismatch — title said "more than 50%" but
    filter used `>=50` and included exact-50% rows Cook Islands
    and Greece; sovereign-vs-dependencies scope claim — source
    framing is "countries and dependencies" and includes Greenland
    and French Guiana) plus two HYGIENE items (12-vs-9 100%-count
    and Iceland-vs-Kenya Other-dominance attribution); cycles 2-5
    cleaned successive narrative-drift bands of `>=50`/`68`-row/
    Cook-Islands-Wind/Wind-dominant-list references introduced or
    left behind by the cycle-1 repair. Useful reference: when a
    single staged-data error (Cook Islands' wrong dominance) is
    described in narrative, every cross-reference in draft.json and
    curation.json must be re-aligned against the live data, not the
    prior narrative — the renewables arc spread one source error
    across six narrative locations.
16. ~~Original Education instance~~ — **STAGED → Ready 2026-05-11**
    as `major_b2c_online_learning_platforms` (multi_attribute_projection
    + **Education/EdTech** — first Education family instance; 13 rows
    × 4 cols; Tier B manual_wikipedia, source_count=15: 1 master
    `List of MOOC providers` + 13 per-platform articles + 1
    Wikimedia Foundation supporting article for Wikiversity's
    Country derivation; closes the lone remaining family-level gap;
    brings `multi_attribute_projection` from gap 3 to gap 2).
    Audit-passed Ready after a 10-cycle arc — see Current Snapshot
    bullet above for the cycle-by-cycle summary. With this staging,
    **all 16 domain families now have at least one instance**.
17. ~~Original aggregation instance~~ — **STAGED 2026-05-02** as
    `programming_languages_by_decade_of_first_appearance`
    (aggregation + **Technology/Programming** NEW subdomain; 14 rows
    × 5 cols; total 424 Timeline entries across 14 decades 1790s-2020s;
    Tier B manual_wikipedia, source_count=15: 1 master Timeline + 1
    cross-validation List + 13 per-language articles). First staged
    `aggregation` instance — brings the category from gap 4 to gap 3.
    Audit-passed Ready after a 6-cycle arc: cycle-1 count miscount
    (419→424) + db_analog mismatch + Country→Year column rename;
    cycle-2 Year mixed-rule fix + db_analog alias mismatch + Country
    HYG scrub; cycle-3 Year rule pivot infobox→Timeline (pre-1950
    rows have no infoboxes); cycle-4 explicit Timeline-Entry-Start-Year
    range-scalarization + C++ self-correction (1985→1983) +
    per-language-infobox HYG scrub; cycle-5 Fortran→FORTRAN I
    disambiguation; cycle-6 cycle-history narrative scrub. Useful
    reference: aggregation rules with mixed temporal coverage
    (pre-1950 historical entries vs modern infobox-attested languages)
    require a single uniform source rule documented upfront — the
    cycle-2-vs-cycle-3 flip-flop on infobox vs Timeline could have
    been avoided with explicit pre-stage commitment.
18a. ~~Original multi_hop instance — Apollo lunar landing crews~~
     — **STAGED 2026-05-11 → Ready 2026-05-11** as
     `apollo_lunar_landing_mission_crews` (multi_hop +
     **Science/Space Exploration** NEW subdomain; 18 rows × 5 cols;
     composite PK (Mission, Astronaut); 6 Apollo lunar landings ×
     3 crew = 12 moonwalkers + 6 orbital CMPs; Tier B manual_wikipedia,
     source_count=7: 1 master List_of_Apollo_astronauts + 6 per-mission
     articles). Audit-passed Ready in a single cycle (only blocker
     was a linter blanking curation.json — mechanical fix; only
     hygiene was correcting a "spanned year-boundaries" wording in
     temporal_note narrative — CSV values were correct). Brings
     `multi_hop` from gap 4 to gap 3. NEW entity class
     (missions × astronauts) — neither countries nor cities/continents.
18b. ~~Original temporal instance — Time Persons of the Year 2000-2024~~
     — **STAGED 2026-05-11 → Ready 2026-05-11** as
     `time_person_of_the_year_2000_2024` (temporal +
     **Society/Media** NEW subdomain; 25 rows × 5 cols; PK = Year;
     5 cols Year/Choice/Entity Type/Lifetime/Selection Number;
     Tier B manual_wikipedia, source_count=1). Audit-passed Ready
     after a 2-cycle arc: cycle-1 BLOCKING was rowspan=2 row
     recovery (source's 2020 row assigns Biden+Harris and 2022
     row assigns Zelenskyy+Spirit-of-Ukraine as joint sub-entities
     via HTML rowspan=2; both fixed in cycle 1 with a third
     'Joint Selection' Entity Type value added to the schema);
     cycle-2 closed with HYGIENE-only residual stale-narrative
     fixes. Brings `temporal` from gap 4 to gap 3. Society family
     extends from 1 to 2 (Languages, Media).
18c. ~~Original hierarchical_decomposition instance — foundational
     painters per modern art movement~~ — **STAGED 2026-05-11
     (parallel agent)** as
     `foundational_painters_of_major_modern_art_movements`
     (hierarchical_decomposition + **Culture/Visual Arts** NEW
     subdomain; 18 rows × 5 cols; composite PK (Movement, Painter);
     6 art movements × 3 painters; Tier B manual_wikipedia,
     source_count=7). Brings `hierarchical_decomposition` from gap
     4 to gap 3. Culture family extends from 6 to 7 subdomains
     (Visual Arts NEW). NEW entity class (art movements × painters
     with representative works). **Note:** as of this snapshot,
     the parallel agent's staging has csv+draft.json only; no
     curation.json or comments.json yet — auditor should treat as
     pre-curation skeleton.
19. ~~Original `causal` instance — crewed spaceflight fatal incidents~~
    — **STAGED 2026-05-12 → Ready 2026-05-12** as
    `crewed_spaceflight_fatal_incidents` (causal + Science/Space
    Exploration; 13 rows × 5 cols; Tier B manual_wikipedia,
    source_count=1; 5-cycle audit arc dominated by single-source-
    rigor BLOCKINGs for per-incident detail leakage). Brings
    `causal` from gap 4 to gap 3. **Useful reference**: 3-of-5
    cycle BLOCKINGs were single-source rigor violations (per-
    incident or per-person Wikipedia article details leaking into
    staged cells when source_urls only declared the master list).
    Future single-source instances should commit to
    master-only cell wording from cycle 0.
20. ~~Original `causal` instance — modern animal extinctions~~ —
    **STAGED 2026-05-12 → Ready 2026-05-12** as
    `modern_animal_extinctions_since_1900` (causal +
    **Science/Conservation NEW subdomain** — Science extends to 8
    subdomains; 22 rows × 5 cols; Tier B manual_wikipedia,
    source_count=24: 2 master Wikipedia articles + 22 per-taxon).
    Brings `causal` from gap 3 to **gap 2** (3 of 5 staged) — the
    first non-saturated category to drop below gap-3. First
    animal-taxa-keyed instance. 4-cycle audit arc covering
    claim_type, year relation, cause-cell grounding, taxa+
    populations scope, hygiene rename. See bullet in Current
    snapshot for full cycle history.
20b. ~~Original `geographic` instance — major world deserts~~ —
    **STAGED 2026-05-12 → Ready 2026-05-12** as
    `major_world_deserts_by_area` (geographic + **Geography/Deserts
    NEW subdomain** — Geography extends to 10 instances; 18 rows ×
    4 cols; Tier B manual_wikipedia, source_count=19: 1 master
    'List of deserts by area' + 18 per-desert articles). Brings
    `geographic` from gap 4 to **gap 3** (2 of 5 staged). Original
    instance (no seed) — first physical-landform-keyed instance.
    3-cycle audit arc. See bullet in Current snapshot.
20c. ~~Original `comparative` instance — tennis Grand Slam
    champions~~ — **STAGED 2026-05-12 → Ready 2026-05-12** as
    `tennis_players_with_more_than_5_grand_slam_singles_titles`
    (comparative + **Sports/Tennis NEW subdomain** — Sports
    extends from 2 to 3 instances; 119 rows × 5 cols; Tier B
    manual_wikipedia, source_count=4: 2 master Grand Slam lists +
    2 Open Era records pages). **First athlete-keyed instance in
    pkdb_bench** — proves the comparator primitive beyond
    country-keyed entities. Brings `comparative` from gap 4 to
    **gap 3** (2 of 5 staged) — clears the last gap-4 category.
    Universe-heavy comparative (119 rows; 24 qualifiers >5).
    5-cycle audit arc + cycle-6 hygiene cleanup. See bullet in
    Current snapshot.
20. ~~#18 — 20th-c US presidents × attrs~~ — **STAGED 2026-05-11
    → Ready 2026-05-11** as
    `twentieth_century_us_presidents_attributes`
    (multi_attribute_projection + History/Politics — second History
    instance; 18 rows × 5 cols; PK = President; 5 cols President /
    Term Start Year / Term End Year / Party / Vice President(s);
    Tier B manual_wikipedia, source_count=1). Audit-passed Ready
    in **1 cycle with 0 findings** — first single-cycle clean
    Ready of this session. Brings `multi_attribute_projection` from
    gap 2 to gap 1 (7 of 8 — third category to approach
    saturation). Universe: 18 presidents whose term overlapped any
    portion of the 20th century (1901-2000), McKinley through
    Clinton; George W. Bush excluded (term started Jan 20, 2001).
    Three multi-VP rows (FDR 3 VPs, Nixon 2 VPs, McKinley 2 VPs).
    Useful reference: single-source Tier B staging from a
    well-curated Wikipedia master wikitable + rowspan-aware
    pandas.read_html extraction is a clean 1-cycle Ready pattern.
21. **#6 (reshaped) — Continents × countries with population over
    50M** — `aggregation` (gap 3); the strict seed reading fails the
    row-count gate, but a reshape that reports per-continent counts
    AT MULTIPLE thresholds (e.g., >10M, >25M, >50M, >100M) gives
    ~24 rows of aggregation (6 continents × 4 thresholds) and is
    Tier A SPARQL clean.
22. **#16 — South America countries × attrs** —
    `multi_attribute_projection`; Tier A SPARQL feasible. Note
    Geography is already heavy (10 instances) — adapt the framing
    if needed to surface a non-geographic axis.
23. ~~#9 — Physicists who were Nobel laureates~~ — **STAGED 2026-04-30**
    as `physicists_who_were_nobel_laureates` (set_operation INTERSECT +
    **Science/Physics** — new subdomain; 285 rows × 4 cols; Tier A
    sparql_wikidata, source_count=1). Audit-passed Ready in one cycle
    with no fixes needed. The instance uses the wider physicists ∩
    all-Nobels reading (rather than the older Physics-and-Chemistry
    double-laureate reading in `tables/nobel_physics_and_chemistry.csv`,
    which produces only 1 row and fails the gate). Brings
    `set_operation` to gap 1 (7 of 8) — first category to approach
    saturation.
24. ~~#17 — 50 US states × attrs~~ — **STAGED 2026-05-11 → Ready
    2026-05-11** as `fifty_us_states_attributes`
    (multi_attribute_projection + Politics/Government — extends
    Politics from 5 to 6 instances; 50 rows × 6 cols; PK = State;
    6 cols State / Capital / Largest City / Statehood Year /
    Population (2020) / Total Area (sq mi); Tier B
    manual_wikipedia, source_count=1). Audit-passed Ready in **1
    cycle with 1 HYGIENE** (stale ratifier-count narrative
    7→11; CSV cells were correct). **SATURATES
    `multi_attribute_projection` at 8/8 — first category to fully
    clear target.** 17 of 50 states have Capital == Largest City
    (calibration-rich axis); 33 do not. Different row-count band
    from the recent 13-25 staging cluster (EdTech 13, Time POY
    25, Presidents 18, G20 19). Useful reference:
    pandas.read_html with multi-column-header parsing on a
    well-structured Wikipedia master wikitable is a clean
    near-1-cycle pattern.
25. ~~Original `qualitative` instance — music genres × landmark
    album~~ — **STAGED 2026-05-12 → Ready 2026-05-13** as
    `major_music_genres_landmark_albums` (qualitative + Culture/Music
    — second Music-keyed instance; 10 rows × 4 cols; Tier B
    manual_wikipedia, source_count=20). Brings `qualitative` from
    gap 3 to **gap 2** (3 of 5 staged). NEW entity class (music
    genres × albums × artists). 4-cycle paired curator/audit arc
    terminating at 0/0/0 audit confirmation. See bullet in Current
    snapshot.
26. ~~Original `factual_lookup` instance — Greek Olympian gods ×
    Roman equivalent~~ — **STAGED 2026-05-13 → Ready 2026-05-13**
    as `twelve_olympians_greek_to_roman` (factual_lookup +
    multi_attribute_projection secondary; Religion/Mythology NEW;
    13 rows × 4 cols; source_count=14). Brings `factual_lookup` from
    gap 3 to **gap 2** (3 of 5 staged). NEW entity class (Greek
    deities / mythological figures). 5-cycle paired curator/audit
    arc terminating at 0/0/0. See bullet in Current snapshot.
26b. ~~Original `set_filter` instance — world's reigning kingdoms~~
     — **STAGED 2026-05-13 → Ready 2026-05-13** as
     `worlds_reigning_kingdoms` (set_filter + Politics/Government —
     extends Politics from 6 to 7 instances; 17 rows × 5 cols;
     Tier B manual_wikipedia, source_count=2). **SATURATES
     `set_filter` at 5/5 — third category to fully clear target.**
     Universe: 17 sovereign states constituted as a kingdom with
     their own monarch (excludes 14 Commonwealth realms that share
     Charles III). See bullet in Current snapshot.
26c. ~~Original `causal` instance — modern financial crises~~ —
     **STAGED 2026-05-13 → Ready 2026-05-13** as
     `modern_financial_crises_1929_2025` (causal +
     Business/Economics — third Business instance; 27 rows × 5 cols;
     Tier B manual_wikipedia, source_count=1). Brings `causal` from
     gap 2 to **gap 1** (4 of 5 staged) — **first non-saturated
     category to reach gap-1**. Universe: 27 stock market crashes
     and bear markets 1929-2025 whose Notes column documents a
     clear proximate cause. 6-cycle audit arc terminating at 0/0/0.
     See bullet in Current snapshot.
27. ~~Original `multi_hop` instance — Notable spacecraft missions~~
    — **STAGED 2026-05-13 → Ready 2026-05-13** as
    `notable_robotic_spacecraft_missions` (multi_hop + Science/Space
    Exploration; 22 rows × 4 cols; source_count=23). Brings
    `multi_hop` from gap 3 to **gap 2** (3 of 5 staged). NEW entity
    class extension (robotic spacecraft missions broaden the
    Apollo-crewed entity class). 3-cycle paired curator/audit arc
    terminating at 0/0/0. See bullet in Current snapshot.
28. ~~Original `top_k` instance — Notable bridges of the world ×
    main span~~ — STAGED 2026-05-13 → Ready 2026-05-14 after a
    3-cycle paired curator/audit arc terminating at 0/0/0
    Ready+Clean: `notable_bridges_by_main_span` (top_k +
    **Technology/Civil Engineering NEW subdomain** — Technology
    family extends from 2 to 3 instances; 17 rows × 5 cols; Tier B
    manual_wikipedia, source_count=22). Brings `top_k` from gap 3
    to **gap 2** (3 of 5 staged). NEW entity class: bridges as
    engineered structures (no prior pkdb_bench instance covers
    engineered physical structures). Cycle-1 staged
    'Engineering/Civil Engineering' was cycle-2 audit corrected to
    'Technology/Civil Engineering' per `domain_taxonomy.md`
    Technology-family pattern. See bullet in Current snapshot.
29. ~~Original `factual_lookup` instance — world's tallest dams ×
    height + country + river~~ — STAGED 2026-05-14 → Ready
    2026-05-14 after a 2-cycle paired curator/audit arc terminating
    at 0/0/0 Ready+Clean: `worlds_tallest_dams_by_structural_height`
    (factual_lookup + **Technology/Civil Engineering subdomain** —
    second Civil Engineering instance joining
    `notable_bridges_by_main_span`; Technology family extends from
    3 to 4 instances; 20 rows × 5 cols; Tier B manual_wikipedia,
    source_count=21). **SATURATES `factual_lookup` at 5/5 — FIFTH
    CATEGORY TO FULLY CLEAR**. NEW `engineered-structures/dams`
    fine-class (bucket extends from 2 to 3 instances). See bullet
    in Current snapshot.
29. **Other gap-3 candidates** (6 categories at gap 3 total —
    pick any). Sketches: ~~`implicit_join` — "world capitals × the
    river they sit on"~~ **STAGED 2026-05-15 → Ready 2026-05-16 as
    `sovereign_state_capitals_on_rivers` — SATURATES implicit_join
    at 8/8 (9th category fully cleared)**; `aggregation` — "books
    that have won both Booker and Pulitzer"; `temporal` — "major
    peace treaties of 20th century × year + parties";
    `hierarchical_decomposition` — "periodic table groups ×
    representative elements"; `geographic` — "major active
    volcanoes by continent"; `comparative` — "athletes with more
    than 5 Olympic gold medals". (`top_k`
    dropped from this list — it is now at gap 2 after
    `notable_bridges_by_main_span`.)

After post-FAOSTATCrops-Ready + parallel-agent post-SCOTUS-Appointments-Ready, the benchmark sits at: **🎯 15 of 15 categories FULLY CLEARED — ABSOLUTE-SATURATION MILESTONE ACHIEVED** (`factual_lookup` 6/5 + `top_k` 6/5 + `multi_hop` 5/5 + `qualitative` 6/5 + `multi_attribute_projection` 8/8 + `set_operation` 8/8 + `set_filter` 5/5 + `causal` 5/5 + `implicit_join` 9/8 + `scaled_enumeration` 6/5 + `hierarchical_decomposition` 5/5 + `comparative` 5/5 + `temporal` 5/5 + `geographic` 5/5 + `aggregation` 5/5), **zero gap categories at any level**. 16 domain families covered. **POST-SATURATION PHASE**: all target categories at-or-over their saturation thresholds (84 categorized + 1 uncategorized = 85; subtotal 79+10=89, but actual = 80 categorized including overage). Future picks shift focus from category-gap-chipping to crisis-light family chips (Health remains at 1 instance) + entity-class diversification + quality-improvement passes.

**Recommended next pick (post-15/15 saturation — focus shifts to family-chip and quality):**

Single direction: saturate ANY of `aggregation` / `geographic` / `temporal` at 5/5 (any one makes the 13th fully-cleared category; benchmark would be at 13 saturated + 2 gap-1 remaining + 0 gap-2). All three are now equally distant from the line, and the MILESTONE of 0 gap-2 categories is preserved. New instances should target under-represented entity classes (`organizations/*` at 5, `engineered-structures/*` at 5 with skyscrapers added, `taxonomic-entities/*` at 5; `time-periods/*` extended to 2 with NEW geological-periods) and underrepresented domains (Law/Judiciary, Food/Nutrition, Environment/Energy (2 instances after Global200TMBF), Health/Epidemiology — all at 1-2 instances still).

Sketches respecting entity-class duplicate check (avoid `countries` at 14, `people/laureates` at 3, `physical-features/mountains` at 1 already, `physical-features/rivers` at 2 already, `events/battles`, `events/empires`, `abstract-categories/religions` x2, `abstract-categories/philosophical-schools`, `abstract-categories/architectural-styles`):

- ~~**"Periodic table groups × 3 representative elements"** (`hierarchical_decomposition`)~~ **STAGED 2026-05-16 → Ready 2026-05-16 as `periodic_table_groups_first_three_elements_by_atomic_number`**. Pushes `hierarchical_decomposition` from gap 3 to gap 2 (3/5). Entity class staged as **abstract-categories/chemical-groups** (NOT taxonomic-entities/chemical-groups as originally sketched — audit-cycle-1 amendment #6 ruled abstract-categories more semantically consistent because the PK is a conceptual IUPAC category, not an individual chemical entity); abstract-categories bucket extends from 5 → 6. Renamed pre-staging from `representative_elements` to `first_three_elements_by_atomic_number` to drop the curator-judgment 'representative' framing — making claim_type=source_defined defensible.
- ~~**"Major active volcanoes by continent"** (`geographic`)~~ **STAGED 2026-05-17 → Ready 2026-05-18 as `decade_volcanoes_by_continent`**. Pushes `geographic` from gap 3 to gap 2 (3/5). Final framing: IAVCEI Decade Volcanoes (closed canonical 16-entry list, stable since 1991) per Wikipedia `Decade Volcanoes` master article — replaced the original "Major active volcanoes" framing because the audit cycle-1 + cycle-2 design reviews found "active" + "major" thresholds are retro-discovery-prone. NEW `physical-features/volcanoes` fine-class. Lesson: closed canonical lists (Decade Volcanoes ≈ periodic_table_groups ≈ twelve_olympians) saturate source_defined cleanly in 2-3 pre-staging rounds; retro-discovery-prone "first-N-by-historical-year" universes (the abandoned vaccines design) chase deeper history each round.
- **"Athletes with more than 5 Olympic gold medals"** (`comparative`; `people/athletes` fine-class extends from 1 → 2; Sports/Olympics existing). ★★ — entity class is people/athletes which is mid-light. Source: 'List of multiple Olympic gold medalists' master.
- **"Major peace treaties of the 20th century × year + parties"** (`temporal`; `events/treaties` NEW fine-class extending events bucket from 10 → 11; History/Politics existing). ★★★ strong; temporal-keyed by year; events/treaties is a novel fine-class.
- **"Books that have won both Booker AND Pulitzer prizes"** (`aggregation`; `artifacts/books` NEW fine-class; Culture/Literature existing — 3rd Literature instance after Booker + Pulitzer + Nobel works). ★★ — but the set-operation framing might overlap with the saturated set_operation category; better framed as aggregation-over-prize-rosters with composite filter.
- **"Top-5 longest-reigning monarchs per century since 1500"** (`scaled_enumeration`; `people/monarchs` NEW fine-class extending people bucket; History/Politics). ★★ — historical scope ambiguities.

---

## Maintenance

This file is a snapshot. Refresh it after each active labeling session, or
before using it if several staged/registered artifacts have changed since the
snapshot date.

1. Run the Stage-0 inventory commands and reconcile any surprises (a
   parallel-agent staging that did not appear in the previous snapshot).
2. Update the category-coverage table for any new instances.
3. Update the seed-coverage list (which seeds are touched, which remain).
4. Update the domain-frequency table.
5. **Update the entity-class inventory** — add one row to the
   per-instance entity-class table for each new Ready instance, then
   refresh the bucket rollup counts and the Axis 3 + Current-snapshot
   saturated/under-represented bullet lists. The per-instance table
   is the canonical source; the rollup is derived from it.
6. Re-run the rubric and update the recommended-next-instance sequence,
   marking newly-staged seeds with `~~strikethrough~~` + **STAGED**.

Keep the snapshot date in this file's "Current snapshot" header. The
one-month rule is only a fallback for quiet periods; during active curation,
refresh per session. When multiple agents may be working in parallel,
treat the inventory check as mandatory before proposing — the file's
snapshot can lag the on-disk reality between agent sessions.
