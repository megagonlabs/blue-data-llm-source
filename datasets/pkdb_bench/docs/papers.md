# Research Papers: LLM Parametric Knowledge, Table Extraction, Calibration Benchmarks
**Query:** LLM parametric knowledge extraction structured tables benchmarks
**Date:** 2026-04-13
**Sources:** Web search (Google), Semantic Scholar, ACL Anthology, OpenReview

---

## Cluster 1 — Direct Ancestors (Table Generation from Parametric Knowledge)

**[1]. Generating Tables from the Parametric Knowledge of Language Models** (KnowledgeNLP @ ACL 2025)
*Authors: Analysis-bots group*
**Summary:** Introduces WikiTabGen, 119 manually curated Wikipedia tables with descriptions, used to benchmark LLMs on generating structured tabular data purely from parametric knowledge. Best model (LLaMA3.1-405B) reaches only 25.4% accuracy. Analyzes how table size, popularity, and numeric content affect performance.
**Why it's relevant:** This is the direct baseline your project extends. WikiTabGen tests single-source table recall; your multisources_dataset_v1 tests multi-source synthesis — a harder, differentiated setting.
🔗 https://aclanthology.org/2025.knowledgenlp-1.4/ | arXiv: https://arxiv.org/abs/2406.10922

---

## Cluster 2 — Parametric Knowledge Probing & Factual Recall Benchmarks

**[2]. Towards Understanding Factual Knowledge of Large Language Models** (ICLR 2024 Spotlight)
*Authors: THU-BPM group*
**Summary:** Introduces Pinocchio, 20K diverse factual questions spanning sources, timelines, domains, regions, and languages. Tests whether LLMs can compose facts, update temporal knowledge, reason over multi-hop facts, identify subtle factual differences, and resist adversarial examples. Finds LLMs still lack factual knowledge and suffer from spurious correlations.
**Why it's relevant:** Directly frames your benchmark's core challenge: LLMs implicitly memorize facts in parameters rather than explicitly store them. Their multi-dimensional taxonomy (temporal, multi-hop, compositional) maps onto your query category taxonomy.
🔗 https://openreview.net/forum?id=9OevMUdods

**[3]. SimpleQA: Measuring Short-Form Factuality in Large Language Models** (OpenAI, 2024)
*Authors: Jason Wei et al. (OpenAI)*
**Summary:** 4,326 short fact-seeking questions with unique, verifiable answers, designed to measure parametric knowledge recall with minimal ambiguity. Focuses on questions that are resistant to guessing and require genuine factual recall.
**Why it's relevant:** SimpleQA targets the same "parametric knowledge" measurement goal as your benchmark, but at the single-cell QA level. Your work scales this to full structured table extraction — the table is essentially thousands of SimpleQA-style cells simultaneously.
🔗 https://arxiv.org/abs/2411.04368

**[4]. FActScore: Fine-grained Atomic Evaluation of Factual Precision in Long Form Text Generation** (EMNLP 2023)
*Authors: Sewon Min, Kalpesh Krishna, Xinxi Lyu, Mike Lewis, Wen-tau Yih, Pang Koh, Mohit Iyyer, Luke Zettlemoyer, Hannaneh Hajishirzi*
**Summary:** Decomposes generated text into atomic facts and computes the percentage supported by a knowledge source. Evaluated 6,500 generations from 13 LLMs. GPT-4 and ChatGPT score higher than open-source models; error rate under 2%.
**Why it's relevant:** FActScore's "atomic fact decomposition" is conceptually equivalent to your per-cell evaluation. Your benchmark extends this from free-form bios to structured table cells — a more demanding, schema-constrained setting.
🔗 https://aclanthology.org/2023.emnlp-main.741/

**[5]. OKBench: Democratizing LLM Evaluation with Fully Automated, On-Demand, Open Knowledge Benchmarking** (arXiv 2025)
*Authors: Unknown (preprint)*
**Summary:** Automated pipeline for generating factual evaluation benchmarks on demand, reducing reliance on hand-curation. Focuses on open-domain factual knowledge across categories.
**Why it's relevant:** Directly relevant to the scalability challenge your multisources_dataset_v1 faces. If you want to expand beyond 10 hand-curated tables to 100+, automated benchmark generation strategies from this work are applicable.
🔗 https://arxiv.org/abs/2511.08598

---

## Cluster 3 — Confidence, Calibration & ECE

