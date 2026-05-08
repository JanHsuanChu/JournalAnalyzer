# journal_context.py
# Build journal text blobs for the QC grader from config `qc_journal_context.mode`.

from __future__ import annotations

import pandas as pd

from paths import ensure_ja_on_path

ensure_ja_on_path()

from context_builder import (  # noqa: E402
    format_entries_with_ids,
    slice_last_n_calendar_days,
)


def build_journal_text_for_qc(
    entries_df: pd.DataFrame,
    mode: str,
) -> str:
    """
    modes:
      analysis_window — use the full analysis DataFrame (already date-filtered).
      k10_last_30 — last 30 calendar days within that frame (matches Agent 1 window).
      both_labeled_sections — two labeled blocks for the grader.
    """
    mode = (mode or "analysis_window").strip().lower()
    if entries_df is None or entries_df.empty:
        return "(No journal entries in range.)"

    if mode == "analysis_window":
        _, blob = format_entries_with_ids(entries_df)
        return blob

    if mode == "k10_last_30":
        df_k = slice_last_n_calendar_days(entries_df, 30)
        if df_k.empty:
            return "(No entries in the last 30 calendar days of the analysis window.)"
        _, blob = format_entries_with_ids(df_k)
        return blob

    if mode == "both_labeled_sections":
        _, full_blob = format_entries_with_ids(entries_df)
        df_k = slice_last_n_calendar_days(entries_df, 30)
        if df_k.empty:
            k10_section = "(No entries in the last 30 calendar days.)"
        else:
            _, k10_section = format_entries_with_ids(df_k)
        return (
            "=== Analysis window (full range) ===\n\n"
            + full_blob
            + "\n\n=== Last 30 calendar days (K10 window) ===\n\n"
            + k10_section
        )

    raise ValueError(f"Unknown qc_journal_context.mode: {mode!r}")
