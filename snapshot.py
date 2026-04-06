# snapshot.py
# Persist K10 snapshots to Supabase (optional; failures are non-fatal).

from __future__ import annotations

import logging
import os
from datetime import date
from typing import Any

import pandas as pd

logger = logging.getLogger(__name__)


def _client():
    url = os.environ.get("SUPABASE_URL", "").strip()
    key = (
        os.environ.get("SUPABASE_KEY", "").strip()
        or os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    )
    if not url or not key:
        return None
    from supabase import create_client

    return create_client(url, key)


def save_k10_snapshot(
    k10_payload: dict[str, Any],
    df_k10: pd.DataFrame,
    model: str | None,
) -> None:
    """Insert one row into k10_snapshot; ignore errors."""
    c = _client()
    if c is None:
        return
    try:
        if df_k10 is None or df_k10.empty:
            w_end = None
            w_start = None
        else:
            d = df_k10["date"]
            w_end = d.max()
            w_start = d.min()
            if hasattr(w_end, "date"):
                w_end = w_end.date()
            if hasattr(w_start, "date"):
                w_start = w_start.date()
        row = {
            "total_score": int(k10_payload.get("total_score", 0)),
            "item_scores": k10_payload.get("item_scores", []),
            "severity_band": str(k10_payload.get("severity_band", "")),
            "window_end_date": w_end.isoformat() if w_end else None,
            "window_start_date": w_start.isoformat() if w_start else None,
            "entry_count": len(df_k10) if df_k10 is not None else 0,
            "model": (model or "")[:200],
        }
        c.table("k10_snapshot").insert(row).execute()
    except Exception as e:
        logger.warning(
            "k10_snapshot insert failed (table missing or permissions? apply supabase_migration_k10_snapshot.sql): %s",
            e,
        )


def fetch_k10_snapshots(limit: int = 50) -> list[dict]:
    """Recent snapshots for trend chart (newest last)."""
    c = _client()
    if c is None:
        return []
    try:
        res = (
            c.table("k10_snapshot")
            .select("created_at, total_score, window_end_date, item_scores, severity_band")
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
        rows = list(res.data or [])
        return list(reversed(rows))
    except Exception as e:
        logger.warning(
            "k10_snapshot fetch failed (table missing or permissions?): %s",
            e,
        )
        return []
