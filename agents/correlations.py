# agents/correlations.py
# Metric registry and compute_correlation for Agent 2 tools (scipy/numpy only).

from __future__ import annotations

import re
from typing import Any

import numpy as np
import pandas as pd

# Simple keyword proxies for journals (deterministic)
_ANXIETY_RE = re.compile(
    r"\b(anxious|anxiety|worry|worried|panic|nervous|stress|stressed|overwhelm|overwhelmed)\b",
    re.I,
)
_MOOD_POS_RE = re.compile(
    r"\b(happy|grateful|good day|great|joy|calm|peaceful|hopeful|motivated|productive)\b",
    re.I,
)


def metric_word_count(text: str) -> float:
    return float(len(str(text).split()))


def metric_char_count(text: str) -> float:
    return float(len(str(text)))


def metric_anxiety_hits(text: str) -> float:
    return float(len(_ANXIETY_RE.findall(str(text))))


def metric_mood_positive_hits(text: str) -> float:
    return float(len(_MOOD_POS_RE.findall(str(text))))


METRIC_REGISTRY: dict[str, dict[str, Any]] = {
    "word_count": {
        "label": "Word count per entry",
        "fn": metric_word_count,
    },
    "char_count": {
        "label": "Character count per entry",
        "fn": metric_char_count,
    },
    "anxiety_hits": {
        "label": "Anxiety-related keyword hits",
        "fn": metric_anxiety_hits,
    },
    "mood_positive_hits": {
        "label": "Positive mood keyword hits",
        "fn": metric_mood_positive_hits,
    },
}


def series_for_metric(df: pd.DataFrame, metric_id: str) -> np.ndarray | None:
    if metric_id not in METRIC_REGISTRY:
        return None
    fn = METRIC_REGISTRY[metric_id]["fn"]
    if "text" not in df.columns:
        return None
    return np.array([float(fn(str(t))) for t in df["text"]], dtype=float)


def pearson_r(a: np.ndarray, b: np.ndarray) -> tuple[float | None, int]:
    """Pearson r; requires variance and n>=2."""
    n = len(a)
    if n < 2 or len(b) != n:
        return None, n
    if np.std(a) == 0 or np.std(b) == 0:
        return None, n
    c = np.corrcoef(a, b)[0, 1]
    if np.isnan(c):
        return None, n
    return float(c), n


def compute_correlation_pair(df: pd.DataFrame, metric_a: str, metric_b: str) -> dict:
    """Return dict with r, n, method, caveats."""
    sa = series_for_metric(df, metric_a)
    sb = series_for_metric(df, metric_b)
    if sa is None or sb is None:
        return {
            "metric_a": metric_a,
            "metric_b": metric_b,
            "r": None,
            "n": len(df),
            "method": "pearson",
            "caveats": "Unknown metric id or missing text column.",
        }
    r, n = pearson_r(sa, sb)
    caveats = ""
    if r is None:
        caveats = "Insufficient variance or sample size for correlation."
    return {
        "metric_a": metric_a,
        "metric_b": metric_b,
        "r": r,
        "n": n,
        "method": "pearson",
        "caveats": caveats,
    }


def list_metrics_impl() -> dict:
    return {
        "metrics": [
            {"id": k, "label": v["label"]}
            for k, v in METRIC_REGISTRY.items()
        ]
    }


def find_correlations_all_pairs(df: pd.DataFrame, *, top_k: int | None = 12) -> list[dict]:
    """
    Pearson r for each unique pair in METRIC_REGISTRY (same dict shape as compute_correlation_pair).
    Optionally keep only top_k pairs by abs(r) when many metrics exist.
    """
    ids = list(METRIC_REGISTRY.keys())
    runs: list[dict] = []
    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            runs.append(compute_correlation_pair(df, ids[i], ids[j]))
    def sort_key(d: dict) -> float:
        r = d.get("r")
        if r is None:
            return -1.0
        return abs(float(r))

    runs.sort(key=sort_key, reverse=True)
    if top_k is not None and len(runs) > top_k:
        runs = runs[:top_k]
    return runs
