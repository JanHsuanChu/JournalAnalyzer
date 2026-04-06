# k10_utils.py
# K10 severity bands and validation (aligned with dsai/08_function_calling/journal_k10_workflow.py).

from __future__ import annotations

import ast
import json
import re
import sys
from typing import Any

# 0. Constants (same order as K10) #################################

K10_ITEM_LABELS = [
    "Tired out for no good reason",
    "Nervous",
    "So nervous nothing could calm you down",
    "Hopeless",
    "Restless or fidgety",
    "So depressed nothing could cheer you up",
    "Everything was an effort",
    "Worthless",
    "Depressed",
    "So restless it was hard to sit still",
]

# Official K10 question stems (same wording as journal_k10_workflow OFFICIAL_K10_ROWS), item order 1–10 for RAG embeddings.
K10_RAG_QUERIES = [
    "About how often did you feel tired out for no good reason?",
    "About how often did you feel nervous?",
    "About how often did you feel so nervous that nothing could calm you down?",
    "About how often did you feel hopeless?",
    "About how often did you feel restless or fidgety?",
    "About how often did you feel so sad that nothing could cheer you up?",
    "About how often did you feel that everything was an effort?",
    "About how often did you feel worthless?",
    "About how often did you feel depressed?",
    "About how often did you feel so restless you could not sit still?",
]

DISCLAIMER = (
    "This is an informal estimate from diary text, not a clinical K10 administration. "
    "It is not a diagnosis or medical advice."
)


def severity_band(total: int) -> str:
    """Band key from total score 10–50."""
    if 10 <= total <= 15:
        return "low"
    if 16 <= total <= 21:
        return "moderate"
    if 22 <= total <= 29:
        return "high"
    return "very_high"


def severity_label(total: int) -> str:
    """Human-readable label for conventional K10 ranges."""
    if 10 <= total <= 15:
        return "Low distress (10–15)"
    if 16 <= total <= 21:
        return "Moderate distress (16–21)"
    if 22 <= total <= 29:
        return "High distress (22–29)"
    if 30 <= total <= 50:
        return "Very high distress (30–50)"
    return "Outside expected range (10–50); interpret with caution."


def _unwrap_nested_list(raw: Any) -> Any:
    if isinstance(raw, (list, tuple)) and len(raw) == 1 and isinstance(raw[0], (list, tuple)):
        return raw[0]
    return raw


def _coerce_int_list(raw: Any, label: str) -> list[int]:
    if isinstance(raw, str):
        s = raw.strip()
        try:
            raw = json.loads(s)
        except json.JSONDecodeError:
            try:
                raw = ast.literal_eval(s)
            except (ValueError, SyntaxError) as e:
                raise ValueError(f"{label}: could not parse list from string: {e}") from e
    raw = _unwrap_nested_list(raw)
    if not isinstance(raw, (list, tuple)):
        raise ValueError(f"{label} must be a list, got {type(raw)}")
    return [int(x) for x in raw]


# K10 Likert frequency labels (same wording as k10_report_html.FREQUENCY_LABELS); evidence must not be label-only.
_K10_FREQ_LABELS_LOWER = frozenset(
    x.lower()
    for x in (
        "None of the time",
        "A little of the time",
        "Some of the time",
        "Most of the time",
        "All of the time",
    )
)


def _sanitize_item_evidence_text(ev_txt: str, score: int) -> str:
    """
    For item scores >= 2, require narrative diary-grounded evidence.
    Replace numeric-only, label-only, or non-alphabetic strings with em dash placeholder.
    """
    if score <= 1:
        return ev_txt
    t = (ev_txt or "").strip()
    if not t:
        return "—"
    if len(t) < 2:
        return "—"
    # Bare numerals or number-like tokens only (e.g. "3", "4 ", "2-3")
    if re.fullmatch(r"[\d\s\.\-]+", t):
        return "—"
    if not any(c.isalpha() for c in t):
        return "—"
    if t.lower() in _K10_FREQ_LABELS_LOWER:
        return "—"
    return t


def coerce_k10_item_scores(raw: list) -> list[int]:
    """Ten ints 1–5; pad/truncate; map legacy 0–4 to 1–5."""
    scores = _coerce_int_list(raw, "item_scores")
    n = len(scores)
    if n != 10:
        print(
            f"Note: item_scores had length {n}; normalized to 10 (truncate or pad with 1).",
            file=sys.stderr,
        )
    if n > 10:
        scores = scores[:10]
    elif n < 10:
        scores = list(scores) + [1] * (10 - n)
    out = []
    for s in scores:
        if 0 <= s <= 4:
            s = s + 1
        out.append(max(1, min(5, s)))
    return out


