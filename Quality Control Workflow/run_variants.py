# run_variants.py
# Drive build_report per variant × replicate; one QC grader pass per run; append CSV.

from __future__ import annotations

import argparse
import os
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any

import yaml
from aggregate_results import (
    CSV_COLUMNS_GI_ONLY,
    CSV_COLUMNS_GI_RAG_AB,
    CSV_COLUMNS_K10_GI,
    CSV_COLUMNS_K10_ONLY,
    append_qc_rows,
    csv_columns_for_schema,
    ensure_csv_not_legacy_schema,
)
from journal_context import build_journal_text_for_qc
from k10_report_parse import parse_k10_total_from_html, strip_html_to_text
from load_entries import load_dotenv_journal_analyzer, load_entries_for_qc
from utils import get_api_base
from paths import QC_ROOT, ensure_ja_on_path
from qc_grader import run_k10_qc_grader

ensure_ja_on_path()

from gi_qc_deterministic import extract_general_insights_body, run_gi_deterministic_qc  # noqa: E402
from gi_qc_grader import run_gi_qc_grader  # noqa: E402
from report_builder import build_report  # noqa: E402
from utils import filter_entries_by_date_only  # noqa: E402

DEFAULT_CSV = QC_ROOT / "qc_experiment_scores.csv"


def _parse_date(s: str) -> date:
    return datetime.strptime(str(s).strip(), "%Y-%m-%d").date()


def _managed_env_keys(variants: list[dict]) -> list[str]:
    base = ["OLLAMA_MODEL_AGENT1", "OLLAMA_MODEL_AGENT2", "OLLAMA_MODEL_AGENT3"]
    extra: set[str] = set()
    for v in variants:
        extra.update((v.get("rag_env") or {}).keys())
        extra.update((v.get("prompt_env") or {}).keys())
    return base + sorted(extra)


def _snapshot(keys: list[str]) -> dict[str, str | None]:
    return {k: os.environ.get(k) for k in keys}


def _restore(snapshot: dict[str, str | None]) -> None:
    for k, v in snapshot.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def _apply_variant_env(snapshot: dict[str, str | None], variant: dict) -> None:
    _restore(snapshot)
    os.environ["OLLAMA_MODEL_AGENT1"] = str(variant["OLLAMA_MODEL_AGENT1"])
    os.environ["OLLAMA_MODEL_AGENT2"] = str(
        variant.get("OLLAMA_MODEL_AGENT2") or variant["OLLAMA_MODEL_AGENT1"]
    )
    os.environ["OLLAMA_MODEL_AGENT3"] = str(
        variant.get("OLLAMA_MODEL_AGENT3") or "gpt-oss:20b-cloud"
    )
    for rk, rv in (variant.get("rag_env") or {}).items():
        os.environ[str(rk)] = str(rv)
    for pk, pv in (variant.get("prompt_env") or {}).items():
        os.environ[str(pk)] = str(pv)


def _print_gi_qc_summary(det: dict[str, Any], p_gi: dict[str, Any] | None, raw_gi: str | None) -> None:
    print("✅ General Insights QC (first replicate):")
    if det and "trend_keyword_total_count" in det:
        print(f"   trend_keyword_total_count: {det.get('trend_keyword_total_count')}")
    if p_gi:
        for k in ("question_relevance_1_5", "trend_relevance_1_5", "direction_1_5"):
            print(f"   {k}: {p_gi.get(k)}")
    if det and "correlation_analysis_present" in det:
        print(f"   correlation_analysis_present: {det.get('correlation_analysis_present')}")
    if p_gi:
        for k in (
            "gi_hallucination",
            "clinical_safety_1_5",
            "internal_coherence_1_5",
            "formatting_hygiene_1_5",
        ):
            print(f"   {k}: {p_gi.get(k)}")
        gd = (p_gi.get("gi_details") or "")[:240]
        print(f"   gi_details: {gd}")
    else:
        print("   (GI grader parse failed or skipped)")
    print()
    print("📥 GI grader raw (truncated):")
    print((raw_gi or "(none)")[:400])
    print()


def _print_summary_like_r(raw: str | None, parsed: dict[str, Any] | None, k10_total: int | None) -> None:
    print("📥 AI Response (raw):")
    print(raw if raw else "(none)")
    print()
    print("✅ Quality Control Results:")
    if parsed:
        print(f"   k10_total (parsed): {k10_total}")
        print(f"   frequency_rubric_mean: {parsed.get('frequency_rubric_mean')}")
        print(f"   evidence_relevance_mean: {parsed.get('evidence_relevance_mean')}")
        print(f"   hallucination: {parsed.get('hallucination')}")
        det = (parsed.get("details") or "")[:300]
        print(f"   details: {det}")
    else:
        print("   (parse failed)")
    print()


