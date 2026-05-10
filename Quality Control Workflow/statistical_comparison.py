# statistical_comparison.py
# Compare QC scores across variant_label (qc_level filtered to end_to_end when column present).
# Terminal style follows dsai/09_text_analysis/03_statistical_comparison.py (emoji headings,
# ✅/❌ interpretation lines).
# Default --out: plots/qc_statistical_comparison.txt (or *_1.txt, *_2.txt, … if that path exists).

from __future__ import annotations

import argparse
import math
import os
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import IO, TextIO

import pandas as pd
import pingouin as pg
from scipy.stats import bartlett, chi2_contingency, fisher_exact

from aggregate_results import detect_qc_csv_schema, mutate_variant_label_for_gi_rag_ab_comparison
from paths import QC_ROOT, uniquify_output_path

DEFAULT_CSV = QC_ROOT / "qc_experiment_scores.csv"
DEFAULT_REPORT_OUT = QC_ROOT / "plots" / "qc_statistical_comparison.txt"


def _parse_out_argument(raw: str) -> Path | None:
    """CLI helper: `--out PATH` saves report; `--out ''` skips the file."""
    s = raw.strip()
    return None if s == "" else Path(s)


class _TeeTextIO(TextIO):
    """Write the same unicode text to stdout and an open UTF-8 report file."""

    def __init__(self, stdout: TextIO, file_obj: IO[str]) -> None:
        self._stdout = stdout
        self._file = file_obj

    def write(self, data: str) -> int:  # noqa: D102 — io.TextIOBase protocol
        self._stdout.write(data)
        self._file.write(data)
        return len(data)

    def flush(self) -> None:
        self._stdout.flush()
        self._file.flush()


@contextmanager
def _tee_stdout_report(path: Path | None):
    """Temporarily tee `sys.stdout` to *path* (UTF-8). If *path* is None, no file is written."""
    if path is None:
        yield
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    original_out = sys.stdout
    with open(path, "w", encoding="utf-8") as f_out:
        sys.stdout = _TeeTextIO(original_out, f_out)
        try:
            yield
        finally:
            sys.stdout = original_out

# Order matches [Validation criteria] Set A / Set B tables in README.
SET_A_NUMERIC_CRITERIA: list[tuple[str, str]] = [
    ("k10_total", "K-10 total (parsed from report HTML)"),
    ("frequency_rubric_mean", "Frequency rubric mean (per-item 1–5, reviewer)"),
    ("evidence_relevance_mean", "Evidence relevance mean (per-item 1–5, reviewer)"),
]

SET_A_BOOLEAN_CRITERIA: list[tuple[str, str]] = [
    ("hallucination", "K-10 scope: hallucination vs journal"),
]

SET_B_NUMERIC_CRITERIA: list[tuple[str, str]] = [
    ("trend_keyword_total_count", "Deterministic: sum of trend chart bar heights"),
    ("question_relevance_1_5", "GI: question relevance (1–5)"),
    ("trend_relevance_1_5", "GI: trend relevance (1–5)"),
    ("direction_1_5", "GI: direction / valence alignment (1–5)"),
    ("clinical_safety_1_5", "GI: clinical stance / safety (1–5)"),
    ("internal_coherence_1_5", "GI: internal coherence (1–5)"),
    ("formatting_hygiene_1_5", "GI: formatting / schema hygiene (1–5)"),
    ("replicate_duration_s", "Harness: replicate wall-clock seconds"),
]

SET_B_BOOLEAN_CRITERIA: list[tuple[str, str]] = [
    ("gi_hallucination", "GI scope: hallucination vs journal excerpt"),
    ("correlation_analysis_present", "Deterministic: substantive correlation list rows"),
]

NUMERIC_CRITERION_TITLES: dict[str, str] = dict(
    SET_A_NUMERIC_CRITERIA + SET_B_NUMERIC_CRITERIA
)


def _ruler(char: str = "=", width: int = 78) -> str:
    return char * width


def _print_banner(title_main: str, title_sub: str | None = None, *, char: str = "-") -> None:
    """Readable terminal section start (similar spirit to numbered tutorial scripts)."""
    line = _ruler(char)
    print(line)
    print(title_main.strip())
    if title_sub:
        print(title_sub.strip())
    print(line)


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


