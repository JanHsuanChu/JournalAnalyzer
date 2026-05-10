# plot_qc_variants.py
# Boxplots and summary statistics for QC scores by variant_label
#
# Reads QC CSV(s) from run_variants (`k10_only` or `k10_gi` template), prints a
# group summary table, and saves one PNG figure with subplots (one metric per subplot).
#
# Typical use (from JournalAnalyzer/):
#   python "Quality Control Workflow/plot_qc_variants.py" \
#     --csv "Quality Control Workflow/qc_experiment_scores.csv" \
#     --out "Quality Control Workflow/plots/qc_variants_comparison.png"  # collides → *_1.png, …
# Backslash line continuation: no character after \ on each continued line (no trailing space).

from __future__ import annotations

import argparse
import os
from pathlib import Path

import matplotlib
import matplotlib.pyplot as plt
import pandas as pd

from aggregate_results import detect_qc_csv_schema, mutate_variant_label_for_gi_rag_ab_comparison
from paths import QC_ROOT, uniquify_output_path

DEFAULT_CSV = QC_ROOT / "qc_experiment_scores.csv"
DEFAULT_OUT = QC_ROOT / "plots" / "qc_variants_comparison.png"
DEFAULT_DVS = ("frequency_rubric_mean", "evidence_relevance_mean")

# Non-plotted / non-numeric QC metadata (align with statistical_comparison.py skips).
_SKIP_PLOT_COLUMNS: frozenset[str] = frozenset(
    {
        "variant_label",
        "rag_on",
        "qc_level",
        "replicate_index",
        "details",
        "report_path",
        "raw_grader_response",
        "raw_gi_grader_response",
        "gi_details",
        "gi_hallucination",
        "hallucination",
        "correlation_analysis_present",
    }
)

# When --dv is omitted and K-10 defaults are absent (e.g. gi_only CSV), plot in this order first.
_PREFERRED_NUMERIC_ORDER: tuple[str, ...] = (
    "k10_total",
    "frequency_rubric_mean",
    "evidence_relevance_mean",
    "trend_keyword_total_count",
    "question_relevance_1_5",
    "trend_relevance_1_5",
    "direction_1_5",
    "clinical_safety_1_5",
    "internal_coherence_1_5",
    "formatting_hygiene_1_5",
    "replicate_duration_s",
)

MAX_AUTO_DVS = 12


def _numeric_columns_for_auto_plot(df: pd.DataFrame) -> list[str]:
    """Columns coercible to float with ≥1 non-null value; excludes metadata and booleans-by-name."""
    preferred = [
        c
        for c in _PREFERRED_NUMERIC_ORDER
        if c in df.columns and c not in _SKIP_PLOT_COLUMNS
        and pd.to_numeric(df[c], errors="coerce").notna().any()
    ]
    seen = set(preferred)
    rest: list[str] = []
    for c in sorted(df.columns, key=str):
        if c in _SKIP_PLOT_COLUMNS or c in seen:
            continue
        s = pd.to_numeric(df[c], errors="coerce")
        if s.notna().any():
            rest.append(str(c))
            seen.add(str(c))
    return preferred + rest


def _read_scores(path: Path) -> pd.DataFrame:
    """Load QC table; support Excel-in-.csv like statistical_comparison.py."""
    with open(path, "rb") as f:
        sig = f.read(4)
    if sig.startswith(b"PK"):
        try:
            return pd.read_excel(path, engine="openpyxl")
        except ImportError as e:
            raise SystemExit(
                "File looks like Excel, not text CSV. Install openpyxl: pip install openpyxl"
            ) from e
    return pd.read_csv(path)


def _load_one_or_more_csvs(paths: list[Path]) -> pd.DataFrame:
    dfs = [_read_scores(p) for p in paths]
    if len(dfs) == 1:
        return dfs[0]
    return pd.concat(dfs, ignore_index=True, sort=True)


