# embedding_pipeline.py
# Incremental journal chunking + embeddings (sentence-transformers by default, optional OpenAI) + Supabase upsert.
# Aligned with dsai/07_rag/05_embed.py (MiniLM) while keeping Supabase pgvector.
# Tim Fraser

from __future__ import annotations

import hashlib
import os
from typing import Any

import pandas as pd

# Chunk diary text: ~550–800 tokens target using character budget (rough).
_CHUNK_CHARS = 2200
_CHUNK_OVERLAP = 200

# Lazy singleton for sentence-transformers (matches 05_embed.py pattern).
_st_model = None
_st_model_loaded_name: str | None = None


def _hash_text(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def chunk_entry_text(text: str, max_chars: int = _CHUNK_CHARS, overlap: int = _CHUNK_OVERLAP) -> list[tuple[int, int, str]]:
    """
    Deterministic split of entry text into overlapping segments.
    Returns list of (char_start, char_end_exclusive, chunk_text).
    """
    t = (text or "").strip()
    if not t:
        return []
    out: list[tuple[int, int, str]] = []
    start = 0
    n = len(t)
    while start < n:
        end = min(start + max_chars, n)
        chunk = t[start:end]
        out.append((start, end, chunk))
        if end >= n:
            break
        start = max(0, end - overlap)
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


def _embedding_backend() -> str:
    """sentence_transformers (default, 05_embed-style) or openai."""
    return os.environ.get("EMBEDDING_BACKEND", "sentence_transformers").strip().lower()


def _embedding_config() -> tuple[str, int]:
    """Model id and expected vector dimension (must match pgvector column size)."""
    b = _embedding_backend()
    if b in ("openai", "open_ai"):
        model = os.environ.get("EMBEDDING_MODEL", "text-embedding-3-small").strip()
        dim = int(os.environ.get("EMBEDDING_DIMENSION", "1536"))
        return model, dim
    model = os.environ.get("EMBEDDING_MODEL", "all-MiniLM-L6-v2").strip()
    dim = int(os.environ.get("EMBEDDING_DIMENSION", "384"))
    return model, dim


def _get_sentence_transformer():
    """Load SentenceTransformer once per process (same idea as 05_embed.py get_embed_model)."""
    global _st_model, _st_model_loaded_name
    model_name, _ = _embedding_config()
    if _st_model is None or _st_model_loaded_name != model_name:
        from sentence_transformers import SentenceTransformer

        _st_model = SentenceTransformer(model_name)
        _st_model_loaded_name = model_name
    return _st_model


def embed_texts_sentence_transformers(texts: list[str]) -> list[list[float]]:
    """Batch encode with sentence-transformers (local; no OPENAI_API_KEY)."""
    if not texts:
        return []
    m = _get_sentence_transformer()
    batch_size = max(1, int(os.environ.get("EMBED_BATCH_SIZE", "32")))
    all_vecs: list[list[float]] = []
    for i in range(0, len(texts), batch_size):
        batch = texts[i : i + batch_size]
        arr = m.encode(batch, convert_to_numpy=True, show_progress_bar=False, normalize_embeddings=True)
        for row in arr:
            all_vecs.append(row.tolist())
    return all_vecs


def embed_texts_openai(texts: list[str]) -> list[list[float]]:
    """Batch embedding via OpenAI; raises if OPENAI_API_KEY missing."""
    if not texts:
        return []
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is required when EMBEDDING_BACKEND=openai.")
    model, _dim = _embedding_config()
    from openai import OpenAI

    client = OpenAI(api_key=api_key)
    batch_size = int(os.environ.get("EMBED_BATCH_SIZE", "32"))
    all_vecs: list[list[float]] = []
    for i in range(0, len(texts), batch_size):
        batch = texts[i : i + batch_size]
        resp = client.embeddings.create(model=model, input=batch)
        for d in sorted(resp.data, key=lambda x: x.index):
            all_vecs.append(list(d.embedding))
    return all_vecs


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Route to OpenAI or sentence-transformers per EMBEDDING_BACKEND."""
    b = _embedding_backend()
    if b in ("openai", "open_ai"):
        return embed_texts_openai(texts)
    return embed_texts_sentence_transformers(texts)


def rag_available() -> bool:
    """True when Supabase is configured and embeddings can run (ST installed or OpenAI key for openai backend)."""
    if _get_supabase_client() is None:
        return False
    b = _embedding_backend()
    if b in ("openai", "open_ai"):
        return bool(os.environ.get("OPENAI_API_KEY", "").strip())
    try:
        import sentence_transformers  # noqa: F401
    except ImportError:
        return False
    return True


def _entry_date_py(d) -> str:
    if hasattr(d, "strftime"):
        return d.strftime("%Y-%m-%d")
    return str(d)[:10]


def _fetch_chunks_for_entries(client, entry_ids: list[int]) -> dict[int, list[dict[str, Any]]]:
    """Map journal_entry_id -> list of chunk rows (id, chunk_index, content_hash)."""
    if not entry_ids:
        return {}
    res = (
        client.table("journal_chunk")
        .select("id,journal_entry_id,chunk_index,content_hash,text")
        .in_("journal_entry_id", entry_ids)
        .execute()
    )
    rows = res.data or []
    by_eid: dict[int, list[dict[str, Any]]] = {}
    for r in rows:
        eid = int(r["journal_entry_id"])
        by_eid.setdefault(eid, []).append(r)
    for eid in by_eid:
        by_eid[eid].sort(key=lambda x: int(x["chunk_index"]))
    return by_eid


def _chunks_match_existing(
    new_segments: list[tuple[int, int, str]], existing: list[dict[str, Any]]
) -> bool:
    if len(new_segments) != len(existing):
        return False
    for i, seg in enumerate(new_segments):
        h = _hash_text(seg[2])
        if existing[i].get("content_hash") != h:
            return False
        if int(existing[i].get("chunk_index", -1)) != i:
            return False
    return True


def sync_chunks_for_dataframe(df: pd.DataFrame) -> int:
    """
    Upsert journal_chunk rows for all rows in df (expects id, date, text).
    Deletes and replaces chunks when entry text changes.
    Returns count of journal_chunk rows written or touched (approximate: new inserts + updates).
    """
    client = _get_supabase_client()
    if client is None:
        raise RuntimeError("Supabase client not configured.")
    if df.empty or "id" not in df.columns:
        return 0
    df = df.copy()
    if not pd.api.types.is_datetime64_any_dtype(df["date"]):
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
    entry_ids = [int(x) for x in df["id"].dropna().unique().tolist()]
    existing_by_entry = _fetch_chunks_for_entries(client, entry_ids)
    touched = 0
    for _, row in df.iterrows():
        try:
            eid = int(row["id"])
        except (TypeError, ValueError):
            continue
        text = str(row.get("text", "") or "")
        entry_date = _entry_date_py(row["date"])
        new_segments = chunk_entry_text(text)
        prev = existing_by_entry.get(eid, [])
        if new_segments and _chunks_match_existing(new_segments, prev):
            continue
        # Replace chunks for this entry (cascade removes stale embeddings).
        client.table("journal_chunk").delete().eq("journal_entry_id", eid).execute()
        if not new_segments:
            continue
        insert_rows = []
        for idx, (cs, ce, ctext) in enumerate(new_segments):
            insert_rows.append(
                {
                    "journal_entry_id": eid,
                    "chunk_index": idx,
                    "entry_date": entry_date,
                    "text": ctext,
                    "char_start": cs,
                    "char_end": ce,
                    "content_hash": _hash_text(ctext),
                }
            )
        client.table("journal_chunk").insert(insert_rows).execute()
        touched += len(insert_rows)
    return touched


def list_chunks_missing_embeddings(
    client,
    entry_ids: list[int],
    embedding_model: str,
) -> list[dict[str, Any]]:
    """Chunk rows under entry_ids with no embedding row for embedding_model."""
    if not entry_ids:
        return []
    chunks: list[dict[str, Any]] = []
    step = 80
    for i in range(0, len(entry_ids), step):
        batch = entry_ids[i : i + step]
        res = (
            client.table("journal_chunk")
            .select("id,journal_entry_id,entry_date,text,content_hash")
            .in_("journal_entry_id", batch)
            .execute()
        )
        chunks.extend(res.data or [])
    if not chunks:
        return []
    chunk_ids = [str(c["id"]) for c in chunks]
    have: set[str] = set()
    step = 120
    for i in range(0, len(chunk_ids), step):
        batch = chunk_ids[i : i + step]
        emb_res = (
            client.table("journal_chunk_embedding")
            .select("chunk_id,embedding_model")
            .in_("chunk_id", batch)
            .eq("embedding_model", embedding_model)
            .execute()
        )
        have |= {str(r["chunk_id"]) for r in (emb_res.data or [])}
    return [c for c in chunks if str(c["id"]) not in have]


def upsert_embeddings(
    client,
    chunk_ids: list[str],
    vectors: list[list[float]],
    embedding_model: str,
) -> None:
    rows = [
        {"chunk_id": cid, "embedding": vec, "embedding_model": embedding_model}
        for cid, vec in zip(chunk_ids, vectors)
    ]
    if not rows:
        return
    client.table("journal_chunk_embedding").upsert(rows, on_conflict="chunk_id").execute()


def ensure_embeddings_for_entries(df: pd.DataFrame) -> tuple[int, int]:
    """
    Sync chunks for df, then embed any chunks missing vectors for EMBEDDING_MODEL.
    Returns (chunks_synced_touch_count, embeddings_upserted_count).
    """
    model, _dim = _embedding_config()
    client = _get_supabase_client()
    if client is None:
        raise RuntimeError("Supabase client not configured.")
    synced = sync_chunks_for_dataframe(df)
    entry_ids = [int(x) for x in df["id"].dropna().unique().tolist()] if not df.empty else []
    missing = list_chunks_missing_embeddings(client, entry_ids, model)
    if not missing:
        return synced, 0
    texts = [str(m["text"]) for m in missing]
    vectors = embed_texts(texts)
    ids = [str(m["id"]) for m in missing]
    upsert_embeddings(client, ids, vectors, model)
    return synced, len(missing)
