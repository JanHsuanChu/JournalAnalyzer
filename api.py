# api.py
# Journal Analyzer API — serves journal entries from Supabase when configured, else CSV.
# Pairs with Shiny app (app.py) which consumes GET /entries.

# 0. Setup #################################

import os
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

# Load .env from app directory so uvicorn cwd does not matter
_APP_DIR = Path(__file__).resolve().parent
load_dotenv(_APP_DIR / ".env")

# 1. Data loading (CSV fallback) #################################

_CSV_PATH = _APP_DIR / "journal_entries.csv"
_REPORTS_DIR = _APP_DIR / "reports"

_supabase_client = None  # lazy: None = not resolved, False = not configured


def _get_supabase():
    """Return Supabase client or None if URL/key missing."""
    global _supabase_client
    if _supabase_client is not None:
        return _supabase_client if _supabase_client is not False else None
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_KEY") or os.environ.get("SUPABSE_SERVICE_ROLE_KEY")
    if url and key:
        from supabase import create_client

        _supabase_client = create_client(url, key)
    else:
        _supabase_client = False
    return _supabase_client if _supabase_client is not False else None


def _load_entries_from_csv():
    if not _CSV_PATH.exists():
        return pd.DataFrame()
    return pd.read_csv(_CSV_PATH, parse_dates=["date"])


def _entries_from_supabase():
    """Fetch rows from journal_entry; map entry_date -> date for the Shiny app."""
    client = _get_supabase()
    if not client:
        return None
    res = (
        client.table("journal_entry")
        .select("entry_date, day_of_week, time_of_day, text")
        .order("entry_date")
        .execute()
    )
    out = []
    for row in res.data:
        d = row.get("entry_date")
        if d is None:
            continue
        if isinstance(d, str) and "T" in d:
            d = d.split("T")[0]
        elif hasattr(d, "strftime"):
            d = d.strftime("%Y-%m-%d")
        out.append(
            {
                "date": d,
                "day_of_week": row["day_of_week"],
                "time_of_day": row["time_of_day"],
                "text": row["text"],
            }
        )
    return out


# 2. FastAPI app #################################

app = FastAPI(
    title="Journal Analyzer API",
    description="Serves journal entries from Supabase (journal_entry) when SUPABASE_URL and SUPABASE_KEY are set; otherwise from journal_entries.csv.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health():
    """Readiness check; entries_source indicates intended data source for /entries."""
    src = "supabase" if _get_supabase() else "csv"
    return {"status": "ok", "entries_source": src}


@app.get("/entries")
def get_entries():
    """
    Return all journal entries as JSON.
    No server-side filtering; the Shiny app filters client-side.
    """
    try:
        from_sb = _entries_from_supabase()
        if from_sb is not None:
            return from_sb
    except Exception:
        pass

    df = _load_entries_from_csv()
    if df is None or df.empty:
        return []
    df = df.copy()
    df["date"] = df["date"].dt.strftime("%Y-%m-%d")
    return df.to_dict(orient="records")


@app.get("/reports/{filename:path}")
def get_report(filename: str):
    """
    Serve a generated report HTML file from the reports/ directory.
    Only files under _REPORTS_DIR are allowed (no path traversal).
    """
    path = (_REPORTS_DIR / filename).resolve()
    try:
        path.relative_to(_REPORTS_DIR)
    except ValueError:
        raise HTTPException(status_code=404, detail="Report not found")
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Report not found")
    return FileResponse(path, media_type="text/html")
