# statistical_comparison.py
# Compare QC scores across variant_label. Legacy CSVs with qc_level filter to end_to_end.

from __future__ import annotations

import argparse
import os
from pathlib import Path

import pandas as pd
import pingouin as pg
from scipy.stats import bartlett, chi2_contingency, fisher_exact

from paths import QC_ROOT

DEFAULT_CSV = QC_ROOT / "qc_experiment_scores.csv"


def _read_scores_table(path: Path) -> pd.DataFrame:
    """
    Read QC results table from either:
    - a real text CSV, or
    - an Excel/Numbers-style ZIP container (often starts with PK) that was saved with a .csv name.
    """
    with open(path, "rb") as f:
        sig = f.read(4)

    if sig.startswith(b"PK"):
        try:
            return pd.read_excel(path, engine="openpyxl")
        except ImportError as e:
            raise SystemExit(
                "This file looks like an Excel/Numbers workbook (ZIP container), not a text CSV. "
                "Install the Excel reader dependency, then rerun:\n\n"
                "  pip install openpyxl\n"
            ) from e

    return pd.read_csv(path)


def main() -> None:
    ap = argparse.ArgumentParser(description="Statistical comparison of QC scores by variant")
    ap.add_argument("--csv", type=Path, default=DEFAULT_CSV, help="qc_experiment_scores.csv path")
    ap.add_argument(
        "--dv",
        default="frequency_rubric_mean",
        help="Dependent variable column (e.g. frequency_rubric_mean, evidence_relevance_mean)",
    )
    args = ap.parse_args()

    if not args.csv.is_file():
        raise SystemExit(f"CSV not found: {args.csv}")

    # Avoid matplotlib trying to write to ~/.matplotlib in restricted environments.
    os.environ.setdefault("MPLCONFIGDIR", str(QC_ROOT / ".mplconfig"))

    scores = _read_scores_table(args.csv)
    if "qc_level" in scores.columns:
        scores = scores[scores["qc_level"] == "end_to_end"].copy()

    # Hallucination is categorical; summarize + test separately.
    if "hallucination" in scores.columns:
        h = scores["hallucination"]
        # Coerce common string encodings to boolean.
        if h.dtype == object:
            h = (
                h.astype(str)
                .str.strip()
                .str.lower()
                .map({"true": True, "false": False, "1": True, "0": False, "yes": True, "no": False})
            )
        scores["hallucination"] = h.astype("boolean")

        htab = pd.crosstab(scores["variant_label"], scores["hallucination"], dropna=True)
        if not htab.empty and (True in htab.columns):
            total = htab.sum(axis=1)
            rate = (htab[True] / total).rename("hallucination_rate").round(3)
            print("🧪 Hallucination rate by variant_label:")
            print(pd.concat([htab, total.rename("n_total"), rate], axis=1))
            print()

        # Significance test: Fisher for 2×2, else chi-square.
        if htab.shape == (2, 2):
            odds, p = fisher_exact(htab.to_numpy())
            print("🧪 Fisher exact test (hallucination × variant_label):")
            print(f"   odds_ratio: {odds:.4f}, p-value: {p:.6f}")
            print()
        elif htab.shape[0] >= 2 and htab.shape[1] >= 2:
            chi2, p, dof, _exp = chi2_contingency(htab.to_numpy())
            print("🧪 Chi-square test (hallucination × variant_label):")
            print(f"   chi2: {chi2:.4f}, dof: {dof}, p-value: {p:.6f}")
            print()
    # Coerce DV to numeric so stats functions behave consistently.
    scores[args.dv] = pd.to_numeric(scores[args.dv], errors="coerce")
    scores = scores.dropna(subset=[args.dv])
    if scores.empty:
        raise SystemExit(f"No rows with non-null {args.dv}")

    print("📊 Quality Control Scores (filtered):")
    print(scores.head())
    print(f"\nShape: {scores.shape}")
    print(f"Columns: {list(scores.columns)}\n")

    variants = scores["variant_label"].unique().tolist()
    print("Variants:", variants)
    print()

    summary = scores.groupby("variant_label")[args.dv].agg(["mean", "std", "count"]).round(3)
    print("📈 Summary by variant_label:")
    print(summary)
    print()

    # Cast to float for scipy stats (avoids NaN→int assignment edge-cases).
    groups = [scores.query("variant_label == @v")[args.dv].astype(float) for v in variants]
    var_equal = True
    if len(variants) >= 2:
        b_stat, b_p = bartlett(*groups)
        print("🔍 Bartlett test (homogeneity of variance):")
        print(f"   statistic: {b_stat:.4f}, p-value: {b_p:.4f}")
        var_equal = b_p >= 0.05
        print(
            "   Equal variance:",
            "yes (p >= 0.05)" if var_equal else "no — prefer Welch",
        )
        print()

    if len(variants) == 2:
        a, b = variants[0], variants[1]
        x = scores.query("variant_label == @a")[args.dv].astype(float)
        y = scores.query("variant_label == @b")[args.dv].astype(float)
        print(f"📊 T-test: {a} vs {b}")
        tt = pg.ttest(x, y, correction=not var_equal)
        print(tt)
        print()
    elif len(variants) >= 3:
        if var_equal:
            anova = pg.anova(dv=args.dv, between="variant_label", data=scores)
            print("📊 ANOVA (equal variances assumed):")
        else:
            anova = pg.welch_anova(dv=args.dv, between="variant_label", data=scores)
            print("📊 Welch ANOVA:")
        print(anova)
        print()

    print("✅ statistical_comparison complete")


if __name__ == "__main__":
    main()
