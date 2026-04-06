# report_builder.py
# Multi-agent journal report: Agent 1 (K10) || Agent 2 (insights), then Agent 3 HTML.
# Used by app.py when the user clicks "Generate report".

from __future__ import annotations

import os
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import pandas as pd

from agents.agent1_k10 import run_agent1_k10
from agents.agent2_insight import run_agent2_insight
from agents.agent3_merge import build_agent3_report
from context_builder import (
    format_k10_per_item_rag_prompt,
    format_rag_chunks_for_prompt,
    slice_last_n_calendar_days,
)
from json_utils import normalize_insight_output
from snapshot import fetch_k10_snapshots, save_k10_snapshot

# Directory for saved reports (next to this file)
_REPORTS_DIR = Path(__file__).resolve().parent / "reports"


def _ensure_reports_dir():
    _REPORTS_DIR.mkdir(parents=True, exist_ok=True)


def _wants_trend_chart(user_question: str | None, trend_keywords: list[str]) -> bool:
    if trend_keywords:
        return True
    if not user_question:
        return False
    t = user_question.lower()
    return any(k in t for k in ("trend", "over time", "monthly", "each month", "over the months"))


def _run_rag_context(
    entries_df: pd.DataFrame,
    trend_keywords: list[str],
    user_question: str | None,
    include_k10_section: bool,
    status_callback: Callable[[str], None] | None,
) -> tuple[str | None, str | None]:
    """
    Incremental embed + retrieval. Returns (rag_blob_for_agent1, rag_blob_for_agent2).
    Either may be None if disabled, empty retrieval, or error (caller falls back to plaintext).
    """
    from embedding_pipeline import ensure_embeddings_for_entries, rag_available

    if not rag_available() or entries_df.empty:
        return None, None
    try:
        if status_callback:
            status_callback("Indexing journal passages…")
        ensure_embeddings_for_entries(entries_df)
    except Exception:
        return None, None

    from k10_utils import K10_RAG_QUERIES
    from retrieval import (
        build_agent2_rag_queries,
        date_range_from_dataframe,
        retrieve_k10_per_item_rows,
        retrieve_merged,
    )

    rag_a1: str | None = None
    rag_a2: str | None = None

    k10_top_k = int(os.environ.get("RAG_K10_TOP_K", "10"))
    a2_top_k = int(os.environ.get("RAG_AGENT2_TOP_K", "15"))
    k10_budget = int(os.environ.get("RAG_K10_CHAR_BUDGET", "12000"))
    a2_budget = int(os.environ.get("RAG_AGENT2_CHAR_BUDGET", "120000"))

    df_k10 = slice_last_n_calendar_days(entries_df, 30)
    if include_k10_section and not df_k10.empty:
        d0, d1 = date_range_from_dataframe(df_k10)
        if d0 and d1:
            try:
                rows_per_item = retrieve_k10_per_item_rows(
                    list(K10_RAG_QUERIES),
                    d0,
                    d1,
                    top_k_per_query=k10_top_k,
                )
                if any(rows_per_item):
                    blob = format_k10_per_item_rag_prompt(
                        rows_per_item,
                        list(K10_RAG_QUERIES),
                        total_char_budget=k10_budget,
                    )
                    if blob.strip():
                        rag_a1 = blob
            except Exception:
                rag_a1 = None

    d2a, d2b = date_range_from_dataframe(entries_df)
    q2 = build_agent2_rag_queries(user_question, trend_keywords)
    if d2a and d2b and q2:
        try:
            rows2 = retrieve_merged(q2, d2a, d2b, top_k_per_query=a2_top_k)
            blob2 = format_rag_chunks_for_prompt(
                rows2,
                "Retrieved passages for your question and trend keyword(s).",
                char_budget=a2_budget,
            )
            if blob2.strip():
                rag_a2 = blob2
        except Exception:
            rag_a2 = None

    return rag_a1, rag_a2


def build_report(
    entries_df: pd.DataFrame,
    trend_keywords: list[str],
    api_key: str | None,
    date_from,
    date_to,
    user_question: str | None = None,
    include_k10_section: bool = True,
    include_k10_trends: bool = False,
    status_callback: Callable[[str], None] | None = None,
) -> str:
    """
    Multi-agent pipeline: Agent 1 (K10) || Agent 2 (insights), then Agent 3 HTML.
    Writes HTML and returns the file path.
    """
    _ensure_reports_dir()
    filename = f"journal_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html"
    out_path = _REPORTS_DIR / filename

    model1 = os.environ.get("OLLAMA_MODEL_AGENT1", "nemotron-3-nano:30b-cloud")
    model2 = os.environ.get("OLLAMA_MODEL_AGENT2", model1)

    correlation_sidecar: list = []

    k10_payload = None
    insight: dict = {}
    if api_key:
        rag_a1: str | None = None
        rag_a2: str | None = None
        try:
            rag_a1, rag_a2 = _run_rag_context(
                entries_df,
                trend_keywords,
                user_question,
                include_k10_section,
                status_callback,
            )
        except Exception:
            rag_a1, rag_a2 = None, None
        if status_callback:
            status_callback("Generating AI summaries…")

        def run_a1():
            return run_agent1_k10(entries_df, model1, rag_diary_blob=rag_a1)

        def run_a2():
            return run_agent2_insight(
                entries_df, user_question, model2, correlation_sidecar, rag_journal_blob=rag_a2
            )

        if include_k10_section:
            with ThreadPoolExecutor(max_workers=2) as pool:
                f1 = pool.submit(run_a1)
                f2 = pool.submit(run_a2)
                k10_payload = f1.result()
                insight, _ = f2.result()
        else:
            insight, _ = run_agent2_insight(
                entries_df, user_question, model2, correlation_sidecar, rag_journal_blob=rag_a2
            )
    else:
        insight = normalize_insight_output(None)

    k10_for_doc: dict | None = None
    if k10_payload and include_k10_section:
        df_k10 = slice_last_n_calendar_days(entries_df, 30)
        save_k10_snapshot(k10_payload, df_k10, model1)
        k10_for_doc = dict(k10_payload)
        if not df_k10.empty:
            dmin = df_k10["date"].min()
            dmax = df_k10["date"].max()
            k10_for_doc["data_source"] = {
                "entry_count": len(df_k10),
                "period_start": pd.Timestamp(dmin).strftime("%Y-%m-%d"),
                "period_end": pd.Timestamp(dmax).strftime("%Y-%m-%d"),
                "recent_days": 30,
            }
        else:
            k10_for_doc["data_source"] = {
                "entry_count": 0,
                "period_start": None,
                "period_end": None,
                "recent_days": 30,
            }

    k10_history = fetch_k10_snapshots(80) if include_k10_trends else None
    want_trend = _wants_trend_chart(user_question, trend_keywords)

    correlation_tools_used = len(correlation_sidecar) > 0

    full_doc = build_agent3_report(
        entries_df=entries_df,
        date_from=date_from,
        date_to=date_to,
        k10_payload=k10_for_doc if include_k10_section else None,
        insight=insight,
        correlation_sidecar=correlation_sidecar,
        include_k10_section=include_k10_section,
        include_k10_trends=include_k10_trends,
        user_query=user_question,
        want_trend_chart=want_trend,
        trend_keywords=trend_keywords,
        k10_history=k10_history,
        correlation_tools_used=correlation_tools_used,
    )

    with open(out_path, "w", encoding="utf-8") as f:
        f.write(full_doc)

    return str(out_path)
