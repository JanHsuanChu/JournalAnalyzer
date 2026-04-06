# context_builder.py
# Build text bundles from journal DataFrames for Agent 1 (K10) and Agent 2 (insights).

from __future__ import annotations

import json
import os
from datetime import timedelta

import pandas as pd

# Character budget for Agent 2 context (soft limit)
_AGENT2_CHAR_BUDGET = 120_000


def ensure_date_column(df: pd.DataFrame) -> pd.DataFrame:
    """Return a copy with parsed `date` column."""
    out = df.copy()
    if "date" not in out.columns:
        raise ValueError("DataFrame must include a 'date' column")
    if not pd.api.types.is_datetime64_any_dtype(out["date"]):
        out["date"] = pd.to_datetime(out["date"], errors="coerce")
    return out


def slice_last_n_calendar_days(df: pd.DataFrame, n_days: int = 30) -> pd.DataFrame:
    """Keep rows in the last `n_days` calendar days relative to max date in frame."""
    df = ensure_date_column(df)
    if df.empty:
        return df
    end = df["date"].max()
    if pd.isna(end):
        return df.iloc[0:0].copy()
    start = end - timedelta(days=n_days - 1)
    start = pd.Timestamp(start).normalize()
    # .dt.normalize() — datetime methods live on .dt for Series (not .normalize() on Series).
    return df.loc[df["date"].dt.normalize() >= start].sort_values("date")


def format_entries_with_ids(df: pd.DataFrame) -> tuple[list[dict], str]:
    """
    Build line-oriented records and a single prompt block.
    Each row: entry_id, iso_date, text preview.
    """
    df = ensure_date_column(df)
    rows: list[dict] = []
    lines: list[str] = []
    for i, r in df.iterrows():
        eid = r.get("id", i)
        try:
            eid = int(eid)
        except (TypeError, ValueError):
            eid = str(eid)
        d = r["date"]
        ds = d.strftime("%Y-%m-%d") if hasattr(d, "strftime") else str(d)[:10]
        text = str(r.get("text", "") or "")
        rows.append({"entry_id": eid, "date": ds, "text": text})
        snippet = text[:2000].replace("\n", " ")
        lines.append(f"[entry_id={eid} date={ds}] {snippet}")
    blob = "\n\n".join(lines)
    return rows, blob


def build_k10_corpus_note(df_k10: pd.DataFrame) -> str:
    """Short header describing the K10 slice."""
    df_k10 = ensure_date_column(df_k10)
    if df_k10.empty:
        return "No journal rows in the K10 window."
    d0 = df_k10["date"].min()
    d1 = df_k10["date"].max()
    return (
        f"K10 window: {len(df_k10)} entries from {d0.strftime('%Y-%m-%d')} to {d1.strftime('%Y-%m-%d')}."
    )


def build_agent2_context_bundle(df: pd.DataFrame, char_budget: int = _AGENT2_CHAR_BUDGET) -> str:
    """Ordered journal text for insights; truncated to char_budget."""
    df = ensure_date_column(df)
    parts: list[str] = []
    total = 0
    for _, r in df.sort_values("date").iterrows():
        d = r["date"]
        ds = d.strftime("%Y-%m-%d") if hasattr(d, "strftime") else str(d)[:10]
        text = str(r.get("text", "") or "")
        line = f"--- {ds} ---\n{text}\n"
        if total + len(line) > char_budget:
            remain = char_budget - total
            if remain > 100:
                parts.append(line[:remain])
            break
        parts.append(line)
        total += len(line)
    return "\n".join(parts)


K10_PER_ITEM_RAG_MARKER = "K10 retrieval (per-item sections):"
# Opening line of build_k10_structured_full_diary_prompt (RAG disabled / empty retrieval fallback).
K10_STRUCTURED_FULL_MARKER = "STRUCTURED K10 WINDOW (full diary, no RAG):"