def main() -> None:
    os.environ.setdefault("MPLCONFIGDIR", str(QC_ROOT / ".mplconfig"))

    ap = argparse.ArgumentParser(
        description="Boxplots + summary stats for QC scores by variant_label"
    )
    ap.add_argument(
        "--csv",
        action="append",
        type=Path,
        dest="csvs",
        metavar="PATH",
        help="QC results CSV (repeat to concatenate several files)",
    )
    ap.add_argument(
        "--out",
        type=Path,
        default=DEFAULT_OUT,
        help=(
            "Output PNG path (parent dirs are created). If the file exists, "
            "writes name_1.png, name_2.png, … instead (never overwrites)."
        ),
    )
    ap.add_argument(
        "--dv",
        action="append",
        dest="dvs",
        metavar="COLUMN",
        help=(
            "Numeric column to plot (repeat for multiple). "
            f"Default: {list(DEFAULT_DVS)} when present; otherwise auto-pick numeric QC columns "
            f"(see README; max {MAX_AUTO_DVS} when auto)."
        ),
    )
    args = ap.parse_args()
    csv_paths = args.csvs if args.csvs else [DEFAULT_CSV]
    for p in csv_paths:
        if not p.is_file():
            raise SystemExit(f"CSV not found: {p}")

    scores = _load_one_or_more_csvs(csv_paths)
    if "qc_level" in scores.columns:
        scores = scores[scores["qc_level"] == "end_to_end"].copy()

    schema = detect_qc_csv_schema(list(scores.columns))
    if schema:
        print(f"📎 Detected QC CSV template: {schema} (columns={len(scores.columns)})")
    else:
        print(
            "📎 CSV header is not an exact k10_only, k10_gi, gi_only, or gi_rag_ab template; "
            "plotting requested columns only."
        )
    print()

    scores, rag_axis = mutate_variant_label_for_gi_rag_ab_comparison(scores, schema)
    if rag_axis:
        print(
            "📎 RAG A/B plot grouping: x-axis is RAG_on vs RAG_off (uniform YAML variant); "
            "matches statistical_comparison.py for Experiment C.\n"
        )

    user_specified_dv = args.dvs is not None and len(args.dvs) > 0
    if user_specified_dv:
        want = list(args.dvs)
        dvs = [d for d in want if d in scores.columns]
        missing = [d for d in want if d not in scores.columns]
        if missing:
            print(f"⚠️  Skipping columns not in CSV: {', '.join(missing)}")
        if not dvs:
            raise SystemExit(
                "No valid --dv columns present in the CSV. "
                f"Tried: {want}. Pick columns that exist (e.g. trend_relevance_1_5, replicate_duration_s)."
            )
    else:
        dvs = [d for d in DEFAULT_DVS if d in scores.columns]
        missing = [d for d in DEFAULT_DVS if d not in scores.columns]
        if missing:
            print(f"⚠️  Default columns not in CSV: {', '.join(missing)}")
        if not dvs:
            auto = _numeric_columns_for_auto_plot(scores)
            if not auto:
                raise SystemExit(
                    "No numeric columns to plot after excluding metadata. "
                    "Pass explicit --dv COLUMN names from your CSV."
                )
            if len(auto) > MAX_AUTO_DVS:
                print(
                    f"⚠️  Auto-selecting first {MAX_AUTO_DVS} numeric columns "
                    f"(of {len(auto)}); repeat --dv to choose exactly which metrics to plot."
                )
                auto = auto[:MAX_AUTO_DVS]
            dvs = auto
            print(f"📊 Auto plot metrics (--dv omitted): {', '.join(dvs)}\n")

    if "variant_label" not in scores.columns:
        raise SystemExit("CSV missing required column: variant_label")

    # --- Summary table (stdout; good for copy-paste / screenshots) ---
    print("📊 Summary by variant_label (mean, std, count):\n")
    for dv in dvs:
        sub = scores.copy()
        sub[dv] = pd.to_numeric(sub[dv], errors="coerce")
        sub = sub.dropna(subset=[dv])
        if sub.empty:
            print(f"   (no non-null rows for {dv})\n")
            continue
        g = sub.groupby("variant_label", observed=True)[dv].agg(["mean", "std", "count"]).round(3)
        print(f"--- {dv} ---")
        print(g)
        print()

    # --- Boxplots ---
    n = len(dvs)
    fig, axes = plt.subplots(1, n, figsize=(max(5 * n, 6), 4.5), squeeze=False)
    axes_flat = axes.ravel()

    for ax, dv in zip(axes_flat, dvs):
        sub = scores.copy()
        sub[dv] = pd.to_numeric(sub[dv], errors="coerce")
        sub = sub.dropna(subset=[dv, "variant_label"])
        if sub.empty:
            ax.set_title(f"{dv} (no data)")
            continue
        labels = sorted(sub["variant_label"].astype(str).unique())
        data = [sub.loc[sub["variant_label"].astype(str) == lab, dv].values for lab in labels]
        _maj, _min = (int(x) for x in matplotlib.__version__.split(".")[:2])
        if (_maj, _min) >= (3, 9):
            ax.boxplot(data, tick_labels=labels, showmeans=True)
        else:
            ax.boxplot(data, labels=labels, showmeans=True)
        ax.set_ylabel(dv)
        ax.set_title(dv)
        ax.tick_params(axis="x", rotation=25)

    fig.suptitle("QC scores by variant_label", fontsize=12, y=1.02)
    fig.tight_layout()

    intended = args.out.expanduser().resolve()
    out_path = uniquify_output_path(intended)
    if out_path != intended:
        print(
            f"📄 Requested --out file already exists — saving to:\n   {out_path}\n"
        )
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"✅ Saved plot: {out_path}")


if __name__ == "__main__":
    main()
