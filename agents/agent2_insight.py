# agents/agent2_insight.py
# Agent 2: tools + final insight_output JSON.

from __future__ import annotations

import json
import os
from pathlib import Path

import pandas as pd

_JOURNAL_ANALYZER_ROOT = Path(__file__).resolve().parent.parent

from agents.correlations import compute_correlation_pair, find_correlations_all_pairs, list_metrics_impl
from context_builder import build_agent2_context_bundle, split_window_halves
from json_utils import extract_json_from_reply, normalize_insight_output
from ollama_client import run_tool_loop_until_text

TOOLS_AGENT2 = [
    {
        "type": "function",
        "function": {
            "name": "list_metrics",
            "description": "List available metric ids for correlation (registry).",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "compute_correlation",
            "description": "Compute Pearson correlation between two metrics over journal entries.",
            "parameters": {
                "type": "object",
                "required": ["metric_a", "metric_b"],
                "properties": {
                    "metric_a": {"type": "string", "description": "Metric id from list_metrics."},
                    "metric_b": {"type": "string", "description": "Metric id from list_metrics."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_correlations",
            "description": (
                "Compute Pearson correlations for all pairs of registered metrics (word count, "
                "char count, anxiety hits, mood-positive hits). Appends each pair result like compute_correlation. "
                "Use for exploration; use compute_correlation when you only need one specific pair."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
]

AGENT2_SYSTEM = """You are an insightful assistant analyzing personal journal entries over a date range.

You may call tools:
- list_metrics — discover metric ids.
- compute_correlation — get a quantitative correlation (r, n) between two metrics. Never invent correlation values in prose.
- find_correlations — run all pairwise correlations among registry metrics at once (exploratory).

After you have enough context from tools (or if correlations are unnecessary), respond with ONLY a JSON object (no markdown fences) matching this shape:
{
  "themes": [ {"name": string, "description": string, "salience": optional number 1-5, "order": optional integer} ],
  "emerging_patterns": [ string ],
  "fading_patterns": [ string ],
  "trends": [ string | {"label": string, "direction": "up"|"down"|"flat"|"unclear", "note": string} ],
  "query_answer": string,
  "confidence": number between 0 and 1,
  "insight_schema_version": 1
}

If the user did not ask a specific question, set "query_answer" to a short summary of themes.
Qualitative insights only unless backed by tool results for numbers.
Do not quote journal text verbatim."""


def resolved_agent2_system_message() -> str:
    """
    Default: built-in AGENT2_SYSTEM.
    Override via AGENT2_SYSTEM_PROMPT_PATH: UTF-8 file; path relative to JournalAnalyzer
    root unless absolute. Used by QC prompt sweeps and optional .env in deployment.
    """
    raw = (os.environ.get("AGENT2_SYSTEM_PROMPT_PATH") or "").strip()
    if not raw:
        return AGENT2_SYSTEM
    path = Path(raw)
    if not path.is_absolute():
        path = _JOURNAL_ANALYZER_ROOT / path
    if not path.is_file():
        print(
            f"Warning: AGENT2_SYSTEM_PROMPT_PATH not found ({path}); using built-in Agent 2 system prompt.",
            flush=True,
        )
        return AGENT2_SYSTEM
    try:
        text = path.read_text(encoding="utf-8-sig").strip()
    except OSError as exc:
        print(
            f"Warning: could not read AGENT2_SYSTEM_PROMPT_PATH ({path}): {exc}; using built-in.",
            flush=True,
        )
        return AGENT2_SYSTEM
    return text or AGENT2_SYSTEM


def run_agent2_insight(
    df_analysis: pd.DataFrame,
    user_query: str | None,
    model: str,
    correlation_sidecar: list,
    rag_journal_blob: str | None = None,
) -> tuple[dict, list]:
    """
    Run tool loop; parse insight_output. Appends to correlation_sidecar dicts from compute_correlation.
    Returns (normalize_insight_output dict, correlation_sidecar).
    """
    use_rag = bool(rag_journal_blob and str(rag_journal_blob).strip())
    if use_rag:
        bundle = str(rag_journal_blob).strip()
        journal_heading = "JOURNAL TEXT (semantic retrieval for your question/trends; not full diary):\n"
        half_note = ""
    else:
        bundle = build_agent2_context_bundle(df_analysis)
        journal_heading = "JOURNAL TEXT (chronological):\n"
        first, second = split_window_halves(df_analysis)
        half_note = ""
        if first and second:
            half_note = "\nFirst half of window and second half are available for emerging vs fading patterns.\n"
    uq = (user_query or "").strip()

    user_block = (
        f"{journal_heading}{bundle}\n"
        f"{half_note}\n"
        f"USER QUESTION (may be empty): {uq}\n"
    )

    def list_metrics(**_kwargs) -> dict:
        return list_metrics_impl()

    def compute_correlation(metric_a: str, metric_b: str) -> dict:
        out = compute_correlation_pair(df_analysis, metric_a, metric_b)
        correlation_sidecar.append(out)
        return out

    def find_correlations(**_kwargs) -> dict:
        runs = find_correlations_all_pairs(df_analysis)
        for run in runs:
            correlation_sidecar.append(run)
        return {"pairs": runs, "count": len(runs)}

    registry = {
        "list_metrics": list_metrics,
        "compute_correlation": compute_correlation,
        "find_correlations": find_correlations,
    }

    messages = [
        {"role": "system", "content": resolved_agent2_system_message()},
        {"role": "user", "content": user_block},
    ]

    final_text = run_tool_loop_until_text(
        messages,
        model=model,
        tools=TOOLS_AGENT2,
        tool_registry=registry,
        max_rounds=10,
        timeout=180,
    )
    if not final_text:
        return normalize_insight_output(None), correlation_sidecar
    parsed = extract_json_from_reply(final_text)
    return normalize_insight_output(parsed), correlation_sidecar
