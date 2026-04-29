# Benchmark Domain Survey

**Status:** draft, 2026-04-28.

**Purpose.** PKDB-Bench is a common-sense / Wikipedia-style parametric
knowledge benchmark. This note surveys recent DB, NLP, and ML benchmarks to
understand what topical domains they expose, and what PKDB-Bench should borrow
when selecting new table instances.

The goal is not to copy another benchmark's taxonomy directly. Most existing
benchmarks are either multiple-choice QA, fact verification, table QA, or broad
scenario suites. PKDB-Bench differs because it asks a closed-book model to
produce a structured table. Still, their domain coverage is useful motivation.

For the operational domain-family vocabulary used during curation, see
`domain_taxonomy.md`.

## Scope

Primary inclusion window: 2021-2026, with emphasis on top ML/NLP/DB venues or
highly relevant factual / Wikipedia benchmarks. Some entries are not top-conference
papers, but are included because they are directly relevant to parametric factuality
or Wikipedia table generation.

## Survey

| Benchmark / paper | Venue / year | Source type | Domain signal exposed | What PKDB-Bench should borrow |
| --- | --- | --- | --- | --- |
| [MMLU](https://openreview.net/forum?id=d7KBjmI3GmQ) | ICLR 2021 | Exam-style multiple choice | 57 subjects grouped into broad areas such as STEM, humanities, social sciences, and other; examples include math, US history, computer science, law, and more. The dataset card lists subjects such as astronomy, business ethics, clinical knowledge, economics, geography, government, law, medicine, nutrition, philosophy, psychology, sociology, world religions, and others ([dataset card](https://huggingface.co/datasets/cais/mmlu)). | Broad domain breadth matters. PKDB-Bench should cover school-like knowledge, professional knowledge, and world facts, but avoid becoming only exam QA. |
| [MMLU-Pro](https://proceedings.neurips.cc/paper_files/paper/2024/hash/ad236edc564f3e3156e1b2feafb99a24-Abstract-Datasets_and_Benchmarks_Track.html) | NeurIPS Datasets and Benchmarks 2024 | Harder multi-domain multiple choice | 14 domains: Biology, Business, Chemistry, Computer Science, Economics, Engineering, Health, History, Law, Math, Philosophy, Physics, Psychology, Other ([project / dataset](https://github.com/TIGER-AI-Lab/MMLU-Pro)). | A compact domain family list is useful. It separates Science, Business, Health, Law, History, and Philosophy instead of hiding all under "general knowledge." |
| [TruthfulQA](https://aclanthology.org/2022.acl-long.229/) | ACL 2022 | Misconception-resistant factual QA | 817 questions across 38 categories, with paper examples including health, law, finance, and politics. | Truthfulness risk is domain-dependent. PKDB-Bench should include domains where false folk beliefs or source ambiguity matter: health, law, finance, politics, religion, history, and nutrition. |
| [SimpleQA](https://openai.com/index/introducing-simpleqa/) | OpenAI 2024 technical report | Short-form factual QA | Broad topics, described as ranging from science and technology to TV shows and video games. Public dataset metadata includes `topic` and `answer_type` fields in common mirrors ([dataset card](https://huggingface.co/datasets/OpenEvals/SimpleQA)). | Keep questions easy to grade and time-stable when possible. For PKDB-Bench, the analogous unit is a verifiable cell, not a single answer. |
| [Pinocchio](https://openreview.net/forum?id=9OevMUdods) | ICLR 2024 | Factual knowledge probing | 20K factual questions spanning sources, timelines, domains, regions, and languages. | Domain is only one axis. Region, language, source, and time are also coverage axes, but PKDB-Bench should keep them secondary to table category and domain. |
| [KILT](https://aclanthology.org/2021.naacl-main.200/) | NAACL 2021 | Wikipedia-grounded knowledge-intensive tasks | Not primarily topical; organized by task families: open-domain QA, fact checking, slot filling, entity linking, and dialogue, all grounded in one Wikipedia snapshot. | Source grounding and provenance matter. PKDB-Bench should retain strict source URLs and avoid mixing source snapshots silently. |
| [FEVEROUS](https://neurips.cc/virtual/2021/22713) | NeurIPS Datasets and Benchmarks 2021 | Wikipedia fact verification over text and tables | Not topically balanced; focuses on verification over unstructured and structured Wikipedia evidence. | Wikipedia tables plus prose are both legitimate evidence. Audit should search for missing Wiki-family list/table pages when row universes are underdefined. |
| [CREAK](https://openreview.net/forum?id=mbW_GT3ZN-) | NeurIPS Datasets and Benchmarks 2021 | Entity commonsense | Entity-grounded commonsense; examples involve fictional entities, products, institutions, occupations, and social affordances. | Useful for qualitative PKDB-Bench instances: model must combine entity knowledge with commonsense-like predicates, but every row still needs a source-defined anchor. |
| [CommonsenseQA 2.0](https://openreview.net/forum?id=qF7FlUT5dxa) | NeurIPS Datasets and Benchmarks 2021 | Adversarial commonsense QA | Not a topical domain taxonomy; collection is controlled by prompts/phrases and human validation. | Domain balance is not enough. Candidate questions should also be selected for difficulty against current models and for broad human agreement. |
| [FeTaQA](https://aclanthology.org/2022.tacl-1.3/) | TACL 2022 | Wikipedia table QA | Wikipedia tables from diverse topics; task requires retrieval, integration, inference, and free-form answer generation from table cells. | PKDB-Bench should favor table shapes that require integrating multiple cells, not just retrieving isolated facts. |
| [WikiTabGen](https://aclanthology.org/2025.knowledgenlp-1.4/) | KnowledgeNLP @ ACL 2025 | Wikipedia table generation from parametric knowledge | 119 manually curated Wikipedia tables with descriptions; analyzes table size, popularity, and numeric content. | Direct ancestor. PKDB-Bench should go beyond single Wikipedia tables by stressing multi-source synthesis, partitioning, joins, and per-cell calibration. |
| [WikiDBs](https://openreview.net/forum?id=abXaOcvujs) | NeurIPS Datasets and Benchmarks 2024 | Wikidata relational databases | 100,000 relational databases from various domains, based on Wikidata, with multiple tables connected by foreign keys. | Good motivation for relational structure. PKDB-Bench should use Wikidata/SPARQL when appropriate, but still curate human-meaningful common-knowledge queries. |
| [GitTables](https://gittables.github.io/) | PACMMOD / ACM Management of Data 2023 | GitHub CSV tables | 1M relational tables with DBpedia and Schema.org semantic type annotations. | Semantic type diversity is useful for domain normalization, but GitHub table domains are not the same as common-sense / Wikipedia knowledge. |
| [BIG-bench](https://huggingface.co/papers/2206.04615) | ICLR 2023 | Collaborative LM task suite | Diverse topics including linguistics, childhood development, math, common-sense reasoning, biology, physics, social bias, software development, and more. | Diversity should be explicit. PKDB-Bench can borrow the idea of broad topical spread, but with stricter table/source viability. |
| [HELM](https://crfm-helm.readthedocs.io/) | TMLR 2023 / living benchmark | Multi-scenario LM evaluation | Scenario-oriented rather than topical; includes targeted evaluations for world knowledge, reasoning, memorization/copyright, and disinformation. | Report domain and query-category breakdowns, not only aggregate score. Calibration and robustness can vary sharply by domain. |
| [LegalBench](https://www.law.virginia.edu/node/2185931) | NeurIPS 2023 | Legal reasoning benchmark | Single-domain benchmark with 162 tasks covering six types of legal reasoning. | Law is a meaningful domain family, but many legal facts are jurisdictional and time-sensitive; use only source-stable legal tables. |
| [GPQA](https://huggingface.co/papers/2311.12022) | arXiv 2023, widely used | Expert science QA | Biology, physics, and chemistry. | Expert science is not the main PKDB-Bench target, but light coverage of broadly known science facts is useful. Avoid obscure graduate-level rows unless testing abstention/calibration intentionally. |

## Patterns

Existing benchmarks use at least four different domain strategies:

1. **Academic subject taxonomy.** MMLU and MMLU-Pro split knowledge into
   school/professional domains: STEM, humanities, social sciences, law, health,
   business, and so on.
2. **Wikipedia / source-grounded task taxonomy.** KILT, FEVEROUS, FeTaQA, and
   WikiTabGen often rely on Wikipedia as the source and emphasize task type,
   provenance, or table structure more than topical balance.
3. **Commonsense / entity-centered taxonomy.** CREAK and CommonsenseQA 2.0
   emphasize everyday or entity-grounded inferences. The topical domain is
   less important than whether humans agree on the inference.
4. **Specialist domain benchmarks.** LegalBench and GPQA isolate one domain
   deeply. They are useful reminders that broad benchmarks can underrepresent
   important domains, but PKDB-Bench should avoid over-indexing on expert-only
   questions.

## Operational Domain Taxonomy

The finalized working taxonomy lives in `domain_taxonomy.md`. This survey is
kept as the motivation layer: it explains why the taxonomy includes families
such as Geography, Politics, History, Business, Science, Technology, Health,
Culture, Religion, Sports, Education, Society, Food, Law, Transportation, and
Environment.

## Implications For Instance Selection

1. **Add domain family as a planning lens, not a new hard schema yet.** The
   current `domain` field remains free text, but curators should prefer the
   `Family/Subdomain` pattern in `domain_taxonomy.md`.
2. **Limit geography dominance.** The current staged+registered set is heavily
   geography-flavored. For the next batch, prioritize absent families unless a
   geography candidate is unusually strong.
3. **Borrow MMLU-Pro's compactness.** A small family list is easier to maintain
   than dozens of narrow topics. Fine-grained labels can live after the slash:
   `Science/Astronomy`, `Culture/Literature`, `Business/Companies`.
4. **Borrow Wikipedia benchmarks' source discipline.** KILT, FEVEROUS, FeTaQA,
   and WikiTabGen all reinforce the need for explicit source grounding. PKDB-Bench
   should continue citing source URLs and auditing missing Wiki-family pages.
5. **Borrow TruthfulQA/SimpleQA's grading discipline.** Prefer cells with stable,
   verifiable answers. When a domain is inherently unstable, mark temporal
   sensitivity and expected cutoff risk.
6. **Borrow CREAK's entity commonsense carefully.** Qualitative instances are
   valuable, but the predicate must be source-defined or deliberately framed as
   anchored-fuzzy, not invented from parametric intuition.

## Suggested Near-Term Domain Priorities

Given the current PKDB-Bench snapshot, the next instances should strongly favor:

1. **Astronomy / space** - e.g., planets and named moons.
2. **Religion** - e.g., major world religions and holy sites.
3. **Technology / inventions** - e.g., defining inventions per decade.
4. **Food / nutrition** - only if adapted to a registration-safe row count.
5. **Business / finance** - e.g., major companies, exchanges, currencies, or sectors.
6. **Law / public policy** - only for stable, source-defined facts such as
   legal systems, courts, landmark cases, rights, or public-policy rules.
7. **Arts / literature** - common-knowledge friendly and underrepresented.
8. **Transportation / infrastructure** - good for top-k, geography, and temporal tables.

## Practical Selection Rule

When two candidates are otherwise similar:

1. Prefer the one from an untouched human seed.
2. Prefer the one in a high-gap query category.
3. Prefer the one in an absent or underrepresented domain family.
4. Prefer the one with a source-defined universe and at least 10 rows.
5. Prefer the one with both easy/popular and harder/obscure cells, so calibration
   has room to show signal.

This keeps domain variety useful without letting it override the benchmark's core
purpose: closed-book structured extraction of common, source-grounded knowledge.
