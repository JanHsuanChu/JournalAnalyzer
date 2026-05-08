# run_variants.py
# Drive build_report per variant × replicate; one QC grader pass per run; append CSV.

from __future__ import annotations

import argparse
import os
from datetime import date, datetime
from pathlib import Path
from typing import Any

import yaml
from aggregate_results import append_qc_rows, ensure_csv_not_legacy_schema
from journal_context import build_journal_text_for_qc
from k10_report_parse import parse_k10_total_from_html, strip_html_to_text
from load_entries import load_dotenv_journal_analyzer, load_entries_for_qc
from paths import QC_ROOT, ensure_ja_on_path
from qc_grader import run_k10_qc_grader

ensure_ja_on_path()

from report_builder import build_report  # noqa: E402
from utils import filter_entries_by_date_only  # noqa: E402

DEFAULT_CSV = QC_ROOT / "qc_experiment_scores.csv"


def _parse_date(s: str) -> date:
    return datetime.strptime(str(s).strip(), "%Y-%m-%d").date()


def _managed_env_keys(variants: list[dict]) -> list[str]:
    base = ["OLLAMA_MODEL_AGENT1", "OLLAMA_MODEL_AGENT2", "OLLAMA_MODEL_AGENT3"]
    rag: set[str] = set()
    for v in variants:
        rag.update((v.get("rag_env") or {}).keys())
    return base + sorted(rag)


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


def run_batch(config_path: Path, csv_path: Path) -> None:
    load_dotenv_journal_analyzer()
    ensure_csv_not_legacy_schema(csv_path)

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

    df_all = load_entries_for_qc()
    if df_all is None or df_all.empty:
        raise SystemExit("No journal entries loaded (is the API running and JOURNAL_API_URL correct?)")

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
    print(
        f"Starting QC batch: {n_variants} variant(s) × {replicates} replicate(s) "
        f"({total_jobs} runs) → appending to {csv_path}",
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
            with open(report_path, encoding="utf-8") as hf:
                html = hf.read()
            k10_total = parse_k10_total_from_html(html)
            report_plain = strip_html_to_text(html)

            raw1, p1 = run_k10_qc_grader(journal_text, report_plain, rag_retrieval_context=None)
            rows_out = [
                {
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
            ]

            append_qc_rows(csv_path, rows_out)

            if summary_mode == "first" and not summary_printed:
                _print_summary_like_r(raw1, p1, k10_total)
                summary_printed = True
            if str(summary_mode).lower().startswith("best") and p1:
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

    if str(summary_mode).lower().startswith("best") and best_row:
        _print_summary_like_r(best_row.get("raw"), best_row.get("parsed"), best_row.get("k10"))

    print("✅ QC batch complete. Results appended to:", csv_path)


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
    args = p.parse_args()
    if not args.config.is_file():
        raise SystemExit(f"Missing config: {args.config} (create Quality Control Workflow/qc_config.yaml)")
    run_batch(args.config, args.csv)


if __name__ == "__main__":
    main()
