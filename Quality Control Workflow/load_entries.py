# load_entries.py
# Fetch journal entries the same way as the Shiny app (API → DataFrame).

from __future__ import annotations

import os

import pandas as pd
from dotenv import load_dotenv

from paths import JA_ROOT, ensure_ja_on_path

ensure_ja_on_path()

from utils import fetch_entries, filter_entries_by_date_only  # noqa: E402


def load_dotenv_journal_analyzer() -> None:
    """Load `.env` from JournalAnalyzer root if present."""
    env_path = JA_ROOT / ".env"
    if env_path.is_file():
        load_dotenv(env_path)


def load_entries_for_qc() -> pd.DataFrame | None:
    """
    Return all entries from the Journal API (same as app), or None on failure.
    Uses JOURNAL_API_URL from the environment (default http://127.0.0.1:8000).
    """
    load_dotenv_journal_analyzer()
    base = os.environ.get("JOURNAL_API_URL", "http://127.0.0.1:8000")
    df = fetch_entries(base)
    return df
