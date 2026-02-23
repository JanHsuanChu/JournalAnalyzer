# utils.py
# Helper functions for the Journal Analyzer Shiny app.
# API client and client-side filtering; used by app.py.

import os
import re

import pandas as pd
import requests

# Default base URL for the Journal Analyzer API (override with JOURNAL_API_URL env var)
DEFAULT_API_BASE = "http://127.0.0.1:8000"


def get_api_base():
    """API base URL: from env JOURNAL_API_URL or default."""
    return os.environ.get("JOURNAL_API_URL", DEFAULT_API_BASE)


def fetch_entries(base_url: str | None = None) -> pd.DataFrame | None:
    """
    Fetch all journal entries from the API.
    Returns a DataFrame with columns date, day_of_week, time_of_day, text;
    returns None on connection error or non-200 response.
    """
    url = (base_url or get_api_base()).rstrip("/") + "/entries"
    try:
        r = requests.get(url, timeout=10)
    except requests.RequestException:
        return None
    if r.status_code != 200:
        return None
    try:
        data = r.json()
    except Exception:
        return None
    if not isinstance(data, list):
        return None
    if len(data) == 0:
        return pd.DataFrame(columns=["date", "day_of_week", "time_of_day", "text"])
    df = pd.DataFrame(data)
    # Ensure date is parsed for filtering
    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"])
    return df


def filter_entries(
    df: pd.DataFrame | None,
    date_from,
    date_to,
    days: list,
    times: list,
    keywords: str,
) -> pd.DataFrame:
    """
    Filter journal entries by date range, day of week, time of day, and keywords.
    Empty filters mean no constraint. Keywords: comma-separated, wildcard * as .*,
    case-insensitive; entry matches if any keyword matches (OR).
    Returns empty DataFrame if df is None or empty.
    """
    if df is None or df.empty:
        return pd.DataFrame()
    out = df.copy()
    # Date range (inclusive)
    if date_from is not None:
        out = out.loc[out["date"].dt.date >= date_from]
    if date_to is not None:
        out = out.loc[out["date"].dt.date <= date_to]
    # Day of week
    if days:
        out = out.loc[out["day_of_week"].isin(days)]
    # Time of day
    if times:
        out = out.loc[out["time_of_day"].isin(times)]
    # Keywords: split by comma, strip, build regex OR; * in keyword becomes .*
    if keywords and isinstance(keywords, str):
        parts = [p.strip() for p in keywords.split(",") if p.strip()]
        if parts:
            pattern_parts = []
            for p in parts:
                # Replace * with .* for wildcard, then escape the rest; use (?: ) to avoid capture-group warning
                wild = re.escape(p).replace("\\*", ".*")
                pattern_parts.append(f"(?:{wild})")
            pattern = "|".join(pattern_parts)
            mask = out["text"].str.contains(pattern, case=False, regex=True, na=False)
            out = out.loc[mask]
    return out.reset_index(drop=True)


def filter_entries_by_date_only(
    df: pd.DataFrame | None,
    date_from,
    date_to,
) -> pd.DataFrame:
    """
    Filter journal entries by date range only (no day, time, or keyword).
    Used for the AI report analysis subset. Returns empty DataFrame if df is None or empty.
    """
    return filter_entries(df, date_from, date_to, days=[], times=[], keywords="")


def ollama_chat(prompt: str, api_key: str | None) -> str | None:
    """
    Send a single user message to Ollama Cloud and return the assistant content.
    Uses model gpt-oss:20b-cloud. Returns None on missing key, request error, or invalid response.
    """
    if not api_key:
        return None
    url = "https://ollama.com/api/chat"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    body = {
        "model": "gpt-oss:20b-cloud",
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
    }
    try:
        r = requests.post(url, headers=headers, json=body, timeout=60)
        r.raise_for_status()
        js = r.json()
        return js.get("message", {}).get("content")
    except Exception:
        return None
