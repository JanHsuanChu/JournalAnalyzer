# aggregate_results.py
# Append QC rows to qc_experiment_scores.csv (one row per variant × replicate).

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

CSV_COLUMNS = [
    "variant_label",
    "replicate_index",
    "k10_total",
    "frequency_rubric_mean",
    "evidence_relevance_mean",
    "hallucination",
    "details",
    "report_path",
    "raw_grader_response",
]


def ensure_csv_not_legacy_schema(csv_path: Path) -> None:
    """Refuse to append if the file header does not match the current column set."""
    if not csv_path.is_file() or csv_path.stat().st_size == 0:
        return
    with open(csv_path, encoding="utf-8") as f:
        first = f.readline()
    if "qc_level" in first:
        raise SystemExit(
            "Output CSV uses the legacy schema (qc_level / tier-2 rows). "
            "Delete or rename that file, or pass a new --csv path, then run again."
        )
    if "qc_overall_score" in first:
        raise SystemExit(
            "Output CSV includes qc_overall_score (column removed). "
            "Delete or rename that file, or pass a new --csv path, then run again."
        )


def append_qc_rows(
    csv_path: Path,
    rows: list[dict[str, Any]],
) -> None:
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    write_header = not csv_path.is_file()
    with open(csv_path, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CSV_COLUMNS, extrasaction="ignore")
        if write_header:
            w.writeheader()
        for row in rows:
            out = {k: row.get(k) for k in CSV_COLUMNS}
            w.writerow(out)
