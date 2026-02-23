# api.py
# Journal Analyzer API — serves journal entries from CSV.
# Pairs with Shiny app (app.py) which consumes GET /entries.

# 0. Setup #################################

from pathlib import Path

import pandas as pd
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

# 1. Data loading #################################

# Resolve paths relative to this file so uvicorn can run from any cwd
_APP_DIR = Path(__file__).resolve().parent
_CSV_PATH = _APP_DIR / "journal_entries.csv"
_REPORTS_DIR = _APP_DIR / "reports"

# Load at startup; parse date as datetime for consistent JSON serialization
def _load_entries():
    if not _CSV_PATH.exists():
        return pd.DataFrame()
    df = pd.read_csv(_CSV_PATH, parse_dates=["date"])
    return df

_entries_df = _load_entries()

# 2. FastAPI app #################################

app = FastAPI(
    title="Journal Analyzer API",
    description="Serves journal entries from journal_entries.csv for the Shiny app.",
)

# Allow Shiny app on another port to call this API
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health():
    """Readiness check."""
    return {"status": "ok"}


@app.get("/entries")
def get_entries():
    """
    Return all journal entries as JSON.
    No server-side filtering; the Shiny app filters client-side.
    """
    if _entries_df is None or _entries_df.empty:
        return []
    # Serialize: date to ISO string so JSON is consistent
    df = _entries_df.copy()
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
