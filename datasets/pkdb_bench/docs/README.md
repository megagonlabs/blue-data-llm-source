# PKDB Benchmark (Next Generation)

Design, curation, and implementation hub for the benchmark beyond `multisources_dataset_v1`.

## What this benchmark is

The existing **multisources_dataset_v1** benchmark (5 join + 5 union tables) was the first stable
artifact for multi-source parametric knowledge synthesis. It established the task, but it still
has a narrow structural scope:

- only 10 tables
- mostly join / union style composition
- standard extraction metrics (Key F1 / Cell F1 / column-type F1)

This next benchmark aims to generalize that first dataset into a broader research artifact with:

- **More query types** — a taxonomy of 15 query categories mapped to DB analogues
- **Calibration-aware metrics** — ECE, composite reward-penalty, and selective prediction metrics
- **Larger scale** — more tables, broader domains, semi-automated curation

The design docs live in this directory. Ground-truth data and metadata live in `datasets/pkdb_bench/`.

## Current status

`datasets/pkdb_bench/` is the **consolidated benchmark package**. The `cfg.json` and `tables/`
directory are synchronized: every registered entry has a co-located CSV.

The benchmark currently has **11 registered instances** across 4 query categories:

| Table set | Count | Query categories |
| --- | ---: | --- |
| `trial_join_*` | 5 | `implicit_join`, `multi_attribute_projection`, `set_operation` |
| `trial_union_*` | 5 | `set_operation` |
| `us_presidents_count_by_birth_state` | 1 | `aggregation` |
| **Total** | **11** | 4 categories covered |

`set_filter` has no registered instance yet, although staged candidates exist
(`transboundary_rivers_by_continent_*`). Four additional SPARQL prototype CSVs exist in `tables/`
but are **not registered** due to data quality issues (see `curation_protocol.md` Section 2 for
details). The taxonomy targets 15 categories with **82 instances total** — curation is ongoing.

Target per category: 8 for the three primary multi-source categories (`set_operation`,
`implicit_join`, `multi_attribute_projection`); 5 for all others; 3 for `factual_lookup` (baseline).
Rationale: ≥5 is the BIG-Bench minimum for per-task F1 reliability; 80+ total instances are needed
to fill ECE confidence bins meaningfully (Guo et al. 2017; Naeini et al. 2015).

## How the existing 10 tables fit into the new benchmark

The most likely point of confusion is whether the original 10 tables are being replaced. The answer
is **no**: they should be retained as the first split of the new benchmark.

They serve three roles:

1. **Continuity with prior results**
   They preserve comparability with the already-run `multisources_dataset_v1` experiments.
2. **Seed coverage**
   They instantiate the first few query categories in the new taxonomy.
3. **Calibration baseline**
   They are the only tables currently wired into the existing confidence / ECE evaluation pipeline.

Current seed coverage from the 10 legacy tables:

| Existing table set | Count | Covered query categories |
| --- | ---: | --- |
| `trial_join_*` | 5 | `implicit_join`, `multi_attribute_projection`, `set_operation` |
| `trial_union_*` | 5 | `set_operation` |
| Total seed split | 10 | `implicit_join`, `multi_attribute_projection`, `set_operation` |

This means the benchmark design is ahead of the dataset curation: the taxonomy is broader than the
currently instantiated tables.

## Directory contents

| File | Purpose |
|------|---------|
| `README.md` | This file — current status, benchmark framing, and transition notes |
| `categories.txt` | Taxonomy of 15 query categories with DB analogues and stress notes |
| `example_questions.txt` | Sample queries per category in the LLM-as-DB framing |
| `example_output.png` | Confidence grid mockup (per-cell confidence heatmap) |
| `metrics_notes.txt` | Short metric summary + how to run the implemented ECE evaluator |
| `calibration_metrics.md` | Literature analysis for confidence / calibration design |
| `papers.md` | Related-work survey on parametric knowledge, table extraction, and calibration |

## Query category taxonomy

Defined in `categories.txt`. The taxonomy currently contains **15 categories**:

