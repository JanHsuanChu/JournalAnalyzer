# retrieval.py
# pgvector RAG: embed queries, call match_journal_chunks, merge/dedupe results.
# Tim Fraser

from __future__ import annotations

import os
from datetime import date
from typing import Any

import pandas as pd

from embedding_pipeline import _embedding_config, embed_texts


def build_agent2_rag_queries(user_question: str | None, trend_keywords: list[str]) -> list[str]:
    """Queries for Agent 2 retrieval: user question plus each non-empty trend phrase."""
    out: list[str] = []
    uq = (user_question or "").strip()
    if uq:
        out.append(uq)
    for k in trend_keywords or []:
        t = (k or "").strip()
        if t and t not in out:
            out.append(t)
    return out


def _get_supabase_client():
    url = os.environ.get("SUPABASE_URL", "").strip()
    key = (
        os.environ.get("SUPABASE_KEY", "").strip()
        or os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    )
    if not url or not key:
        return None
    from supabase import create_client

    return create_client(url, key)


def _rpc_match(
    client,
    query_embedding: list[float],
    embedding_model: str,
    date_from: date,
    date_to: date,
    match_count: int,
    min_similarity: float,
) -> list[dict[str, Any]]:
    res = client.rpc(
        "match_journal_chunks",
        {
            "query_embedding": query_embedding,
            "embedding_model_filter": embedding_model,
            "filter_date_from": date_from.isoformat(),
            "filter_date_to": date_to.isoformat(),
            "match_count": match_count,
            "min_similarity": min_similarity,
        },
    ).execute()
    return list(res.data or [])


def retrieve_merged(
    queries: list[str],
    date_from: date,
    date_to: date,
    top_k_per_query: int = 12,
    min_similarity: float | None = None,
) -> list[dict[str, Any]]:
    """
    Run vector search per query; merge rows by chunk_id keeping max similarity.
    Each row: chunk_id, journal_entry_id, entry_date, chunk_text, similarity
    """
    if not queries or date_from > date_to:
        return []
    client = _get_supabase_client()
    if client is None:
        return []
    model, _dim = _embedding_config()
    min_sim = (
        float(min_similarity)
        if min_similarity is not None
        else float(os.environ.get("RAG_MIN_SIMILARITY", "0.2"))
    )
    # One API call for all query strings.
    q_vecs = embed_texts(queries)
    best: dict[str, dict[str, Any]] = {}
    for qtext, qvec in zip(queries, q_vecs):
        _ = qtext  # reserved for logging
        rows = _rpc_match(client, qvec, model, date_from, date_to, top_k_per_query, min_sim)
        for r in rows:
            cid = str(r.get("chunk_id", ""))
            if not cid:
                continue
            sim = float(r.get("similarity") or 0.0)
            prev = best.get(cid)
            if prev is None or sim > float(prev.get("similarity") or 0.0):
                best[cid] = {
                    "chunk_id": cid,
                    "journal_entry_id": r.get("journal_entry_id"),
                    "entry_date": r.get("entry_date"),
                    "chunk_text": r.get("chunk_text") or "",
                    "similarity": sim,
                }
    merged = list(best.values())
    merged.sort(key=lambda x: float(x.get("similarity") or 0.0), reverse=True)
    return merged


def retrieve_k10_per_item_rows(
    queries: list[str],
    date_from: date,
    date_to: date,
    top_k_per_query: int = 10,
    *,
    parallel: bool | None = None,
) -> list[list[dict[str, Any]]]:
    """
    One vector search per K10 question (no cross-query merge). Preserves which passages belong to which item.
    """
    if not queries or date_from > date_to:
        return [[] for _ in queries]
    if parallel is None:
        parallel = os.environ.get("RAG_K10_PARALLEL", "1").strip().lower() not in ("0", "false", "no")
    if parallel:
        from concurrent.futures import ThreadPoolExecutor

        max_w = min(len(queries), max(1, int(os.environ.get("RAG_K10_PARALLEL_WORKERS", "5"))))
        with ThreadPoolExecutor(max_workers=max_w) as pool:
            futs = [
                pool.submit(retrieve_merged, [q], date_from, date_to, top_k_per_query) for q in queries
            ]
            return [f.result() for f in futs]
    return [retrieve_merged([q], date_from, date_to, top_k_per_query) for q in queries]


def date_range_from_dataframe(df: pd.DataFrame) -> tuple[date | None, date | None]:
    """Min/max dates from a journal DataFrame (column `date`)."""
    if df.empty or "date" not in df.columns:
        return None, None
    s = pd.to_datetime(df["date"], errors="coerce").dropna()
    if s.empty:
        return None, None
    return s.min().date(), s.max().date()
