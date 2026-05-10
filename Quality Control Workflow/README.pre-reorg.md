# Quality Control Workflow (v1)

Batch runner for the JournalAnalyzer multi-agent pipeline: compare **LLM models** (`OLLAMA_MODEL_AGENT1` / `AGENT2` / `AGENT3`) and optional **`rag_env`** overrides, grade each HTML report with **`gpt-oss:20b-cloud`**, and append results to **`qc_experiment_scores.csv`**.

**Scope (v1):** Only model + RAG factors are meant to vary across `variants:`; keep `shared:` inputs identical across variants for a fair comparison.

## Setup

1. Install JournalAnalyzer dependencies (see `../requirements.txt`) plus QC extras:

   ```bash
   # recommended: activate your venv first (e.g. .venv)
   python -m pip install -r "../requirements.txt"
   python -m pip install -r "requirements-qc.txt"
   ```

2. Edit [`qc_config.yaml`](qc_config.yaml) for dates, `variants`, and `qc_journal_context.mode` (no secrets in this file—use `.env` for keys).

   - `shared.date_from` / `shared.date_to`: strings in `YYYY-MM-DD`
   - `shared.trend_keywords`: list of strings
   - `shared.user_question`: string (or null). If you don't want a question, omit it or set it to an empty string.

3. Environment (same as the app; use `JournalAnalyzer/.env`):

   - `OLLAMA_API_KEY`, `OLLAMA_HOST` — Ollama Cloud
   - `JOURNAL_API_URL` — FastAPI journal API (default `http://127.0.0.1:8000`)
   - `SUPABASE_*` — if you use RAG (embeddings + retrieval)

4. Start the Journal Analyzer API so `load_entries` can fetch rows (or point `JOURNAL_API_URL` at a running instance).

   ```bash
   python -m uvicorn api:app --reload --port 8000
   ```

## Run

From the **`JournalAnalyzer`** directory:

```bash
python "Quality Control Workflow/run_variants.py" --config "Quality Control Workflow/qc_config.yaml" --csv "Quality Control Workflow/qc_experiment_scores.csv"
```

Notes:

- If `OLLAMA_API_KEY` is unset, `build_report` will **skip LLM agents**, and you won't be QC'ing the full pipeline.
- After the **first** completed run, the script prints a short raw JSON + parsed metrics summary.

## RAG vs no-RAG experiment

**Separate this from LLM-comparison runs.** Comparing models (`variants` with different `OLLAMA_MODEL_*`) belongs in one campaign with a **stable** RAG environment and a **single** output CSV (e.g. `qc_experiment_scores_llm.csv`). Studying retrieval vs no retrieval is a **different** question: fix the models and `shared:` inputs, run twice with the same `qc_config`, and write **two** CSVs so results are not confounded with a multi-model grid.

**Prerequisite:** Journal Analyzer API is running (`JOURNAL_API_URL`); journal rows still come from the API. The RAG on/off script only changes env in the **QC batch process** (not the API server) — this affects **report generation** (`build_report(...)`), not the QC grader (which always evaluates the final HTML report text).

1. Copy or adapt [`qc_config.rag_ab.example.yaml`](qc_config.rag_ab.example.yaml) to a fixed-model config (one variant, `rag_env: {}`), or point `--config` at such a file.
2. From **`JournalAnalyzer`**:

   ```bash
   python "Quality Control Workflow/run_qc_rag_ab.py" \
     --config "Quality Control Workflow/qc_config.rag_ab.example.yaml" \
     --csv-on "Quality Control Workflow/qc_experiment_scores_rag_on.csv" \
     --csv-off "Quality Control Workflow/qc_experiment_scores_rag_off.csv"
   ```

   Pass 1 keeps `SUPABASE_*` from `.env` (RAG available when the embedding stack is satisfied). Pass 2 unsets those keys in the subprocess so `rag_available()` is false; if `EMBEDDING_BACKEND=openai`, `OPENAI_API_KEY` is also unset for that pass. Use `--dry-run` to print which keys are stripped without running batches.

3. Compare the two CSVs (same row schema as `run_variants.py`: one QC row per variant × replicate).

**Caveat:** Two sequential batches are two independent draws of LLM stochasticity; raise `replicates_per_variant` if you need tighter comparison.

## Outputs

| Metric | Source |
|--------|--------|
| `k10_total` | Parsed from report HTML (`k10_report_parse.py`) |
| `frequency_rubric_mean`, `evidence_relevance_mean`, `hallucination` | LLM JSON (`qc_grader.py`); means recomputed from per-item JSON in `parse_qc_json` |

**CSV:** one row per `variant_label` × `replicate_index` (single grader pass per report). `replicate_index` is **0-based** (`0..replicates_per_variant-1`). If an existing `qc_experiment_scores.csv` was created by the old two-tier workflow (header includes `qc_level`), delete or rename it, or use a new `--csv` path — the runner refuses to append mixed schemas.

## Statistical comparison

```bash
python "Quality Control Workflow/statistical_comparison.py" --csv "Quality Control Workflow/qc_experiment_scores.csv" --dv frequency_rubric_mean
```

Legacy CSVs that still have a `qc_level` column are filtered to `end_to_end` automatically.

## Modules

| File | Role |
|------|------|
| `qc_config.yaml` | QC experiment parameters (committed; no API keys) |
| `qc_config.rag_ab.example.yaml` | Example single-variant config for RAG on/off runs |
| `run_variants.py` | Main batch driver |
| `run_qc_rag_ab.py` | Two-pass runner (RAG on vs off, two CSVs) |
| `load_entries.py` | API → DataFrame |
| `journal_context.py` | Journal text for grader from `qc_journal_context.mode` |
| `k10_report_parse.py` | HTML → total score + plain text strip |
| `qc_prompt.py` / `qc_grader.py` | Prompts + Ollama JSON grader |
| `rag_context_for_qc.py` | (Unused by `run_variants`; kept for optional RAG-specific tooling) |
| `aggregate_results.py` | CSV append |
| `statistical_comparison.py` | Group tests by `variant_label` |

Optional env: `OLLAMA_MODEL_QC_GRADER` (default `gpt-oss:20b-cloud`).

## Future extensions

Additional experimental dimensions (Agent 3-only sweeps, date windows, user questions, etc.) are **out of scope for v1**; see the project plan for ideas.