| Category | DB Analogue | Stress |
|---|---|---|
| factual_lookup | `SELECT attr WHERE id=X` | Single entity, simplest case |
| set_filter | `SELECT * WHERE predicate` | Crisp vs. fuzzy predicates |
| implicit_join | `A JOIN B ON shared_key` | Two implicit tables, finding join condition |
| aggregation | `COUNT / MAX / GROUP BY` | Derived scalar, not a list |
| top_k | `ORDER BY col DESC LIMIT K` | Orderable attribute + limit |
| set_operation | `INTERSECT / EXCEPT / UNION` | Two or more sub-queries combined |
| multi_hop | Recursive CTE / chained joins | Traversal across entity boundaries |
| qualitative | Fuzzy `WHERE` | No crisp semantics; confidence calibration matters |
| temporal | `WHERE year BETWEEN x AND y` | Time-range predicates, fuzzy anchors |
| geographic | Spatial predicates | Cultural / fuzzy spatial membership |
| causal | Derived / computed relation | Inferred relation, not stored fact |
| comparative | Computed comparison column | Same attribute compared across entities |
| hierarchical_decomposition | Partitioned nested-loop join | Scale requires hierarchy-aware decomposition |
| multi_attribute_projection | Multi-column `SELECT` | Per-cell confidence varies by attribute |
| scaled_enumeration | Partitioned `TOP K` over large universe | Decomposition + confidence gradient |

Important caveat: the current 10-table seed split covers only a subset of these categories.
The taxonomy is the target design; the data curation is still catching up.

## Proposed metrics

Defined fully in `calibration_metrics.md`. Summary:

### Current baseline metrics

Used today on `multisources_dataset_v1`:

- Key F1
- Cell F1
- Non-key F1
- per column-type F1

### Calibration-aware metrics

Implemented today in `experiments/evaluate_ece_metric.py`:

- **ECE (Expected Calibration Error)**
- **Composite reward-penalty score**
- **AUACC** (area under the accuracy-coverage curve)
- **AURC** (area under the risk-coverage curve)
- **AUSF1** (area under the selective F1 curve)

### Confidence signal

Current priority order in the evaluator:

1. explicit per-cell confidence CSV from the system
2. consistency-based confidence from multiple runs
3. default `1.0` confidence when neither is available

The literature summary in `calibration_metrics.md` argues that consistency-based signals are the
best default research direction, but the codebase now supports both explicit and fallback modes.

## Data layout

### `datasets/pkdb_bench/`

This is the **authoritative benchmark package**. It is self-contained:

- `cfg.json` — metadata registry with one entry per registered instance. Fields include
  `query_category`, `db_analog`, `operation_type`, `source_count`, `domain`,
  `temporal_sensitivity`, `gt_construction`, and `source_urls`.
- `tables/<table_id>.csv` — ground-truth CSV for each registered instance.

### `datasets/multisources_dataset_v1/`

Still used as the **evaluation history** source for experiments run before the pkdb_bench
consolidation. The 10 `trial_*` CSVs also remain there as the canonical experiment artifact.
The copies in `datasets/pkdb_bench/tables/` are the benchmark-authoritative versions.

## Semi-automated curation path

The semi-automated curation work lives in `datasets/build_benchmark/sparql_to_csv.py`.

That pipeline is currently best understood as **benchmark construction tooling**, not the final
benchmark itself. It demonstrates how to build cleaner ground-truth tables with attached metadata,
and it likely becomes part of the long-term expansion path for categories such as:

- `set_filter`
- `aggregation`
- `top_k`
- `multi_hop`

## Recommended benchmark framing

If we describe PKDB-Bench in papers or internal notes, the cleanest framing is:

1. `multisources_dataset_v1` is the original 10-table benchmark.
2. `pkdb_bench` is the broader benchmark program that:
   - reuses those 10 tables as the seed split,
   - adds richer metadata and calibration-aware evaluation,
   - expands into additional query categories over time.

That framing avoids implying that the old 10 tables were discarded or superseded.

## Key literature gaps

From `papers.md`:

1. No benchmark tests **multi-source parametric table synthesis** at meaningful breadth.
2. No benchmark applies **calibration-aware metrics at cell granularity** for this task.
3. No benchmark provides a **query-type taxonomy** for parametric table extraction.

## Next steps

- [x] Consolidate `datasets/pkdb_bench/` so `cfg.json` and `tables/` refer to the same table set
- [x] Define the curation protocol clearly enough for repeated dataset growth
- [x] Add confidence output support to the extraction pipeline
- [x] Run ECE-style evaluation on `multisources_dataset_v1`
- [ ] Fix and register 4 unregistered SPARQL prototype CSVs (see `curation_protocol.md` §2)
- [ ] Expand table coverage beyond the 11 registered instances
- [ ] Instantiate more of the 15 query categories (currently 4 of 15 covered)
- [ ] Add a dedicated `experiments/evaluate_pkdb_bench.py` entry point