**[6]. A Survey of Confidence Estimation and Calibration in Large Language Models** (NAACL 2024)
*Authors: Multiple; comprehensive survey*
**Summary:** Taxonomy of confidence estimation methods: verbalized confidence, token probability, ensemble-based, consistency-based. Covers ECE, reliability diagrams, selective prediction. Reviews calibration across factual QA tasks (TriviaQA, TruthfulQA, FEVER).
**Why it's relevant:** This is the technical foundation for the ECE metric idea in your `metrics.txt`. Establishes the "reward confident-correct, penalize confident-wrong" framing that separates your benchmark from simple F1-based evaluation.
🔗 https://aclanthology.org/2024.naacl-long.366/

**[7]. Uncertainty Quantification and Confidence Calibration in Large Language Models: A Survey** (KDD 2025)
*Authors: Multiple*
**Summary:** Extends calibration taxonomy to production settings. Categorizes uncertainty by source: input, reasoning, parameter, and prediction uncertainty. Focuses on computational efficiency of different UQ approaches.
**Why it's relevant:** More recent than NAACL 2024 survey; particularly useful for distinguishing "parameter uncertainty" (directly what your benchmark targets) from retrieval-augmented uncertainty.
🔗 https://arxiv.org/abs/2503.15850

**[8]. Do LLMs Estimate Uncertainty Well? (ICLR 2025)
*Authors: Unknown (ICLR proceedings)*
**Summary:** Empirically tests whether LLMs' stated confidence aligns with actual accuracy. Finds that better-performing models show more aligned confidence, but even top models show minimal variation in confidence between correct and wrong answers.
**Why it's relevant:** The finding that LLMs are poorly calibrated even when accurate strongly motivates your ECE metric angle — you can't just measure accuracy, you need calibration-aware evaluation.
🔗 https://proceedings.iclr.cc/paper_files/paper/2025/file/ef472869c217bf693f2d9bbde66a6b07-Paper-Conference.pdf

---

## Cluster 4 — Structured Table Understanding & Multi-Source Reasoning

**[9]. Table Meets LLM: Can Large Language Models Understand Structured Table Data?** (WSDM 2024)
*Authors: Microsoft Research*
**Summary:** Benchmark for LLM *comprehension* of tabular data across tasks: cell lookup, aggregation, column type detection, structure understanding. Evaluates GPT-4, ChatGPT, and open-source models.
**Why it's relevant:** The inverse of your problem: they test if LLMs can read tables; you test if LLMs can generate them. Together they frame the full LLM↔table capability space.
🔗 https://arxiv.org/abs/2305.13062

**[10]. ALT-GEN: Benchmarking Table Union Search using Large Language Models** (VLDB 2024 TaDA Workshop)
*Authors: Koyena Pal et al.*
**Summary:** Automated framework using LLMs to generate tables for table union search benchmarking. Includes a hallucination verification step to study extent of hallucination in LLM-generated benchmark data.
**Why it's relevant:** Very close cousin — they generate tables *with* LLMs to build a benchmark; you generate tables *from* LLM parametric knowledge as the task itself. Their hallucination verification approach is directly applicable to validating your ground-truth tables.
🔗 https://vldb.org/workshops/2024/proceedings/TaDA/TaDA.3.pdf

**[11]. How Large Language Models Will Disrupt Data Management** (VLDB 2023 vision)
*Authors: Raul Castro Fernandez*
**Summary:** Vision paper framing LLMs as implicit knowledge stores that can answer SQL-like queries over their parametric knowledge. Positions LLMs as a new kind of database engine.
**Why it's relevant:** This is the most direct theoretical framing for your benchmark's core premise. Citable as motivation: "LLMs store world knowledge implicitly; PKDB operationalizes this as a database primitive."
🔗 http://raulcastrofernandez.com/papers/llm_db_vision_vldb23-11.pdf

**[12]. Memorization vs. Reasoning: Updating LLMs with New Knowledge** (ACL 2025 Findings)
*Authors: Unknown (ACL 2025)*
**Summary:** Distinguishes when LLMs recall memorized facts vs. generalize/reason to produce correct answers. Finds scaling helps memorization more than reasoning for factual QA tasks.
**Why it's relevant:** Speaks to the core design tension in your query taxonomy: factual_lookup tests memorization, multi_hop/causal test reasoning. The distinction could define difficulty tiers in your benchmark.
🔗 https://aclanthology.org/2025.findings-acl.1326.pdf

---

## Cluster 5 — Hallucination in Structured Contexts

