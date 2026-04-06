# data_loader.py
# Load journal entries from Supabase with retries (no CSV fallback for app reads).

from __future__ import annotations

import os
import time

import pandas as pd


class DataLoadError(Exception):
    """Raised when journal data cannot be loaded from Supabase."""


def _get_client():
    url = os.environ.get("SUPABASE_URL", "").strip()
    key = (
        os.environ.get("SUPABASE_KEY", "").strip()
        or os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    )
    if not url or not key:
        return None
    from supabase import create_client

    return create_client(url, key)


def load_entries_from_supabase(max_retries: int = 2, backoff_s: float = 0.4) -> pd.DataFrame:
    """
    Fetch all rows from journal_entry. Retries on transient failures.
    Maps entry_date -> date for the Shiny app.
    """
    client = _get_client()
    if client is None:
        raise DataLoadError("Set SUPABASE_URL and SUPABASE_KEY in the environment to load journal entries.")

    last_err: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            res = (
                client.table("journal_entry")
                .select("id, entry_date, day_of_week, time_of_day, text")
                .order("entry_date")
                .execute()
            )
            rows = res.data or []
            if not rows:
                return pd.DataFrame(columns=["id", "date", "day_of_week", "time_of_day", "text"])
            out = []
            for row in rows:
                d = row.get("entry_date")
                if d is None:
                    continue
                if isinstance(d, str) and "T" in d:
                    d = d.split("T")[0]
                elif hasattr(d, "strftime"):
                    d = d.strftime("%Y-%m-%d")
                out.append(
                    {
                        "id": row.get("id"),
                        "date": d,
                        "day_of_week": row["day_of_week"],
                        "time_of_day": row["time_of_day"],
                        "text": row["text"],
                    }
                )
            df = pd.DataFrame(out)
            df["date"] = pd.to_datetime(df["date"])
            return df
        except Exception as e:
            last_err = e
            if attempt < max_retries:
                time.sleep(backoff_s * (attempt + 1))
            continue
    raise DataLoadError(f"Could not load journal entries from Supabase: {last_err}") from last_err
