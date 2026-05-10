# aggregate_results.py
# Append QC rows (one row per variant × replicate). Output templates:
#   k10_only — original K-10 grader columns (append to historical runs).
#   k10_gi   — K-10 + General Insights deterministic + GI grader + replicate_duration_s.
#   gi_only   — GI criteria + replicate_duration_s + report_path only (no K-10 grader columns; runner skips K-10 QC call).
#   gi_rag_ab — Same as gi_only plus a rag_on column (true/false) for paired RAG-on vs RAG-off batches in one CSV.

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any, Literal

import pandas as pd

QC_OUTPUT_SCHEMA_K10_ONLY: Literal["k10_only"] = "k10_only"
QC_OUTPUT_SCHEMA_K10_GI: Literal["k10_gi"] = "k10_gi"
QC_OUTPUT_SCHEMA_GI_ONLY: Literal["gi_only"] = "gi_only"
QCOutputSchema = Literal["k10_only", "k10_gi", "gi_only", "gi_rag_ab"]

CSV_COLUMNS_K10_ONLY = [
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

# Column order matches README Set B rubric: count → relevancy → direction → … → format → latency.
_GI_QC_SUFFIX = [
    "trend_keyword_total_count",
    "question_relevance_1_5",
    "trend_relevance_1_5",
    "direction_1_5",
    "correlation_analysis_present",
    "gi_hallucination",
    "clinical_safety_1_5",
    "internal_coherence_1_5",
    "formatting_hygiene_1_5",
    "replicate_duration_s",
    "gi_details",
    "raw_gi_grader_response",
]

CSV_COLUMNS_K10_GI = CSV_COLUMNS_K10_ONLY + _GI_QC_SUFFIX

CSV_COLUMNS_GI_ONLY = [
    "variant_label",
    "replicate_index",
    "report_path",
] + list(_GI_QC_SUFFIX)

# GI-only rows plus explicit RAG state (one CSV for run_qc_rag_ab two-pass append).
CSV_COLUMNS_GI_RAG_AB = [
    "variant_label",
    "replicate_index",
    "rag_on",
    "report_path",
] + list(_GI_QC_SUFFIX)

# Back-compat: code that imported CSV_COLUMNS for the wide row shape.
CSV_COLUMNS = list(CSV_COLUMNS_K10_GI)


def csv_columns_for_schema(schema: str) -> list[str]:
    s = (schema or "").strip().lower().replace("-", "_")
    if s in ("k10_only", "k10", "narrow"):
        return list(CSV_COLUMNS_K10_ONLY)
    if s in ("k10_gi", "k10gi", "wide", "full"):
        return list(CSV_COLUMNS_K10_GI)
    if s in ("gi_only", "gionly", "gi"):
        return list(CSV_COLUMNS_GI_ONLY)
    if s in ("gi_rag_ab", "giragab", "gi_rag"):
        return list(CSV_COLUMNS_GI_RAG_AB)
    raise ValueError(f"Unknown qc_output_schema: {schema!r} (use k10_only, k10_gi, gi_only, or gi_rag_ab)")


def detect_qc_csv_schema(columns: list[str]) -> QCOutputSchema | None:
    """Infer template from a header or DataFrame.columns."""
    norm = [str(c).strip() for c in columns]
    if norm == list(CSV_COLUMNS_K10_GI):
        return "k10_gi"
    if norm == list(CSV_COLUMNS_K10_ONLY):
        return "k10_only"
    if norm == list(CSV_COLUMNS_GI_ONLY):
        return "gi_only"
    if norm == list(CSV_COLUMNS_GI_RAG_AB):
        return "gi_rag_ab"
    return None


def mutate_variant_label_for_gi_rag_ab_comparison(
    df: pd.DataFrame, schema: str | None
) -> tuple[pd.DataFrame, bool]:
    """
    Experiment C (`gi_rag_ab`): YAML `variant_label` is often fixed while `rag_on` toggles.

    When there is exactly one distinct YAML variant and ≥2 distinct `rag_on` levels, copy the
    YAML name to `_yaml_variant_label` and replace `variant_label` with ``RAG_on`` / ``RAG_off``
    so downstream stats/plots compare retrieval passes like two variants (Experiment D).

    If multiple YAML variants are present, leave `variant_label` unchanged (prompt-sweep design).
    """
    if schema != "gi_rag_ab" or "rag_on" not in df.columns:
        return df, False
    if int(df["variant_label"].nunique(dropna=True)) != 1:
        return df, False

    labels: list[str] = []
    for raw in df["rag_on"].astype(str).str.strip().str.lower():
        if raw in ("true", "1", "yes"):
            labels.append("RAG_on")
        elif raw in ("false", "0", "no"):
            labels.append("RAG_off")
        else:
            labels.append(f"rag_on={raw}")
    rag_axis = pd.Series(labels, index=df.index, dtype=object)
    if int(rag_axis.nunique(dropna=True)) < 2:
        return df, False

    out = df.copy()
    out["_yaml_variant_label"] = out["variant_label"].astype(str)
    out["variant_label"] = rag_axis.astype(str)
    return out, True


def ensure_csv_not_legacy_schema(csv_path: Path, schema: str) -> None:
    """Refuse to append if the file header does not match the chosen template."""
    expected = csv_columns_for_schema(schema)
    if not csv_path.is_file() or csv_path.stat().st_size == 0:
        return
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        header = next(reader, None)
    if not header:
        return
    header_norm = [h.strip() for h in header]
    if "qc_level" in header_norm:
        raise SystemExit(
            "Output CSV uses the legacy schema (qc_level / tier-2 rows). "
            "Delete or rename that file, or pass a new --csv path, then run again."
        )
    if "qc_overall_score" in header_norm:
        raise SystemExit(
            "Output CSV includes qc_overall_score (column removed). "
            "Delete or rename that file, or pass a new --csv path, then run again."
        )
    if header_norm != expected:
        detected = detect_qc_csv_schema(header_norm)
        hint = ""
        if detected:
            hint = f" Header matches {detected!r}; use qc_output_schema / --output-schema {detected!r}, or a different --csv path."
        raise SystemExit(
            f"CSV header does not match qc_output_schema={schema!r} ({len(expected)} columns). "
            f"File has {len(header_norm)} columns.{hint}"
        )


def append_qc_rows(
    csv_path: Path,
    rows: list[dict[str, Any]],
    *,
    schema: str = "k10_only",
) -> None:
    fieldnames = csv_columns_for_schema(schema)
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    write_header = not csv_path.is_file()
    with open(csv_path, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        if write_header:
            w.writeheader()
        for row in rows:
            out = {k: row.get(k) for k in fieldnames}
            w.writerow(out)