**[13]. HalluLens: LLM Hallucination Benchmark** (ACL 2025)
*Authors: Bang et al.*
**Summary:** Comprehensive hallucination benchmark distinguishing extrinsic (unsupported by training data) vs. intrinsic (contradicts training data) hallucination. Emphasizes the importance of refusing to answer when confronted with non-existent instances.
**Why it's relevant:** Your benchmark inherently measures hallucination at the table level — every spurious row or wrong cell value is a hallucination event. HalluLens's taxonomy (extrinsic vs. intrinsic) maps cleanly to your row-level correctness vs. column-level factual error distinction.
🔗 https://aclanthology.org/2025.acl-long.1176.pdf

**[14]. Simple Factuality Probes Detect Hallucinations in Long-Form Generation** (EMNLP 2025 Findings)
*Authors: Unknown*
**Summary:** Shows that simple factuality probes on individual claims can efficiently detect hallucinations in long-form text without expensive per-token verification.
**Why it's relevant:** Applicable to your validation pipeline — efficient cell-level hallucination detection for large tables.
🔗 https://aclanthology.org/2025.findings-emnlp.880.pdf

---

## Cluster 6 — LLM × Data Management (VLDB/SIGMOD Community)

**[15]. LLM for Data Management (Survey)** (VLDB 2024)
*Authors: Guoliang Li, Tsinghua University*
**Summary:** Survey on using LLMs across the data management pipeline: query understanding, schema matching, data cleaning, entity resolution, table understanding. Covers both LLMs-as-tools and LLMs-as-databases.
**Why it's relevant:** Positions your work within the DB community framing. Useful for identifying the right venue (VLDB, SIGMOD) and the right framing (LLM as implicit structured knowledge store).
🔗 https://www.vldb.org/pvldb/vol17/p4213-li.pdf

---

## Cluster 7 — 2026 arXiv Preprints

### Table Construction & Structured Extraction

**[16]. Beyond Isolated Dots: Benchmarking Structured Table Construction as Deep Knowledge Extraction** (arXiv 2507.16271)
*Authors: Unknown (preprint)*
**Summary:** Introduces the AOE (Arranged and Organized Extraction) Benchmark — bilingual, long-form documents averaging 171k tokens, 11 tasks across 3 domains. Tests LLMs' ability to comprehend fragmented information and reconstruct it into organized tables.
**Why it's relevant:** Closest 2026 cousin to your work. They test table reconstruction from documents; you test table generation from parametric knowledge. The "fragmented information → organized table" framing directly parallels your multi-source join/union setting.
🔗 https://arxiv.org/abs/2507.16271

**[17]. ExtractBench: A Benchmark and Evaluation Methodology for Complex Structured Extraction** (arXiv 2602.12247)
*Authors: Unknown (preprint)*
**Summary:** 35 PDFs paired with JSON Schemas and human-annotated gold labels, yielding 12,867 evaluatable fields across economically valuable domains. Introduces field-level grading methodology for structured extraction tasks.
**Why it's relevant:** The field-level grading methodology is directly applicable to your per-cell evaluation design. Their evaluation framework could serve as a template for scoring individual table cells rather than whole-table F1.
🔗 https://arxiv.org/abs/2602.12247

**[18]. LLMStructBench: Benchmarking Large Language Model Structured Data Extraction** (arXiv 2602.14743)
*Authors: Unknown (preprint)*
**Summary:** Systematic benchmark testing 22 models across 5 prompting strategies on extracting JSON from natural language. Includes diverse, manually verified parsing scenarios of varying complexity.
**Why it's relevant:** Shows the community is actively benchmarking LLM → structured output at scale (22 models). Your parametric-knowledge table extraction benchmark should similarly evaluate across model families and prompting strategies.
🔗 https://arxiv.org/abs/2602.14743

**[19]. arXiv2Table: Toward Realistic Benchmarking and Evaluation for LLM-Based Literature-Review Table Generation** (arXiv 2504.10284)
*Authors: Unknown (preprint)*
**Summary:** First benchmark simulating real-world scientific table generation with user demands and distractor papers. Assesses LLMs on multi-source synthesis where irrelevant papers must be filtered.
**Why it's relevant:** Multi-source synthesis with distractors parallels your multi-source join/union tables where LLM must distinguish and merge information across implicit sources. Their distractor setup could inspire harder variants of your benchmark.
🔗 https://arxiv.org/abs/2504.10284