def _load_one_or_more_csvs(paths: list[Path]) -> pd.DataFrame:
    """Load a single CSV or concatenate several (union of columns; missing cells become NaN)."""
    dfs = [_read_scores_table(p) for p in paths]
    if len(dfs) == 1:
        return dfs[0]
    return pd.concat(dfs, ignore_index=True, sort=True)


def _coerce_boolean_series(s: pd.Series) -> pd.Series:
    if s.dtype == object:
        return (
            s.astype(str)
            .str.strip()
            .str.lower()
            .map({"true": True, "false": False, "1": True, "0": False, "yes": True, "no": False})
        )
    return s.astype("boolean")


def _numeric_dv_candidates(df: pd.DataFrame) -> list[str]:
    skip = {
        "variant_label",
        "rag_on",
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
    out: list[str] = []
    for c in df.columns:
        if c in skip:
            continue
        s = pd.to_numeric(df[c], errors="coerce")
        if s.notna().any():
            out.append(str(c))
    return sorted(out)


def _fmt_p(p: float) -> str:
    if p < 1e-15:
        return f"{p:.2e}"
    if p < 0.0001:
        return f"{p:.2e}"
    return f"{p:.6f}"


def _pingouin_ttest_extract(row: pd.Series) -> tuple[float | None, float | None]:
    """
    Pingouin versions differ (`p-val` vs `p_val`); some degenerate samples yield NaN.
    Returns (T or t statistic, p) — values may be NaN.
    """
    t_raw: float | None = None
    for key in ("T", "t"):
        if key in row.index:
            try:
                t_raw = float(row[key])
            except (TypeError, ValueError):
                t_raw = float("nan")
            break
    p_raw: float | None = None
    for key in ("p-val", "p_val"):
        if key in row.index:
            try:
                p_raw = float(row[key])
            except (TypeError, ValueError):
                p_raw = float("nan")
            break
    return t_raw, p_raw


def _safe_two_sample_ttest_summary(
    g0: pd.Series, g1: pd.Series, *, ma: float, mb: float
) -> tuple[float, float, str | None]:
    """
    Run pingouin two-sample t-test (Welch correction=True for summary).

    Identical within-group values → pooled SD = 0 → NaN t/p from pingouin/scipy.
    We then report t = 0, p = 1.0 and a short ⚠️ note so the run never crashes.
    """
    tt = pg.ttest(g0, g1, correction=True)
    row = tt.iloc[0]
    t_raw, p_raw = _pingouin_ttest_extract(row)
    if p_raw is not None and math.isfinite(p_raw):
        t_out = t_raw if t_raw is not None and math.isfinite(t_raw) else float("nan")
        return t_out, float(p_raw), None

    std0 = float(g0.std(ddof=1)) if len(g0) > 1 else 0.0
    std1 = float(g1.std(ddof=1)) if len(g1) > 1 else 0.0
    same_mean = math.isclose(ma, mb, rel_tol=0.0, abs_tol=1e-12)
    zero_spread = (std0 == 0.0 or math.isnan(std0)) and (std1 == 0.0 or math.isnan(std1))
    if same_mean and zero_spread:
        return (
            0.0,
            1.0,
            "   ⚠️ Both groups are constant (zero within-group variance); Welch t is undefined — "
            "using p = 1.0 (no detectable mean difference).",
        )

    t_out = t_raw if t_raw is not None and math.isfinite(t_raw) else float("nan")
    return (
        t_out,
        1.0,
        "   ⚠️ t-test returned non-finite p (numerical edge case); using p = 1.0 so the report can continue.",
    )


def _safe_two_sample_ttest_detailed(
    x: pd.Series,
    y: pd.Series,
    *,
    ma: float,
    mb: float,
    correction: bool,
) -> tuple[pd.DataFrame, float, str | None]:
    """Full pingouin table plus finite p and optional ⚠️ line (same degenerate logic as summary)."""
    tt = pg.ttest(x, y, correction=correction)
    row = tt.iloc[0]
    t_raw, p_raw = _pingouin_ttest_extract(row)
    if p_raw is not None and math.isfinite(p_raw):
        return tt, float(p_raw), None

    stdx = float(x.std(ddof=1)) if len(x) > 1 else 0.0
    stdy = float(y.std(ddof=1)) if len(y) > 1 else 0.0
    same_mean = math.isclose(ma, mb, rel_tol=0.0, abs_tol=1e-12)
    zero_spread = (stdx == 0.0 or math.isnan(stdx)) and (stdy == 0.0 or math.isnan(stdy))
    if same_mean and zero_spread:
        return (
            tt,
            1.0,
            "   ⚠️ Both groups are constant (zero within-group variance); t-test p/t undefined — "
            "using p = 1.0 for interpretation.",
        )
    return (
        tt,
        1.0,
        "   ⚠️ t-test returned non-finite p (numerical edge case); using p = 1.0 for interpretation.",
    )


def _interpret_header() -> None:
    """Match course script `03_statistical_comparison.py`: explicit interpretation block."""
    print("💡 Interpretation:")


def _welch_extract(anova_tbl: pd.DataFrame) -> tuple[float | None, float | None]:
    if anova_tbl is None or anova_tbl.empty:
        return None, None
    row = anova_tbl.iloc[0]
    p_unc = row["p_unc"] if "p_unc" in row.index else row["p-unc"]
    return float(row["F"]), float(p_unc)


def _dv_valence(dv: str) -> str:
    """
    How to phrase "direction" after a significant mean difference (teaching-script style hooks).
    - qc_higher: reviewer QC metric where larger is better rubric alignment (Likert-ish).
    - latency_lower: wall-clock replicate time (lower is faster).
    - neutral: no default valence statement (substantive interpretation is context-specific).
    """
    if dv == "replicate_duration_s":
        return "latency_lower"
    if dv.endswith("_1_5"):
        return "qc_higher"
    if dv in ("frequency_rubric_mean", "evidence_relevance_mean"):
        return "qc_higher"
    return "neutral"


def _interpret_two_group_numeric(
    dv: str, va: str, vb: str, ma: float, mb: float, p: float
) -> None:
    _interpret_header()
    if p >= 0.05:
        print(
            f"   ❌ No statistically significant difference between {va!r} and {vb!r} "
            f"at α = 0.05 (p = {_fmt_p(p)})."
        )
        print(
            "   ❌ You cannot reliably conclude which variant scores higher on this outcome "
            "from this comparison alone—the gap may be sampling noise."
        )
        print()
        return

    hi, lo = (va, vb) if ma > mb else (vb, va)
    m_hi, _m_lo = (ma, mb) if ma > mb else (mb, ma)
    v = _dv_valence(dv)
    diff = ma - mb
    print(f"   ✅ The two variants differ significantly on this measure at α = 0.05 (p = {_fmt_p(p)}).")
    print(
        f"   ✅ Mean difference ({va!r} − {vb!r}) = {diff:+.4f} ({ma:.4f} vs {mb:.4f})."
    )
    if v == "qc_higher":
        print(
            "   ✅ On this rubric-style DV, larger = better-aligned with the reviewer → "
            f"higher mean for {hi!r} than for {lo!r}."
        )
    elif v == "latency_lower":
        slower = hi
        print(
            "   ✅ For wall-clock duration, larger mean ⇒ slower runs → "
            f"{slower!r} is slower on average in this pairing."
        )
    else:
        print(
            f"   ✅ Higher mean in this pairing: {hi!r} ({m_hi:.4f}). "
            "Check the DV definition for whether “higher” is desirable."
        )
    print()


def _interpret_omnibus_course_style(*, dv: str, f_stat: float, p_val: float) -> None:
    _interpret_header()
    if p_val < 0.05:
        print(
            f"   ✅ At least one variant differs on {dv!r} (Welch omnibus F = {f_stat:.4f}, "
            f"p = {_fmt_p(p_val)})."
        )
        print(
            "   ✅ Follow up with planned contrasts or pairwise post-hoc tests "
            "(e.g. Games–Howell printed below)."
        )
    else:
        print(
            f"   ❌ No statistically significant omnibus effect across variants at α = 0.05 "
            f"(F = {f_stat:.4f}, p = {_fmt_p(p_val)})."
        )
        print(
            "   ❌ Treat visible mean gaps as exploratory unless you planned specific contrasts;"
            " avoid fishing pairwise tests after a non-significant omnibus."
        )
    print()


def _numeric_dv_ready(df: pd.DataFrame, dv: str) -> pd.DataFrame:
    """Copy with coerced DV; drop NA on dv and variant."""
    sub = df.copy()
    sub[dv] = pd.to_numeric(sub[dv], errors="coerce")
    return sub.dropna(subset=["variant_label", dv])


def _snapshot_interpret_numeric_two(p: float, variants: tuple[str, str]) -> None:
    _interpret_header()
    if p < 0.05:
        print(
            f"   ✅ Welch t-test: significant difference between {variants[0]!r} and {variants[1]!r} "
            f"at α = 0.05 (p = {_fmt_p(p)})."
        )
        print("   ✅ See Part 2 for means, t-statistic, and narrative interpretation.")
    else:
        print(
            f"   ❌ Welch t-test: no significant difference at α = 0.05 (p = {_fmt_p(p)})."
        )
        print("   ❌ Part 2 prints the full test table and direction-neutral wording.")


def _snapshot_interpret_numeric_multi(p: float, f_val: float) -> None:
    _interpret_header()
    if p < 0.05:
        print(
            f"   ✅ Welch ANOVA: omnibus significant at α = 0.05 "
            f"(F = {f_val:.4f}, p = {_fmt_p(p)})."
        )
        print("   ✅ At least one variant mean differs; inspect Part 2 for Games–Howell (if run).")
    else:
        print(
            f"   ❌ Welch ANOVA: omnibus not significant at α = 0.05 "
            f"(F = {f_val:.4f}, p = {_fmt_p(p)})."
        )
        print("   ❌ No strong evidence that variant means differ overall on this DV.")


def print_summary_numeric(
    scores: pd.DataFrame, dv: str, criterion_title: str
) -> tuple[pd.DataFrame, float | None, float | None]:
    """Dense summary rows; returns (filtered df, F, p) from Welch when k≥3."""
    sub = _numeric_dv_ready(scores, dv)
    prec = 2 if dv == "k10_total" else 3 if dv != "replicate_duration_s" else 4
    headline = f"{dv} — {criterion_title}"
    if sub.empty:
        print(f"📊 {headline}")
        print("   ⚠️ No usable numeric rows for this criterion.")
        print()
        return sub, None, None

    variants_sorted = sorted(sub["variant_label"].unique().tolist(), key=str)
    print(f"📈 {headline}")
    rows = []
    for v in variants_sorted:
        slice_v = sub.loc[sub["variant_label"] == v, dv].astype(float)
        n = int(slice_v.shape[0])
        if n == 0:
            continue
        mu = float(slice_v.mean())
        rows.append({"variant_label": v, "n": n, "mean": round(mu, prec)})
    tbl = pd.DataFrame(rows).set_index("variant_label")
    print(tbl.to_string())
    print()

    k = len(variants_sorted)
    f_val: float | None = None
    p_val: float | None = None
    if k < 2:
        print(f"   ⚠️ Need ≥2 variant levels (found {k}) — no group test.")
    elif k == 2:
        g0 = sub.loc[sub["variant_label"] == variants_sorted[0], dv].astype(float)
        g1 = sub.loc[sub["variant_label"] == variants_sorted[1], dv].astype(float)
        m0, m1 = float(g0.mean()), float(g1.mean())
        print(
            f"   Mean difference ({variants_sorted[0]!r} − {variants_sorted[1]!r}): "
            f"{(m0 - m1):+.3f}"
        )
        tstat, p_val, tt_warn = _safe_two_sample_ttest_summary(
            g0, g1, ma=m0, mb=m1
        )
        if tt_warn:
            print(tt_warn)
        t_str = f"{tstat:.4f}" if math.isfinite(tstat) else "undefined"
        print(
            f"📋 Welch t-test ({variants_sorted[0]!r} vs {variants_sorted[1]!r}): "
            f"t = {t_str}, p = {_fmt_p(p_val)}"
        )
        f_val = None
        _snapshot_interpret_numeric_two(p_val, (variants_sorted[0], variants_sorted[1]))
    else:
        anova = pg.welch_anova(dv=dv, between="variant_label", data=sub)
        f_val, p_val = _welch_extract(anova)
        if f_val is not None and p_val is not None:
            print(
                f"📋 Welch ANOVA (variant_label): F = {f_val:.4f}, p = {_fmt_p(p_val)}"
            )
            _snapshot_interpret_numeric_multi(p_val, f_val)
        else:
            print("   ⚠️ Welch ANOVA: could not compute F/p.")
    print()
    return sub, f_val, p_val


def _interpret_association_course_style(p: float, *, cells_are_replicates: bool) -> None:
    _interpret_header()
    if p < 0.05:
        print(
            f"   ✅ Association test significant at α = 0.05 (p = {_fmt_p(p)}): "
            "rates differ across variant_label."
        )
        print("   ✅ Next: inspect which cells drive the discrepancy (risk/proportion contrasts).")
    else:
        print(
            f"   ❌ No convincing association at conventional α (p = {_fmt_p(p)}): "
            "observed proportions may reflect sampling variability."
        )
        print(
            "   ❌ Avoid strong claims about variant superiority on this QC flag without more evidence."
        )
    if cells_are_replicates:
        print("   📌 Each table cell aggregates independent replicates per variant × outcome.")


def detailed_boolean_analysis(scores: pd.DataFrame, column: str, criterion_title: str) -> None:
    """Crosstab + test + interpretation (emoji style aligned with course script)."""
    if column not in scores.columns:
        return
    _print_banner(f"📊 {column} — {criterion_title}", None, char="-")
    h = _coerce_boolean_series(scores[column]).astype("boolean")
    df = scores.assign(_b=h).dropna(subset=["_b"])
    if df.empty:
        print("   ⚠️ No usable boolean rows for this criterion.\n")
        return
    htab = pd.crosstab(df["variant_label"], df["_b"], dropna=False)
    print("📊 Crosstab (rows = variant_label, columns = value):")
    print(htab.to_string())
    print()
    if htab.shape == (2, 2):
        odds, p = fisher_exact(htab.to_numpy())
        print("📋 Fisher exact test (2×2 table):")
        print(f"   odds ratio = {odds:.4f}, p = {_fmt_p(p)}")
        _interpret_association_course_style(p, cells_are_replicates=True)
    elif htab.shape[0] >= 2 and htab.shape[1] >= 2:
        chi2, p, dof, _ = chi2_contingency(htab.to_numpy())
        print("📋 Chi-square test of independence:")
        print(f"   χ² = {chi2:.4f}, df = {dof}, p = {_fmt_p(p)}")
        _interpret_association_course_style(p, cells_are_replicates=True)
    else:
        print("   ⚠️ Crosstab shape not suitable for χ² / Fisher.")
    print()


def print_summary_boolean(scores: pd.DataFrame, column: str, criterion_title: str) -> None:
    if column not in scores.columns:
        return
    h = _coerce_boolean_series(scores[column]).astype("boolean")
    df = scores.assign(_b=h).dropna(subset=["_b"])
    headline = f"{column} — {criterion_title}"
    if df.empty:
        print(f"📊 {headline}")
        print("   ⚠️ No usable boolean rows for this criterion.")
        print()
        return

    print(f"📊 {headline}")
    rows = []
    for v in sorted(df["variant_label"].unique().tolist(), key=str):
        sl = df.loc[df["variant_label"] == v, "_b"]
        n = int(sl.shape[0])
        if n == 0:
            continue
        true_ct = int(sl.astype(bool).sum())
        rate = true_ct / n
        rows.append({"variant_label": v, "TRUE_n": f"{true_ct}/{n}", "rate": round(rate, 3)})
    print(pd.DataFrame(rows).set_index("variant_label").to_string())
    print()
    htab = pd.crosstab(df["variant_label"], df["_b"], dropna=True)
    if htab.shape == (2, 2):
        odds, p = fisher_exact(htab.to_numpy())
        print(f"📋 Fisher exact (× variant_label): OR = {odds:.4f}, p = {_fmt_p(p)}")
        _interpret_association_course_style(p, cells_are_replicates=True)
    elif htab.shape[0] >= 2 and htab.shape[1] >= 2:
        chi2, p, dof, _ = chi2_contingency(htab.to_numpy())
        print(f"📋 Chi-square: χ² = {chi2:.4f}, df = {dof}, p = {_fmt_p(p)}")
        _interpret_association_course_style(p, cells_are_replicates=True)
    else:
        print("   ⚠️ Crosstab too small for χ² / Fisher.")
    print()


def detailed_numeric_analysis(scores: pd.DataFrame, dv: str, criterion_title: str) -> None:
    _print_banner(f"📊 {dv} — {criterion_title}", None, char="-")
    sub = _numeric_dv_ready(scores, dv)
    if sub.empty:
        print("   ⚠️ No usable numeric rows for this criterion.\n")
        return

    variants = sorted(sub["variant_label"].unique().tolist(), key=str)
    k = len(variants)
    summary = sub.groupby("variant_label")[dv].agg(["mean", "std", "count"]).round(4)
    print("📈 Descriptives (mean, std, count) by variant_label:")
    print(summary.to_string())
    print()

    groups = [sub.loc[sub["variant_label"] == v, dv].astype(float) for v in variants]
    var_equal = True
    if k >= 2:
        try:
            b_stat, b_p = bartlett(*groups)
            bp = float(b_p)
            var_equal = bp >= 0.05
            print("🔍 Bartlett test (homogeneity of variance across variant_label levels):")
            print(f"   statistic = {b_stat:.4f}, p-value = {_fmt_p(bp)}\n")
            status = (
                "✅ Can treat variances as plausibly equal (p ≥ 0.05) — classic ANOVA is coherent."
                if var_equal
                else "❌ Variances appear unequal (p < 0.05) — prefer Welch t / Welch ANOVA;"
                " still print ordinary ANOVA below for coursework comparison."
            )
            print(f"📊 Equal-variance heuristic: {status}\n")
        except ValueError as e:
            print(f"🔍 Bartlett skipped: {e}\n")

    if k == 2:
        x = groups[0]
        y = groups[1]
        ma = float(x.mean())
        mb = float(y.mean())
        print(
            "📋 Two-sample preview: "
            f"mean {variants[0]!r} = {ma:.4f}, mean {variants[1]!r} = {mb:.4f}; "
            f"difference ({variants[0]!r} − {variants[1]!r}) = {(ma - mb):+.4f}"
        )
        print()
        tt, p, tt_warn = _safe_two_sample_ttest_detailed(
            x, y, ma=ma, mb=mb, correction=not var_equal
        )
        label = "Student t-test" if var_equal else "Welch t-test"
        print(f"📋 {label} ({variants[0]!r} vs {variants[1]!r}) — results:")
        print(tt.to_string(index=False))
        if tt_warn:
            print(tt_warn)
        print()
        _interpret_two_group_numeric(dv, variants[0], variants[1], ma, mb, p)
    elif k >= 3:
        anova_eq = pg.anova(dv=dv, between="variant_label", data=sub)
        anova_w = pg.welch_anova(dv=dv, between="variant_label", data=sub)
        print("📋 One-way ANOVA (assumes equal variances — compare with Welch below):")
        print(anova_eq.to_string(index=False))
        print()
        print("📋 Welch ANOVA (robust when variances or n differ by variant):")
        print(anova_w.to_string(index=False))
        f_w, p_w = _welch_extract(anova_w)
        print()
        if f_w is not None and p_w is not None:
            print(f"📊 Welch omnibus highlight: F = {f_w:.4f}, p = {_fmt_p(p_w)}\n")
            _interpret_omnibus_course_style(dv=dv, f_stat=f_w, p_val=p_w)
        if p_w is not None and p_w < 0.05:
            try:
                gh = pg.pairwise_gameshowell(
                    data=sub, dv=dv, between="variant_label", effsize="hedges"
                )
                print("📋 Pairwise Games–Howell (post-hoc; use multiplicity caution):")
                print(gh.to_string(index=False))
            except Exception as exc:  # pragma: no cover
                print(f"   ⚠️ Games–Howell skipped: {exc}")
        elif p_w is not None:
            print(
                "💡 Post-hoc: Games–Howell omitted — omnibus was not significant at α = 0.05 "
                "(avoid unprompted pairwise fishing)."
            )
    else:
        print("   ⚠️ Only one variant level — no between-group test.")
    print()


def _uniq_preserve(xs: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for x in xs:
        if x in seen:
            continue
        seen.add(x)
        out.append(x)
    return out


def _resolve_restrict_dvs(work: pd.DataFrame, requested: list[str]) -> list[str]:
    dup_free = _uniq_preserve(requested)
    for dv in dup_free:
        if dv not in work.columns:
            cand = _numeric_dv_candidates(work)
            raise SystemExit(
                f"Column not in CSV after load: {dv!r}. "
                f"Numeric-ish columns: {', '.join(cand) if cand else '(none)'}"
            )
        s = pd.to_numeric(work[dv], errors="coerce")
        if not s.notna().any():
            raise SystemExit(f"Column {dv!r} has no numeric values after coercion.")
    return dup_free


def criteria_report(
    scores: pd.DataFrame,
    *,
    verbose: bool,
    restrict_dvs: list[str] | None,
    csv_schema: str | None = None,
) -> None:
    """Set A (+ Set B when columns exist): summary then detailed sections."""
    work = scores.copy()
    if "qc_level" in work.columns:
        work = work[work["qc_level"] == "end_to_end"].copy()

    schema = csv_schema if csv_schema is not None else detect_qc_csv_schema(list(work.columns))
    work, rag_axis = mutate_variant_label_for_gi_rag_ab_comparison(work, schema)
    if rag_axis:
        orig = str(work["_yaml_variant_label"].iloc[0])
        levels = ", ".join(sorted(work["variant_label"].unique()))
        print(
            "📎 RAG A/B grouping: comparing rag_on levels "
            f"({levels}) — YAML variant_label was uniform ({orig!r}). All tests use these two row groups.\n"
        )

    numeric_order_restricted: list[tuple[str, str]] | None = None
    if restrict_dvs:
        validated = _resolve_restrict_dvs(work, restrict_dvs)
        numeric_order_restricted = [
            (
                dv,
                NUMERIC_CRITERION_TITLES.get(
                    dv, f"Numeric column `{dv}` (not a named QC criterion title)"
                ),
            )
            for dv in validated
        ]

    _print_banner(
        "📋 PART 1 — SUMMARY",
        "Means & rates snapshot + Welch t / Welch ANOVA headline (preview before Part 2)",
        char="=",
    )

    def _titles_for_ordered_list(
        pairs: list[tuple[str, str]]
    ) -> None:
        for dv, title in pairs:
            if dv not in work.columns:
                continue
            print_summary_numeric(work, dv, title)

    if numeric_order_restricted is not None:
        print("⚠️ --dv mode: boolean QC summaries are omitted.\n")
        _titles_for_ordered_list(numeric_order_restricted)
    else:
        print("📊 Set A — K-10 report QC\n")
        _titles_for_ordered_list([(d, t) for d, t in SET_A_NUMERIC_CRITERIA])

        for col, title in SET_A_BOOLEAN_CRITERIA:
            if col not in work.columns:
                continue
            print_summary_boolean(work, col, title)

        has_b = any(c in work.columns for c, _ in SET_B_NUMERIC_CRITERIA + SET_B_BOOLEAN_CRITERIA)
        if has_b:
            print("📊 Set B — General Insights QC (columns present only)\n")
            _titles_for_ordered_list([(d, t) for d, t in SET_B_NUMERIC_CRITERIA])
            for col, title in SET_B_BOOLEAN_CRITERIA:
                if col not in work.columns:
                    continue
                print_summary_boolean(work, col, title)

    print()
    _print_banner(
        "📋 PART 2 — DETAILED",
        "Lesson-style blocks: 🔍 variance check, 📋 test tables, 💡 takeaway lines",
        char="=",
    )
    print()

    if numeric_order_restricted is not None:
        for dv, title in numeric_order_restricted:
            detailed_numeric_analysis(work, dv, title)
    else:
        for dv, title in SET_A_NUMERIC_CRITERIA:
            if dv not in work.columns:
                continue
            detailed_numeric_analysis(work, dv, title)
        has_b = any(c in work.columns for c, _ in SET_B_NUMERIC_CRITERIA + SET_B_BOOLEAN_CRITERIA)
        if has_b:
            for dv, title in SET_B_NUMERIC_CRITERIA:
                if dv not in work.columns:
                    continue
                detailed_numeric_analysis(work, dv, title)
        for col, title in SET_A_BOOLEAN_CRITERIA:
            if col not in work.columns:
                continue
            detailed_boolean_analysis(work, col, title)
        if has_b:
            for col, title in SET_B_BOOLEAN_CRITERIA:
                if col not in work.columns:
                    continue
                detailed_boolean_analysis(work, col, title)

    if numeric_order_restricted is None:
        print(_ruler("-"))
        print(
            "💡 Method note: with one categorical variant_label (no covariates), one-way Welch ANOVA "
            "answers the same question as OLS: DV ~ C(variant_label)."
        )
        print(
            "💡 Key takeaway: p-values distinguish signal vs noise **on this sample**; substantive "
            "“better prompting” needs design validity + domain judgement, not a single asterisk.\n"
        )

    if verbose:
        print(_ruler("="))
        print("🐞 Debug — first rows of loaded table:")
        print(work.head().to_string())
        print(f"\nShape: {work.shape}\n")


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Statistical comparison of QC scores by variant "
        "(summary + detailed analysis; optional --dv narrows numerics)."
    )
    ap.add_argument(
        "--csv",
        action="append",
        type=Path,
        dest="csvs",
        metavar="PATH",
        help="QC results CSV (repeat to concatenate several files, e.g. k10_only + k10_gi batches)",
    )
    ap.add_argument(
        "--dv",
        action="append",
        dest="dvs",
        metavar="COLUMN",
        help=(
            "Numeric column(s) to analyze only (repeat for several). Omits boolean QC rows when set. "
            "Omit to run full Set A + Set B criterion report when those columns exist in the CSV."
        ),
    )
    ap.add_argument(
        "--verbose",
        action="store_true",
        help="Append first rows / shape dump after the detailed section",
    )
    ap.add_argument(
        "--out",
        type=str,
        default=str(DEFAULT_REPORT_OUT),
        metavar="PATH",
        help=(
            "Save the full report (same text as stdout) as UTF-8. Parent dirs are created. "
            "Default: plots/qc_statistical_comparison.txt. If that file already exists, "
            "the script writes qc_statistical_comparison_1.txt, _2.txt, … (never overwrites). "
            "Disable file output: pass an empty string (e.g. --out \"\")."
        ),
    )
    args = ap.parse_args()
    csv_paths = args.csvs if args.csvs else [DEFAULT_CSV]
    for p in csv_paths:
        if not p.is_file():
            raise SystemExit(f"CSV not found: {p}")

    os.environ.setdefault("MPLCONFIGDIR", str(QC_ROOT / ".mplconfig"))

    scores = _load_one_or_more_csvs(csv_paths)

    report_path: Path | None = _parse_out_argument(args.out)
    if report_path is not None:
        intended = report_path.expanduser().resolve()
        report_path = uniquify_output_path(intended)
        if report_path != intended:
            print(
                f"📄 Requested --out file already exists — saving to:\n   {report_path}\n"
            )

    with _tee_stdout_report(report_path):
        schema = detect_qc_csv_schema(list(scores.columns))
        if schema:
            print(f"📎 Detected QC CSV template: {schema} (columns={len(scores.columns)})")
        else:
            print(
                "📎 CSV header is not an exact k10_only, k10_gi, gi_only, or gi_rag_ab template; "
                "continuing if columns needed for this run are present."
            )
        print()

        restrict = _uniq_preserve(args.dvs) if args.dvs else None
        criteria_report(
            scores,
            verbose=args.verbose,
            restrict_dvs=restrict,
            csv_schema=schema,
        )
        print("✅ statistical_comparison complete!")
        print(
            "💡 Reading guide — 📈 descriptive snapshot • 🔍 variance check • 📋 test output • 💡 takeaway."
        )
        if report_path is not None:
            print(f"📄 Report saved: {report_path.resolve()}")


if __name__ == "__main__":
    main()
