### Fixed Decisions (Lock These Now)

1. Task: binary event classification (active vs inactive).
2. Language: Spanish text as-is (no translation pipeline).
3. Splitting: group split by conflict_id (no conflict overlap across train/test).
4. Model set:

- TF-IDF + Logistic Regression
- TF-IDF + Linear SVM
- Sentence embeddings + Logistic Regression
- One fine-tuned compact transformer (BETO or multilingual DistilBERT; pick one and stick to it)

5. LLM set:

- Zero-shot label-only prompt
- Zero-shot rubric prompt
- Few-shot prompt (train-only examples)
- Structured JSON output prompt

6. Multi-agent: A argues active, B argues inactive, C judges; evaluate only against best single-
    prompt baseline.
7. Metrics: Macro-F1 (primary), per-class recall/F1, balanced accuracy, PR-AUC (minority class),
    confusion matrix.
8. Timebox rule: no extra model families, no heavy hyperparameter sweeps.




### Day-by-Day Detailed To-Do

  1. Day 1: Data audit + final dataset build

  - Build one canonical table with: event_id, conflict_id, event_text, conflict_description, label,
    date, optional metadata.
  - Remove duplicates and empty/near-empty event texts.
  - Compute and save: class balance, text length stats, per-conflict event counts.
  - Leakage check: flag obvious label words in text and repeated template strings.
  - Deliverables:
  - data_audit.md with counts, class ratio, risks.
  - events_master dataset file.
  - Acceptance criteria:
  - You can reproduce total rows and class counts from one script/notebook cell.

  2. Day 2: Split strategy + preprocessing + baseline #1

  - Create fixed train/val/test grouped by conflict.
  - Save split assignments to disk (frozen split artifact).
  - Implement text preprocessing choices (minimal normalization, Spanish stopword policy).
  - Train/evaluate TF-IDF + Logistic Regression with class weights.
  - Threshold tuning on validation set for minority recall vs precision tradeoff.
  - Deliverables:
  - split_report.md showing zero conflict overlap.
  - Baseline results table row for LR.
  - Acceptance criteria:
  - Same metrics reproduced with fixed random seed.

  3. Day 3: Baseline #2 + embedding baseline + error slices

  - Train/evaluate TF-IDF + Linear SVM.
  - Train/evaluate sentence embeddings + Logistic Regression.
  - Compare all classical models in one table.
  - Run error analysis slices: short vs long text, by year, by region/actor metadata if available.
  - Deliverables:
  - model_comparison_classical.csv.
  - error_analysis_classical.md.
  - Acceptance criteria:
  - One clear “best classical” model selected for later comparison.

  4. Day 4: Transformer fine-tuning

  - Fine-tune one compact transformer with early stopping and class weighting.
  - Evaluate on frozen test split.
  - Save confusion matrix + per-class metrics.
  - Add simple calibration or threshold adjustment if minority recall is poor.
  - Deliverables:
  - Transformer results row in master benchmark.
  - Saved model card notes (epochs, batch size, LR, runtime).
  - Acceptance criteria:
  - Transformer result is reproducible and directly comparable to classical baselines.

  5. Day 5: Interpretation layer (must be useful)

  - Topic analysis on event text (e.g., BERTopic or NMF; pick one primary method).
  - Compute topic prevalence difference between active/inactive.
  - Keyword analysis: discriminative n-grams via coefficient/log-odds/chi-square.
  - Optional sentiment analysis only as auxiliary analysis with caution note.
  - Deliverables:
  - top predictive words/phrases,
  - Acceptance criteria:
  - Interpretation outputs connect explicitly to model behavior and policy narrative.

  6. Day 6: OpenAI prompt experiment + multi-agent extension

  - Implement 4 prompt styles (zero-shot simple, zero-shot rubric, few-shot, structured JSON).
  - Run on same frozen test set (or fixed evaluation subset if cost constrained).
  - Track cost, latency, invalid-format rate, and metrics.
  - Multi-agent run:
  - Agent A argument (active)
  - Agent B argument (inactive)
  - Agent C final label
  - Compare multi-agent vs best single-prompt under similar token budget.
  - Deliverables:
  - llm_prompt_ablation.csv.
  - agent_vs_single_prompt.csv.
  - Acceptance criteria:
  - Fair comparison protocol documented (same data slice, same metric set).

  7. Day 7: Integration + writing + presentation

  - Final benchmark table across all families (classical, transformer, LLM, multi-agent).
  - Write limitations: imbalance, reporting bias, non-causal interpretation, external validity.
  - Build final figures:
  - confusion matrix (best non-LLM),
  - prompt comparison chart,
  - interpretation chart (topics/keywords).
  - Prepare final report/presentation narrative: method, results, policy insights.
  - Deliverables:
  - Final report draft.
  - Slide deck with reproducible figures/tables.
  - Acceptance criteria:
  - A reviewer can follow pipeline end-to-end without guessing missing decisions.