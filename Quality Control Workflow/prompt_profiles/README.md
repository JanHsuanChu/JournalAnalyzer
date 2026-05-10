# Agent 1 system prompt profiles (QC experiments)

Markdown files referenced by `AGENT1_SYSTEM_PROMPT_PATH` (see [`agents/agent1_k10.py`](../../agents/agent1_k10.py)) for **prompt sweep** QC runs (`prompt_env` in `qc_config`).

## Core three prompts (compare these for Agent 1 wording / policy)

| File | Role |
|------|------|
| **`agent1_baseline.md`** | Reference—matches built-in `AGENT1_SYSTEM` intent. **Use this profile for non–prompt QC** (model sweeps, `rag_env`, RAG A/B) so Agent 1 stays default while you vary LLM or retrieval. |
| **`agent1_alternative_explicit_calibration.md`** | Alternative **A** — strict calibration (anchor → ±1 severity, 4–5 breadth gate, stem guardrails). |
| **`agent1_alternative_rhetoric.md`** | Alternative **B** — baseline scoring policy; plain English, positive isolation workflow, numbered frequency/severity steps, exemplar evidence. |

Full comparison table and pairwise interpretation: [`agent1_alternative_rationale.md`](agent1_alternative_rationale.md).

## Optional

- **`agent1_variant_b_example.md`** — Single extra tie-break line on baseline; for quick demos only, not the main 3-way design.

After changing the canonical prompt in [`agents/agent1_k10.py`](../../agents/agent1_k10.py) (`AGENT1_SYSTEM`), refresh **`agent1_baseline.md`** if you want the file copy to stay identical to code.
