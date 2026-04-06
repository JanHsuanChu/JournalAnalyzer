# load_journal_to_supabase.py
# Load journal_entries.csv into Supabase table journal_entry.
# Pairs with SUPABASE_JOURNAL.md and supabase_migration_journal_entry.sql

# 0. Setup #################################

import argparse
import os
from pathlib import Path

import pandas as pd
from supabase import create_client

# Load .env from this directory (same pattern as Traffic-Predictor/load_data_to_supabase.py)
_script_dir = Path(__file__).resolve().parent
_env_path = _script_dir / ".env"
if _env_path.exists():
    with open(_env_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, _, value = line.partition("=")
                key, value = key.strip(), value.strip().strip('"').strip("'")
                os.environ.setdefault(key, value)

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY") or os.environ.get("SUPABSE_SERVICE_ROLE_KEY")

BATCH_SIZE = 200
_CSV_NAME = "journal_entries.csv"


def main() -> None:
    parser = argparse.ArgumentParser(description="Load journal_entries.csv into Supabase journal_entry")
    parser.add_argument(
        "--no-truncate",
        action="store_true",
        help="Do not delete existing rows before insert (may duplicate on re-run)",
    )
    args = parser.parse_args()

    if not SUPABASE_URL or not SUPABASE_KEY:
        raise SystemExit(
            "Set SUPABASE_URL and SUPABASE_KEY in .env (or environment). See SUPABASE_JOURNAL.md."
        )

    csv_path = _script_dir / _CSV_NAME
    if not csv_path.is_file():
        raise SystemExit(f"Missing {csv_path}")

    df = pd.read_csv(csv_path)
    # DB column entry_date maps from CSV date (reserved word avoided in Postgres)
    df = df.rename(columns={"date": "entry_date"})
    if "entry_date" not in df.columns:
        raise SystemExit("CSV must have a date column")

    client = create_client(SUPABASE_URL, SUPABASE_KEY)

    # Remove existing rows so re-runs are idempotent (does not touch other tables)
    if not args.no_truncate:
        # Match all rows with id != 0 (ids are positive identity values)
        client.table("journal_entry").delete().neq("id", 0).execute()

    records = df.to_dict(orient="records")
    for i in range(0, len(records), BATCH_SIZE):
        batch = records[i : i + BATCH_SIZE]
        client.table("journal_entry").insert(batch).execute()
        print(f"Inserted rows {i + 1}–{min(i + BATCH_SIZE, len(records))} / {len(records)}")

    print(f"Done: {len(records)} journal_entry rows.")


if __name__ == "__main__":
    main()