def estimate_k10_from_journal(
    item_scores: list,
    item_evidence: list | str,
    journal_text: str,
) -> dict:
    """Validate tool args; return k10_proxy_v2 dict."""
    journal_text = "" if journal_text is None else str(journal_text)
    clamped = coerce_k10_item_scores(item_scores)
    if isinstance(item_evidence, str):
        s = item_evidence.strip()
        try:
            item_evidence = json.loads(s)
        except json.JSONDecodeError:
            try:
                item_evidence = ast.literal_eval(s)
            except (ValueError, SyntaxError):
                item_evidence = [item_evidence]
    item_evidence = _unwrap_nested_list(item_evidence)
    if not isinstance(item_evidence, (list, tuple)):
        item_evidence = [str(item_evidence)]
    ev = [str(x) if x is not None else "" for x in list(item_evidence)]
    while len(ev) < 10:
        ev.append("")
    ev = ev[:10]
    total = sum(clamped)
    items_out = []
    for i, lab in enumerate(K10_ITEM_LABELS):
        sc = clamped[i]
        ev_txt = (ev[i] or "").strip()[:500]
        ev_txt = _sanitize_item_evidence_text(ev_txt, sc)
        if sc >= 2 and not ev_txt:
            ev_txt = "—"
        items_out.append(
            {
                "item_index": i + 1,
                "label": lab,
                "score_1_to_5": sc,
                "evidence": ev_txt,
            }
        )
    safety = _safety_scan(journal_text or "")
    return {
        "schema": "k10_proxy_v2",
        "item_scores": clamped,
        "items": items_out,
        "total_score": total,
        "severity_band": severity_band(total),
        "severity_label": severity_label(total),
        "confidence_note": "Based on diary language only; sparse entries imply lower confidence.",
        "safety_flags": safety,
        "disclaimer": DISCLAIMER,
    }


# Grouped domains for compact highlights (item_index 1–10, same order as K10_ITEM_LABELS).
K10_DOMAIN_GROUPS: list[tuple[str, list[int]]] = [
    ("Energy and fatigue", [1]),
    ("Anxiety and nervous arousal", [2, 3, 10]),
    ("Low mood, hopelessness, and worthlessness", [4, 6, 8, 9]),
    ("Restlessness, depression, and effort", [5, 7]),
]


def compute_k10_history_facts(history: list[dict] | None) -> dict[str, Any]:
    """Summarize snapshot series for Agent 3 brief (no LLM)."""
    if not history:
        return {}
    scores: list[int] = []
    date_keys: list[str] = []
    for row in history:
        ts = row.get("total_score")
        if ts is not None:
            try:
                scores.append(int(ts))
            except (TypeError, ValueError):
                pass
        wd = row.get("window_end_date") or row.get("created_at")
        if wd is not None:
            date_keys.append(str(wd)[:10])
    out: dict[str, Any] = {"n_snapshots": len(history)}
    if not scores:
        out["direction"] = "unclear"
        return out
    n = len(scores)
    mn, mx = min(scores), max(scores)
    mean = sum(scores) / n
    direction = "unclear"
    if n >= 2:
        mid = n // 2
        first = scores[:mid] or scores[:1]
        second = scores[mid:] or scores[-1:]
        a = sum(first) / len(first)
        b = sum(second) / len(second)
        if b > a + 1:
            direction = "up"
        elif b < a - 1:
            direction = "down"
        else:
            direction = "flat"
    out.update(
        {
            "min_score": mn,
            "max_score": mx,
            "mean_score": round(mean, 2),
            "direction": direction,
        }
    )
    if date_keys:
        out["date_span"] = {"earliest": min(date_keys), "latest": max(date_keys)}
    return out


def k10_domain_highlights_text(payload: dict) -> str:
    """Deterministic compact lines from current item scores."""
    items = payload.get("items") or []
    by_idx: dict[int, int] = {}
    for row in items:
        try:
            idx = int(row.get("item_index", -1))
        except (TypeError, ValueError):
            continue
        s = row.get("score_1_to_5")
        if s is None and row.get("score_0_to_4") is not None:
            try:
                s = int(row["score_0_to_4"]) + 1
            except (TypeError, ValueError):
                s = None
        else:
            try:
                s = int(s) if s is not None else None
            except (TypeError, ValueError):
                s = None
        if s is not None and 1 <= s <= 5:
            by_idx[idx] = s
    lines: list[str] = []
    for title, indices in K10_DOMAIN_GROUPS:
        vals = [by_idx[i] for i in indices if i in by_idx]
        if not vals:
            continue
        mx = max(vals)
        avg = sum(vals) / len(vals)
        lines.append(
            f"{title}: strongest item score in this group is {mx} (1–5); group average about {avg:.1f}."
        )
    return "\n".join(lines)


def _safety_scan(text: str) -> dict:
    t = text.lower()
    patterns = {
        "possible_self_harm_mention": r"\b(kill myself|end it all|suicid|self[- ]harm|hurt myself)\b",
        "possible_crisis_language": r"\b(can't go on|hopeless enough to|no way out)\b",
    }
    flags = {}
    for key, pat in patterns.items():
        flags[key] = bool(re.search(pat, t))
    flags["any"] = any(flags.values())
    return flags


def tool_estimate_k10_from_journal_for_blob(diary_blob: str):
    """Return a callable that fills journal_text if the model omits it."""

    def _run(**func_args: object) -> dict:
        jt = func_args.get("journal_text")
        if jt is None or (isinstance(jt, str) and not str(jt).strip()):
            jt = diary_blob
        return estimate_k10_from_journal(
            func_args.get("item_scores", []),
            func_args.get("item_evidence", []),
            str(jt),
        )

    return _run
