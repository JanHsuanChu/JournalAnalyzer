# rag_context_for_qc.py
# Reuse the app’s RAG path to build retrieval text for tier-2 QC.

from __future__ import annotations

from collections.abc import Callable

import pandas as pd

from paths import ensure_ja_on_path

ensure_ja_on_path()

from embedding_pipeline import rag_available  # noqa: E402
from report_builder import _run_rag_context  # noqa: E402


def build_rag_context_string_for_qc(
    entries_df: pd.DataFrame,
    trend_keywords: list[str],
    user_question: str | None,
    include_k10_section: bool,
    status_callback: Callable[[str], None] | None = None,
) -> str | None:
    """
    Concatenate Agent1 + Agent2 retrieval blobs (same as build_report internal RAG).
    Returns None if RAG unavailable or empty.
    """
    if not rag_available() or entries_df is None or entries_df.empty:
        return None
    try:
        a1, a2 = _run_rag_context(
            entries_df,
            trend_keywords,
            user_question,
            include_k10_section,
            status_callback,
        )
    except Exception:
        return None
    parts: list[str] = []
    if a1 and str(a1).strip():
        parts.append("--- K10-oriented retrieval ---\n" + str(a1).strip())
    if a2 and str(a2).strip():
        parts.append("--- Agent 2 retrieval (question/trends) ---\n" + str(a2).strip())
    if not parts:
        return None
    return "\n\n".join(parts)