**[20]. Beyond Known Facts: Generating Unseen Temporal Knowledge** (arXiv 2601.13658)
*Authors: Unknown (preprint)*
**Summary:** Explores whether LLMs can generate plausible facts beyond their training cutoff via temporal extrapolation. Investigates how parametric knowledge degrades near and past training cutoff dates.
**Why it's relevant:** Directly relevant to your `temporal` query category and the ground-truth design question: what happens to benchmark validity when facts (e.g., GDP, medal counts) change after the LLM's knowledge cutoff?
🔗 https://arxiv.org/abs/2601.13658

**[21]. Parametric Knowledge is Not All You Need: Toward Honest LLMs via Retrieval of Pretraining Data** (arXiv 2601.21218)
*Authors: Unknown (preprint)*
**Summary:** Argues parametric-only knowledge is insufficient for reliable factual generation; proposes grounding responses by retrieving pretraining data. Shows parametric recall degrades systematically for less prominent entities.
**Why it's relevant:** Frames the core limitation your benchmark measures. A direct foil: your benchmark deliberately stress-tests parametric-only extraction, and this paper characterizes when and why it fails — useful both as motivation and for interpreting benchmark results.
🔗 https://arxiv.org/abs/2601.21218

**[22]. Querying Structured Data Through Natural Language Using Language Models** (arXiv 2604.03057)
*Authors: Unknown (preprint)*
**Summary:** Trains LLMs to generate executable queries over structured non-textual datasets, contrasting RAG which struggles with numerical/structured data. Fine-tunes DeepSeek R1-Distill-8B via QLoRA for commodity hardware deployment.
**Why it's relevant:** The DB-side complement to your work — they ask "how do LLMs query existing structured data?" You ask "how do LLMs extract structured data from parametric knowledge?" Together they define the full LLM↔DB loop and could appear as related work in a DB-venue submission.
🔗 https://arxiv.org/abs/2604.03057

### Calibration & Confidence (2026 additions)

**[23]. Evaluating and Calibrating LLM Confidence on Questions with Multiple Correct Answers** (arXiv 2602.07842)
*Authors: Unknown (preprint)*
**Summary:** Introduces MACE benchmark evaluating LLM confidence across 6 factual domains using structured knowledge representations. Addresses the multiple-correct-answers problem where binary right/wrong scoring is inadequate.
**Why it's relevant:** Multiple-correct-answer confidence is exactly the challenge in your tables: a partially correct row (right entity, wrong numeric value) should receive partial credit. MACE's framework is a direct reference for your ECE metric design.
🔗 https://arxiv.org/abs/2602.07842

**[24]. Know When You're Wrong: Aligning Confidence with Correctness for LLM Error Detection** (arXiv 2603.06604)
*Authors: Unknown (preprint)*
**Summary:** Introduces normalized confidence scores and a self-evaluation framework. Demonstrates that well-calibrated confidence enables selective retrieval that captures most accuracy gains at a fraction of cost.
**Why it's relevant:** A model that knows when it doesn't know would output lower-confidence cells for obscure entities — exactly the behavior your ECE metric should reward. The selective output idea (output high-confidence cells, abstain on low-confidence ones) could be a key benchmark mode.
🔗 https://arxiv.org/abs/2603.06604

**[25]. Calibration Is Not Enough: Evaluating Confidence Estimation Under Language Variations** (arXiv 2601.08064)
*Authors: Unknown (preprint)*
**Summary:** Evaluates confidence estimation robustness against prompt perturbations, semantic equivalence, and sensitivity to semantically different answers. Finds calibration quality varies substantially across knowledge domains.
**Why it's relevant:** Per-domain calibration variation (e.g., Sports vs. Geography tables) is real and measurable. Validates that your benchmark should report ECE broken down by query category, not just overall — different category types will have different calibration profiles.
🔗 https://arxiv.org/abs/2601.08064

---

## Key Gaps / What's Missing in the Literature

1. **No benchmark tests multi-source parametric table synthesis** — all existing work tests recall from single-source knowledge (one Wikipedia article, one QA pair). Your multisources_dataset_v1 is genuinely novel here.
2. **No benchmark uses calibration-aware metrics at cell granularity** — FActScore decomposes to atomic facts but doesn't ask models to express per-cell confidence. ECE at cell level is unexplored.
3. **No query-type taxonomy for table extraction tasks** — your categories.txt is the only taxonomy I found that classifies table extraction by "query type" (factual_lookup → multi_hop → causal). Existing benchmarks are flat.