def format_k10_per_item_rag_prompt(
    rows_per_item: list[list[dict]],
    questions: list[str],
    total_char_budget: int = 12_000,
) -> str:
    """
    Build Agent 1 diary blob: one ## Item k section per K10 question with chunks from that query only.
    """
    n = len(questions)
    if n == 0:
        return ""
    if len(rows_per_item) != n:
        rows_per_item = list(rows_per_item)[:n]
        while len(rows_per_item) < n:
            rows_per_item.append([])
    env_pi = os.environ.get("RAG_K10_PER_ITEM_CHAR_BUDGET", "").strip()
    if env_pi.isdigit():
        per_item_budget = max(400, int(env_pi))
    else:
        per_item_budget = max(400, total_char_budget // max(1, n))
    parts: list[str] = [
        f"{K10_PER_ITEM_RAG_MARKER} Each section holds passages retrieved only for that item's query. "
        "Score, evidence, and frequency (day/entry bands) apply per item using that section only—do not use Item j "
        "text to justify or frequency-judge Item k."
    ]
    for i, (q, rows) in enumerate(zip(questions, rows_per_item), start=1):
        header = f"## Item {i} — {q}"
        if not rows:
            parts.append(
                f"{header}\n"
                "(No passages retrieved for this query — no textual support; prefer score 1 and empty evidence.)\n"
            )
            continue
        sub = format_rag_chunks_for_prompt(
            rows,
            f"Retrieved for item {i} only.",
            char_budget=per_item_budget,
        )
        parts.append(f"{header}\n{sub}")
    return "\n\n".join(parts).strip()


def build_k10_structured_full_diary_prompt(df_k10: pd.DataFrame, char_budget: int = 12_000) -> str:
    """
    When RAG is off: one full journal plus numbered K10 stems so the model maps each score to the right theme.
    """
    from k10_utils import K10_RAG_QUERIES

    _, blob = format_entries_with_ids(df_k10)
    if len(blob) > char_budget:
        blob = blob[: char_budget - 20] + "\n…[truncated]"
    stems = "\n".join(f"{i + 1}. {q}" for i, q in enumerate(K10_RAG_QUERIES))
    return (
        f"{K10_STRUCTURED_FULL_MARKER} Use the journal below. For each item k, consider only "
        "content relevant to that item's question stem. Combine **frequency** (how often the theme appears) "
        "and **severity** (intensity of distress in language) to choose Likert 1–5.\n\n"
        "Question stems (tool item order 1–10):\n"
        f"{stems}\n\n"
        "---FULL JOURNAL---\n"
        f"{blob}"
    )


def format_rag_chunks_for_prompt(
    rows: list[dict],
    header: str,
    char_budget: int = _AGENT2_CHAR_BUDGET,
) -> str:
    """
    Turn retrieved chunk dicts into a single prompt block (date + ids + text).
    Rows should include chunk_id, journal_entry_id, entry_date, chunk_text, similarity.
    """
    if not rows:
        return ""
    lines: list[str] = [header.strip(), ""]
    total = len(header)
    for r in rows:
        cid = r.get("chunk_id", "")
        jid = r.get("journal_entry_id", "")
        ed = r.get("entry_date", "")
        if hasattr(ed, "strftime"):
            ed = ed.strftime("%Y-%m-%d")
        else:
            ed = str(ed)[:10]
        sim = r.get("similarity")
        sim_s = f"{float(sim):.3f}" if sim is not None else ""
        body = str(r.get("chunk_text") or "").replace("\n", " ")
        line = f"[chunk_id={cid} entry_id={jid} date={ed} sim={sim_s}] {body}"
        if total + len(line) + 2 > char_budget:
            remain = char_budget - total - 2
            if remain > 80:
                lines.append(line[:remain] + "…")
            break
        lines.append(line)
        total += len(line) + 1
    return "\n".join(lines).strip()


def split_window_halves(df: pd.DataFrame) -> tuple[str, str]:
    """First half vs second half of sorted window (for emerging/fading hints in prompts)."""
    df = ensure_date_column(df).sort_values("date")
    if len(df) < 2:
        return "", ""
    mid = len(df) // 2
    first = build_agent2_context_bundle(df.iloc[:mid], char_budget=60_000)
    second = build_agent2_context_bundle(df.iloc[mid:], char_budget=60_000)
    return first, second
