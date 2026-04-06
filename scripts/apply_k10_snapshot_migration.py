#!/usr/bin/env python3
"""
Apply JournalAnalyzer/supabase_migration_k10_snapshot.sql to Postgres.

Requires DATABASE_URL or SUPABASE_DB_URL in the environment (e.g. JournalAnalyzer/.env).
Get the URI from Supabase: Project Settings → Database → Connection string.

If no DB URL is set, prints instructions to run the SQL file in the Supabase SQL Editor.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SQL = _ROOT / "supabase_migration_k10_snapshot.sql"


def main() -> int:
    env_path = _ROOT / ".env"
    try:
        from dotenv import load_dotenv

        load_dotenv(env_path, override=False)
    except ImportError:
        pass

    url = (os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL") or "").strip()
    if not url:
        env_hint = ""
        if env_path.is_file():
            env_hint = (
                f"\nNote: Found {env_path} — it does not contain DATABASE_URL yet.\n"
                "SUPABASE_URL (https://…supabase.co) is not a Postgres connection string; "
                "for psql you need the Database URI (postgres://postgres:PASSWORD@…).\n"
            )
        else:
            env_hint = f"\nNote: No file at {env_path} (optional; use SQL Editor without it).\n"

        print(
            "No DATABASE_URL or SUPABASE_DB_URL set (after loading JournalAnalyzer/.env if present)."
            + env_hint
            + "\nOption A — Supabase Dashboard (works with only Project URL + API key):\n"
            f"  1. SQL → New query\n"
            f"  2. Paste the full contents of:\n     {_SQL}\n"
            "  3. Run\n\n"
            "Option B — Command line (add to .env, then re-run this script):\n"
            "  Supabase → Project Settings → Database → Connection string → URI.\n"
            "  Add: DATABASE_URL=postgresql://postgres.[ref]:[PASSWORD]@…\n"
            f"  Requires: psql installed locally.\n",
            file=sys.stderr,
        )
        return 2

    if not _SQL.is_file():
        print(f"Missing migration file: {_SQL}", file=sys.stderr)
        return 1

    r = subprocess.run(
        ["psql", url, "-v", "ON_ERROR_STOP=1", "-f", str(_SQL)],
        capture_output=True,
        text=True,
    )
    if r.stdout:
        print(r.stdout)
    if r.stderr:
        print(r.stderr, file=sys.stderr)
    if r.returncode != 0:
        return r.returncode
    print("Applied k10_snapshot migration successfully.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
