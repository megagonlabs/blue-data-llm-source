# Calibration Metrics for LLM Knowledge Benchmarks

**Question:** Is there an existing benchmark that uses ECE as a *primary evaluation metric* to penalize LLM predictions on factual parametric knowledge?

**Short answer: No — and that is the gap.**

---

## What the Literature Does

### Camp 1 — ECE as training/fine-tuning loss (majority of papers)
Most calibration papers use ECE or Brier score as the *target to minimize* when training or fine-tuning a model to be better calibrated. ECE is an optimization objective, not an evaluation benchmark metric.

### Camp 2 — ECE as secondary metric in QA benchmarks
A few papers report ECE alongside accuracy, but it is always secondary:

- **QA-Calibration** (ICLR 2025, Amazon) — evaluates confidence scores elicited from LLMs on factual QA; reports ECE, but the benchmark asks *how to elicit* confidence, not how to penalize overconfident wrong answers.
  → https://assets.amazon.science/6d/70/c50b2eb141d3bcf1565e62b60211/qa-calibration-of-language-model-confidence-scores.pdf

- **Benchmarking Uncertainty Calibration in LLM Long-Form QA** (arXiv 2602.00279, 2026) — first large-scale benchmark comparing UQ methods with ECE. Key finding: **"relying exclusively on ECE is misleading"** — verbalized confidence is systematically biased; consistency-based methods (sample multiple outputs, measure agreement) are more reliable signals.
  → https://arxiv.org/abs/2602.00279

- **Mind the Confidence Gap** (arXiv 2502.11028) — evaluates 9 LLMs on 3 factual QA datasets with ECE, but no abstention or penalty mechanism.

- **MACE** (arXiv 2602.07842) — evaluates LLM confidence across 6 factual domains for questions with multiple correct answers; closest to the partial-credit scoring problem in structured tables.

### Camp 3 — Selective prediction / abstention (closest to penalizing overconfidence)
- **SelectLLM** (OpenReview) — penalizes coverage loss from abstaining too often; optimizes coverage-accuracy tradeoff. Framed as selective prediction, not ECE directly.
- **Know Your Limits** (TACL 2025) — survey of abstention strategies; discusses penalizing overconfident wrong answers but defines no standard benchmark metric.

---

## Key Findings from 2026 Papers

From arXiv 2602.00279 (the most relevant recent benchmark):

1. **Verbalized confidence** ("I am 80% sure") is poorly calibrated and systematically biased — avoid relying on it as the confidence signal.
2. **Consistency-based methods** (sample multiple outputs, measure agreement across samples) yield the most reliable calibration signal.
3. **ECE alone is misleading** as a single metric — should be paired with a coverage/utility metric such as AUACC or AURC.
4. **Token-level probabilities** are unreliable after instruction tuning due to probability mass polarization.

---

## The Gap

**No benchmark combines ECE with a task-level score at cell/field granularity for parametric knowledge.**

FActScore (EMNLP 2023) is the closest analog — it decomposes generated text into atomic facts and measures precision — but has no confidence dimension at all. The full space of (accuracy, confidence) at cell level is unexplored.

---

## Proposed Metric Design

Rather than raw ECE, two better-motivated options:

### Option A — Accuracy-Coverage / Risk-Coverage Curves
- Model outputs a confidence score per cell (via consistency sampling or logit-based)
- Sort cells by confidence and evaluate prefix accuracy as coverage increases
- Plot accuracy-coverage and risk-coverage curves
- Summary scalars: AUACC (higher is better) and AURC (lower is better)

### Option B — Composite Reward-Penalty Score
```
Score = mean over cells of:
    +confidence   if cell is correct
    −confidence   if cell is wrong
    0             if abstained
```
This directly operationalizes "reward confident-correct, penalize confident-wrong, allow abstention."

### Confidence Signal Recommendation
Prefer **consistency-based confidence**: generate the table k times (e.g., k=5), measure cell-level agreement across samples. More reliable than verbalized or token-level probabilities per the 2026 benchmark findings.

---

## Relation to This Project

The multisources_dataset_v1 currently evaluates with standard F1 (key F1, cell F1). Adding a calibration-aware metric would be genuinely novel:
- No existing table generation benchmark reports ECE plus AUACC/AURC at cell level
- The query category taxonomy (factual_lookup → multi_hop → causal) predicts different calibration profiles per category — this is a testable hypothesis
- Harder categories (multi_hop, causal, geographic) should show higher ECE and lower consistency, which is a meaningful result even without a new training method

---

*Analysis date: 2026-04-14*
*References: papers_pkdb_benchmark.md (md_claude_code/)*
