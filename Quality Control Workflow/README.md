# Quality Control Workflow (v1)

Batch runner for the JournalAnalyzer multi-agent pipeline: compare **LLM models** (`OLLAMA_MODEL_AGENT1` / `AGENT2` / `AGENT3`) and optional **`rag_env`** overrides, grade each HTML report with an AI reviewer (default `gpt-oss:20b-cloud`), and append results to a CSV.

**Scope (v1):** Only model + RAG factors are meant to vary across `variants:`; keep `shared:` inputs identical across variants for a fair comparison.

## Validation Criteria Table

Table summarizing each evaluation dimension used by the AI reviewer.

| Dimension | Description | Scale / measurement | Benchmark |
|---|---|---|---|
| `k10_total` | K10 total score extracted from the generated HTML report. | Parsed from report HTML (`k10_report_parse.py`). | **TODO(benchmark):** define acceptable error tolerance vs ground truth (manual scoring or known dataset). |
| `frequency_rubric_mean` | How well the report’s per-item “frequency” assessments align with the journal evidence. | Mean of 10 per-item integer ratings (1–5) returned by the reviewer JSON (`qc_grader.py`). | **TODO(benchmark):** define what “good” looks like (e.g., ≥ 4.0). |
| `evidence_relevance_mean` | How relevant / grounded the report’s evidence is to the journal text. | Mean of 10 per-item integer ratings (1–5) returned by the reviewer JSON (`qc_grader.py`). | **TODO(benchmark):** define target threshold and/or acceptable variance. |
| `hallucination` | Whether the report includes hallucinated claims not supported by the journal. | Boolean from reviewer JSON (`qc_grader.py`). | **TODO(benchmark):** define tolerance (ideally always `false`) and how to treat borderline cases. |
| `details` | Free-text rationale / notes from the reviewer. | String from reviewer JSON (`qc_grader.py`). | N/A (qualitative). |

**How this differs from the LAB’s Likert scales**

- The LAB Likert items are typically **human** ratings anchored to subjective agreement (e.g., 1–5 strongly disagree → strongly agree).
- This QC workflow uses an **AI reviewer** with a **structured JSON schema** and per-item rubric ratings (still 1–5, but tied to *specific report claims vs journal evidence*), plus a binary hallucination flag.
- **TODO(lab-comparison):** add your exact LAB dimensions and explain the mapping (or why there is no direct mapping).

## Experimental Design

**What was compared**

- Primary factor: LLM configuration via `variants:` in [`qc_config.yaml`](qc_config.yaml)
  - `OLLAMA_MODEL_AGENT1`, `OLLAMA_MODEL_AGENT2`, `OLLAMA_MODEL_AGENT3`
  - optional per-variant `rag_env` overrides
- Controlled inputs: `shared:` in `qc_config.yaml` (dates, keywords, question, etc.) remain constant across variants.

**Prompts compared**

- **TODO(prompts):** document the exact prompts used by (a) report generation agents and (b) the QC reviewer, and what changed across variants (if anything besides models/RAG).

**How many validation scores per prompt / per variant**

- One QC row is produced per `variant_label × replicate_index` (one reviewer pass per generated report).
- `replicate_index` is **0-based** (`0..replicates_per_variant-1`).

**Sample size**

- Reports per variant: `replicates_per_variant` (from `qc_config.yaml`)
- Total QC rows: `len(variants) × replicates_per_variant`
- Underlying journal sample: entries returned by the running API, filtered to `shared.date_from..shared.date_to`.

## Statistical Analysis

The repo provides a script for group comparison:

```bash
python "Quality Control Workflow/statistical_comparison.py" --csv "Quality Control Workflow/qc_experiment_scores.csv" --dv frequency_rubric_mean
```

**Hypotheses**

- **TODO(hypotheses):** write the null/alternative hypotheses (e.g., “Variant A has higher mean `frequency_rubric_mean` than Variant B”).

**Test choice**

- **TODO(test-choice):** specify whether you are using a t-test, ANOVA, regression, or non-parametric alternative, and why (independence, variance, multiple comparisons, etc.).

**Results + interpretation**

- **TODO(results):** paste the test output (or summary) here.
- **TODO(interpretation):** interpret effect size + practical significance for model selection / system improvements.

## System Design

High-level flow:

1. QC runner loads journal entries via the running API (`load_entries.py` → `JOURNAL_API_URL`), then filters them to the configured date window.
2. For each `variant × replicate`:
   - Applies variant env (`OLLAMA_MODEL_AGENT*` + optional `rag_env`).
   - Builds an HTML report (`report_builder.build_report(...)`) using the JournalAnalyzer pipeline.
   - Parses `k10_total` from the generated HTML and converts HTML → plain text.
   - Calls the **AI reviewer** (`qc_grader.py`) to grade the report and return structured JSON.
