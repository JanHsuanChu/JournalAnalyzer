# test_multi_agent_workflow.py
# CLI: exercise the multi-agent report pipeline (Agents 1–3) without the Shiny dashboard.
# Loads journal rows from Supabase; selections come from CLI args (defaults are examples only).

from __future__ import annotations

import argparse
import os
import sys
from datetime import date
from pathlib import Path

# Allow imports when run as `python scripts/test_multi_agent_workflow.py` from any cwd.
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from dotenv import load_dotenv

from data_loader import DataLoadError, load_entries_from_supabase
from report_builder import build_report
from utils import filter_entries_by_date_only


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=(
            "Generate the JournalAnalyzer HTML report via build_report (Supabase → Agents 1–3). "
            "Requires SUPABASE_URL, SUPABASE_KEY, and OLLAMA_API_KEY in the environment (e.g. .env)."
        )
    )
    p.add_argument(
        "--year",
        type=int,
        default=2026,
        help="Analysis calendar year (sets --date-from / --date-to unless those are set). Default: %(default)s",
    )
    p.add_argument(
        "--date-from",
        type=str,
        default=None,
        metavar="YYYY-MM-DD",
        help="Start of analysis range (overrides --year start).",
    )
    p.add_argument(
        "--date-to",
        type=str,
        default=None,
        metavar="YYYY-MM-DD",
        help="End of analysis range (overrides --year end).",
    )
    p.add_argument(
        "--question",
        type=str,
        default="how is my energy been going?",
        help="Optional user question for Agent 2.",
    )
    p.add_argument(
        "--trends",
        type=str,
        default="OCD",
        help="Comma-separated trend phrases (triggers monthly trend chart when non-empty). Default: %(default)s",
    )
    p.add_argument(
        "--no-k10",
        action="store_true",
        help="Omit K10 Summary (skip Agent 1).",
    )
    p.add_argument(
        "--k10-trends",
        action="store_true",
        help="Include K10 snapshot history chart (requires DB table k10_snapshot).",
    )
    return p.parse_args()


def _resolve_dates(args: argparse.Namespace) -> tuple[date, date]:
    if args.date_from and args.date_to:
        return date.fromisoformat(args.date_from), date.fromisoformat(args.date_to)
    if args.date_from or args.date_to:
        raise ValueError("Provide both --date-from and --date-to, or neither (use --year).")
    y = args.year
    return date(y, 1, 1), date(y, 12, 31)


def main() -> int:
    load_dotenv(_ROOT / ".env")
    args = _parse_args()
    try:
        date_from, date_to = _resolve_dates(args)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2

    try:
        raw = load_entries_from_supabase()
    except DataLoadError as e:
        print(f"Failed to load journal from Supabase: {e}", file=sys.stderr)
        return 1

    if raw is None or raw.empty:
        print("No journal rows returned from Supabase.", file=sys.stderr)
        return 1

    subset = filter_entries_by_date_only(raw, date_from, date_to)
    if subset.empty:
        print(
            f"No entries between {date_from} and {date_to}. Check your data or date range.",
            file=sys.stderr,
        )
        return 1

    trend_keywords = [k.strip() for k in (args.trends or "").split(",") if k.strip()]
    api_key = os.environ.get("OLLAMA_API_KEY")
    if not api_key:
        print(
            "Warning: OLLAMA_API_KEY is not set; the report will use fallback text without full LLM pipeline.",
            file=sys.stderr,
        )

    uq = (args.question or "").strip() or None
    path = build_report(
        subset,
        trend_keywords,
        api_key,
        date_from,
        date_to,
        user_question=uq,
        include_k10_section=not args.no_k10,
        include_k10_trends=bool(args.k10_trends),
    )
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