def _resolve_rag_on_column(schema: str, rag_on_cli: str | None) -> str | None:
    """For gi_rag_ab CSV template, return 'true' or 'false' from --rag-on or QC_RAG_ON env."""
    s = (schema or "").strip().lower().replace("-", "_")
    if s != "gi_rag_ab":
        return None
    if rag_on_cli in ("true", "false"):
        return rag_on_cli
    env = (os.environ.get("QC_RAG_ON") or "").strip().lower()
    if env in ("1", "true", "yes"):
        return "true"
    if env in ("0", "false", "no"):
        return "false"
    return None


def run_batch(
    config_path: Path,
    csv_path: Path,
    *,
    output_schema: str | None = None,
    rag_on: str | None = None,
    quiet: bool = False,
) -> None:
    load_dotenv_journal_analyzer()

    with open(config_path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    variants = cfg.get("variants") or []
    if not variants:
        raise SystemExit("config has no variants")

    shared = cfg.get("shared") or {}
    date_from = _parse_date(shared["date_from"])
    date_to = _parse_date(shared["date_to"])
    trend_keywords = list(shared.get("trend_keywords") or [])
    user_question = shared.get("user_question")
    include_k10 = bool(shared.get("include_k10_section", True))
    include_trends = bool(shared.get("include_k10_trends", False))

    qc_mode = (cfg.get("qc_journal_context") or {}).get("mode", "analysis_window")
    replicates = int(cfg.get("replicates_per_variant") or 1)
    summary_mode = str(cfg.get("qc_summary_run") or "first")
    schema = (output_schema or cfg.get("qc_output_schema") or "k10_only").strip()
    try:
        out_cols = csv_columns_for_schema(schema)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    ensure_csv_not_legacy_schema(csv_path, schema)

    is_k10_only = list(out_cols) == list(CSV_COLUMNS_K10_ONLY)
    is_k10_gi = list(out_cols) == list(CSV_COLUMNS_K10_GI)
    is_gi_only = list(out_cols) == list(CSV_COLUMNS_GI_ONLY)
    is_gi_rag_ab = list(out_cols) == list(CSV_COLUMNS_GI_RAG_AB)
    run_k10_grader = is_k10_only or is_k10_gi
    run_gi_pass = (is_k10_gi or is_gi_only or is_gi_rag_ab) and bool(cfg.get("enable_gi_qc", True))

    rag_on_value = _resolve_rag_on_column(schema, rag_on)
    if is_gi_rag_ab and rag_on_value is None:
        raise SystemExit(
            "qc_output_schema is gi_rag_ab: set rag_on via --rag-on true|false "
            "or export QC_RAG_ON=true|false for this batch."
        )

    df_all = load_entries_for_qc()
    if df_all is None:
        base = get_api_base()
        raise SystemExit(
            "No journal entries loaded: could not GET a JSON list from the Journal API.\n"
            f"  Expected URL: {base.rstrip('/')}/entries\n"
            "  Fix: start the API from JournalAnalyzer/ (e.g. "
            "`python -m uvicorn api:app --reload --port 8000`), then retry.\n"
            "  If the API is not on localhost:8000, set JOURNAL_API_URL in JournalAnalyzer/.env "
            "to match."
        )
    if df_all.empty:
        base = get_api_base()
        raise SystemExit(
            "No journal entries loaded: the API returned zero rows.\n"
            f"  Checked: {base.rstrip('/')}/entries\n"
            "  Fix: open GET /health — if entries_source is supabase, ensure journal_entry has "
            "rows and credentials in .env; if csv, ensure journal_entries.csv exists under "
            "JournalAnalyzer/ and is not empty."
        )

    entries_df = filter_entries_by_date_only(df_all, date_from, date_to)
    if entries_df.empty:
        raise SystemExit("No entries in the configured date range.")

    journal_text = build_journal_text_for_qc(entries_df, qc_mode)
    api_key = os.environ.get("OLLAMA_API_KEY", "").strip() or None
    if not api_key:
        print("Warning: OLLAMA_API_KEY unset — build_report will skip LLM agents.")

    managed_keys = _managed_env_keys(variants)
    env_snapshot = _snapshot(managed_keys)

    n_variants = len(variants)
    total_jobs = n_variants * replicates
    if not quiet:
        print(
            f"Starting QC batch: {n_variants} variant(s) × {replicates} replicate(s) "
            f"({total_jobs} runs) → appending to {csv_path} (qc_output_schema={schema!r}, {len(out_cols)} cols)",
            flush=True,
        )

    summary_printed = False
    best_row: dict[str, Any] | None = None
    job_num = 0

    for vi, variant in enumerate(variants, start=1):
        label = str(variant.get("label") or "variant")
        for rep in range(replicates):
            job_num += 1
            _apply_variant_env(env_snapshot, variant)
            print(
                f"QC progress [{job_num}/{total_jobs}]: variant {vi}/{n_variants} ({label}) "
                f"— replicate {rep + 1}/{replicates}",
                flush=True,
            )
            t0 = time.perf_counter()
            report_path = build_report(
                entries_df,
                trend_keywords,
                api_key,
                date_from,
                date_to,
                user_question=user_question,
                include_k10_section=include_k10,
                include_k10_trends=include_trends,
                status_callback=None,
            )
            t1 = time.perf_counter()
            with open(report_path, encoding="utf-8") as hf:
                html = hf.read()
            k10_total = parse_k10_total_from_html(html)
            report_plain = strip_html_to_text(html)

            raw1: str | None = None
            p1: dict[str, Any] | None = None
            if run_k10_grader:
                t2 = time.perf_counter()
                raw1, p1 = run_k10_qc_grader(journal_text, report_plain, rag_retrieval_context=None)
                t3 = time.perf_counter()
            else:
                t2 = time.perf_counter()
                t3 = t2

            det: dict[str, Any] = {}
            gi_plain = ""
            raw_gi: str | None = None
            p_gi: dict[str, Any] | None = None
            if run_gi_pass:
                gi_body = extract_general_insights_body(html)
                gi_plain = strip_html_to_text(gi_body)[:24_000]
                det = run_gi_deterministic_qc(html, trend_keywords=trend_keywords)
                if api_key:
                    raw_gi, p_gi = run_gi_qc_grader(
                        journal_text[:12_000],
                        gi_plain,
                        configured_user_question=str(user_question or ""),
                        configured_trend_keywords=trend_keywords,
                    )
            t4 = time.perf_counter()

            gi_block: dict[str, Any] = {
                "trend_keyword_total_count": det.get("trend_keyword_total_count", "")
                if run_gi_pass
                else "",
                "question_relevance_1_5": "",
                "trend_relevance_1_5": "",
                "direction_1_5": "",
                "correlation_analysis_present": det.get("correlation_analysis_present", False)
                if run_gi_pass
                else False,
                "gi_hallucination": "",
                "clinical_safety_1_5": "",
                "internal_coherence_1_5": "",
                "formatting_hygiene_1_5": "",
                "replicate_duration_s": round(t4 - t0, 3),
                "gi_details": "",
                "raw_gi_grader_response": raw_gi or "",
            }
            if p_gi:
                gi_block["question_relevance_1_5"] = p_gi.get("question_relevance_1_5")
                gi_block["trend_relevance_1_5"] = p_gi.get("trend_relevance_1_5")
                gi_block["direction_1_5"] = p_gi.get("direction_1_5")
                gi_block["gi_hallucination"] = p_gi.get("gi_hallucination")
                gi_block["formatting_hygiene_1_5"] = p_gi.get("formatting_hygiene_1_5")
                gi_block["clinical_safety_1_5"] = p_gi.get("clinical_safety_1_5")
                gi_block["internal_coherence_1_5"] = p_gi.get("internal_coherence_1_5")
                gi_block["gi_details"] = p_gi.get("gi_details")

            if is_k10_only:
                row = {
                    "variant_label": label,
                    "replicate_index": rep,
                    "k10_total": k10_total,
                    "frequency_rubric_mean": p1.get("frequency_rubric_mean") if p1 else None,
                    "evidence_relevance_mean": p1.get("evidence_relevance_mean") if p1 else None,
                    "hallucination": p1.get("hallucination") if p1 else None,
                    "details": p1.get("details") if p1 else None,
                    "report_path": report_path,
                    "raw_grader_response": raw1 or "",
                }
            elif is_gi_only or is_gi_rag_ab:
                row = {
                    "variant_label": label,
                    "replicate_index": rep,
                    "report_path": report_path,
                    **gi_block,
                }
                if is_gi_rag_ab:
                    row["rag_on"] = rag_on_value
                cols = CSV_COLUMNS_GI_RAG_AB if is_gi_rag_ab else CSV_COLUMNS_GI_ONLY
                for k in cols:
                    row.setdefault(k, "")
                if not run_gi_pass:
                    for k in cols:
                        if k not in ("variant_label", "replicate_index", "report_path", "rag_on"):
                            row[k] = ""
            else:
                # k10_gi
                row = {
                    "variant_label": label,
                    "replicate_index": rep,
                    "k10_total": k10_total,
                    "frequency_rubric_mean": p1.get("frequency_rubric_mean") if p1 else None,
                    "evidence_relevance_mean": p1.get("evidence_relevance_mean") if p1 else None,
                    "hallucination": p1.get("hallucination") if p1 else None,
                    "details": p1.get("details") if p1 else None,
                    "report_path": report_path,
                    "raw_grader_response": raw1 or "",
                    **gi_block,
                }
                for k in CSV_COLUMNS_K10_GI:
                    row.setdefault(k, "")
                if not run_gi_pass:
                    for k in CSV_COLUMNS_K10_GI:
                        if k not in {
                            "variant_label",
                            "replicate_index",
                            "k10_total",
                            "frequency_rubric_mean",
                            "evidence_relevance_mean",
                            "hallucination",
                            "details",
                            "report_path",
                            "raw_grader_response",
                            "replicate_duration_s",
                        }:
                            row[k] = ""

            rows_out = [row]

            append_qc_rows(csv_path, rows_out, schema=schema)

            if not quiet and summary_mode == "first" and not summary_printed:
                if is_gi_only or is_gi_rag_ab:
                    _print_gi_qc_summary(det, p_gi, raw_gi)
                else:
                    _print_summary_like_r(raw1, p1, k10_total)
                summary_printed = True
            if str(summary_mode).lower().startswith("best") and p1 and not (is_gi_only or is_gi_rag_ab):
                row_candidate = {"parsed": p1, "raw": raw1, "k10": k10_total}
                s = (p1.get("frequency_rubric_mean") or 0) + (p1.get("evidence_relevance_mean") or 0)
                best_s = (
                    (best_row["parsed"].get("frequency_rubric_mean") or 0)
                    + (best_row["parsed"].get("evidence_relevance_mean") or 0)
                    if best_row
                    else None
                )
                if best_row is None or best_s is None or s > best_s:
                    best_row = row_candidate

    _restore(env_snapshot)

    if (
        not quiet
        and str(summary_mode).lower().startswith("best")
        and best_row
        and not (is_gi_only or is_gi_rag_ab)
    ):
        _print_summary_like_r(best_row.get("raw"), best_row.get("parsed"), best_row.get("k10"))

    # Always print completion so quiet batches (e.g. RAG A/B) still show a clear finish line.
    print("✅ QC batch complete. Results appended to:", csv_path, flush=True)


def main() -> None:
    p = argparse.ArgumentParser(description="JournalAnalyzer QC workflow batch runner")
    p.add_argument(
        "--config",
        type=Path,
        default=QC_ROOT / "qc_config.yaml",
        help="Path to QC config YAML",
    )
    p.add_argument(
        "--csv",
        type=Path,
        default=DEFAULT_CSV,
        help="Output CSV path",
    )
    p.add_argument(
        "--output-schema",
        choices=("k10_only", "k10_gi", "gi_only", "gi_rag_ab"),
        default=None,
        help=(
            "CSV template: k10_only (K-10 grader columns only), k10_gi (K-10 + GI QC + replicate_duration_s), "
            "gi_only (GI QC + replicate_duration_s + report_path; skips K-10 grader call), "
            "gi_rag_ab (like gi_only plus rag_on true/false; use with --rag-on or QC_RAG_ON). "
            "Overrides qc_output_schema in YAML when set."
        ),
    )
    p.add_argument(
        "--rag-on",
        choices=("true", "false"),
        default=None,
        help="When schema is gi_rag_ab, value stored in the rag_on column (else use QC_RAG_ON env).",
    )
    p.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress batch start line and first-replicate rubric dumps; still print each QC progress line and the final completion line.",
    )
    args = p.parse_args()
    if not args.config.is_file():
        raise SystemExit(f"Missing config: {args.config} (create Quality Control Workflow/qc_config.yaml)")
    run_batch(
        args.config,
        args.csv,
        output_schema=args.output_schema,
        rag_on=args.rag_on,
        quiet=args.quiet,
    )


if __name__ == "__main__":
    main()