3. Appends a single row to the CSV for that run (`aggregate_results.py`).

**AI reviewer’s role**

- The reviewer reads (a) a derived `journal_text` (controlled by `qc_journal_context.mode`) and (b) the generated report (plain text).
- It outputs JSON with per-item rubric scores (1–5), a hallucination flag, and free-text details.
- Optional env: `OLLAMA_MODEL_QC_GRADER` (default `gpt-oss:20b-cloud`).

## Technical Details

### Environment variables

- **Required for full pipeline**:
  - `OLLAMA_API_KEY`, `OLLAMA_HOST` (Ollama Cloud)
  - `JOURNAL_API_URL` (FastAPI journal API; default `http://127.0.0.1:8000`)
- **Required for RAG-enabled report generation**:
  - `SUPABASE_URL`, `SUPABASE_KEY`, `SUPABASE_SERVICE_ROLE_KEY`
  - (if `EMBEDDING_BACKEND=openai`) `OPENAI_API_KEY`

### Config file expectations

- `shared.date_from` / `shared.date_to`: strings in `YYYY-MM-DD`
- `shared.trend_keywords`: list of strings
- `shared.user_question`: string (or null). If you don't want a question, omit it or set it to an empty string.

### File structure (QC modules)

| File | Role |
|------|------|
| `qc_config.yaml` | QC experiment parameters (committed; no API keys) |
| `qc_config.rag_ab.example.yaml` | Example single-variant config for RAG on/off runs |
| `run_variants.py` | Main batch driver |
| `run_qc_rag_ab.py` | Two-pass runner (RAG on vs off, two CSVs) |
| `load_entries.py` | API → DataFrame |
| `journal_context.py` | Journal text for reviewer from `qc_journal_context.mode` |
| `k10_report_parse.py` | HTML → total score + plain text strip |
| `qc_prompt.py` / `qc_grader.py` | Prompts + Ollama JSON reviewer |
| `rag_context_for_qc.py` | (Unused by `run_variants`; kept for optional RAG-specific tooling) |
| `aggregate_results.py` | CSV append |
| `statistical_comparison.py` | Group tests by `variant_label` |

## Usage Instructions

### Install dependencies

From `JournalAnalyzer/`:

```bash
# recommended: activate your venv first (e.g. .venv)
python -m pip install -r "requirements.txt"
python -m pip install -r "Quality Control Workflow/requirements-qc.txt"
```

### Start the API (required)

```bash
python -m uvicorn api:app --reload --port 8000
```

### Run validations (batch QC across variants)

```bash
python "Quality Control Workflow/run_variants.py" \
  --config "Quality Control Workflow/qc_config.yaml" \
  --csv "Quality Control Workflow/qc_experiment_scores.csv"
```

Notes:

- If `OLLAMA_API_KEY` is unset, `build_report` will **skip LLM agents**, and you won't be QC'ing the full pipeline.
- The runner refuses to append mixed schemas. If your existing CSV header includes `qc_level`, use a new `--csv` path (or rename/remove the old file).

### RAG vs no-RAG experiment (A/B)

**Separate this from LLM-comparison runs.** Comparing models (`variants` with different `OLLAMA_MODEL_*`) belongs in one campaign with a **stable** RAG environment and a **single** output CSV. Studying retrieval vs no retrieval is a **different** question: fix the models and `shared:` inputs, run twice with the same `qc_config`, and write **two** CSVs.

**Prerequisite:** Journal Analyzer API is running (`JOURNAL_API_URL`); journal rows still come from the API. The RAG on/off script only changes env in the **QC batch process** (not the API server) — this affects **report generation** (`build_report(...)`), not the QC grader (which always evaluates the final HTML report text).

```bash
python "Quality Control Workflow/run_qc_rag_ab.py" \
  --config "Quality Control Workflow/qc_config.rag_ab.example.yaml" \
  --csv-on "Quality Control Workflow/qc_experiment_scores_rag_on.csv" \
  --csv-off "Quality Control Workflow/qc_experiment_scores_rag_off.csv"
```

Pass 1 keeps `SUPABASE_*` from `.env` (RAG available when the embedding stack is satisfied). Pass 2 unsets those keys in the subprocess so `rag_available()` is false; if `EMBEDDING_BACKEND=openai`, `OPENAI_API_KEY` is also unset for that pass. Use `--dry-run` to print which keys are stripped without running batches.

**Caveat:** Two sequential batches are two independent draws of LLM stochasticity; raise `replicates_per_variant` if you need tighter comparison.

## Additional notes / legacy

- Legacy CSVs that still have a `qc_level` column are filtered to `end_to_end` automatically by `statistical_comparison.py`.

## Future extensions

Additional experimental dimensions (Agent 3-only sweeps, date windows, user questions, etc.) are **out of scope for v1**; see the project plan for ideas.
