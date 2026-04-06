# json_utils.py
# Extract JSON from LLM replies and normalize insight_output.

from __future__ import annotations

import json
import re
from typing import Any

_MAX_QUERY_ANSWER = 8000
_MAX_THEME_DESC = 2000
_MAX_THEME_NAME = 240


def extract_json_from_reply(reply: str) -> dict | None:
    """Parse a JSON object from model text (markdown fences or bare object)."""
    reply = (reply or "").strip()
    for pattern in (r"```(?:json)?\s*([\s\S]*?)```", r"```\s*([\s\S]*?)```"):
        match = re.search(pattern, reply)
        if match:
            try:
                return json.loads(match.group(1).strip())
            except json.JSONDecodeError:
                pass
    match = re.search(r"\{[\s\S]*\}", reply)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass
    try:
        return json.loads(reply)
    except json.JSONDecodeError:
        return None


def _truncate(s: str, max_len: int) -> str:
    s = (s or "").strip()
    if len(s) <= max_len:
        return s
    return s[: max_len - 1] + "…"


def _normalize_trend_item(x: Any) -> dict[str, str]:
    if isinstance(x, dict):
        label = str(x.get("label", x.get("text", ""))).strip()
        note = str(x.get("note", "")).strip()
        direction = str(x.get("direction", "unclear")).strip().lower()
        if direction not in ("up", "down", "flat", "unclear"):
            direction = "unclear"
        return {"label": label, "direction": direction, "note": note}
    s = str(x).strip()
    return {"label": s, "direction": "unclear", "note": ""}


def normalize_insight_output(raw: dict | None) -> dict:
    """Ensure insight_output keys exist with safe defaults."""
    if not raw or not isinstance(raw, dict):
        raw = {}
    themes = raw.get("themes")
    if not isinstance(themes, list):
        themes = []
    norm_themes = []
    for t in themes[:20]:
        if isinstance(t, dict):
            name = _truncate(str(t.get("name", t.get("theme", "")).strip() or "Theme"), _MAX_THEME_NAME)
            desc = _truncate(str(t.get("description", "")).strip(), _MAX_THEME_DESC)
            entry: dict[str, Any] = {"name": name, "description": desc}
            sal = t.get("salience")
            if sal is not None:
                try:
                    sf = float(sal)
                    entry["salience"] = max(1.0, min(5.0, sf))
                except (TypeError, ValueError):
                    pass
            od = t.get("order")
            if od is not None:
                try:
                    entry["order"] = int(od)
                except (TypeError, ValueError):
                    pass
            norm_themes.append(entry)
        elif isinstance(t, str):
            norm_themes.append(
                {"name": _truncate(t.strip() or "Theme", _MAX_THEME_NAME), "description": ""}
            )
    emerging = raw.get("emerging_patterns")
    if not isinstance(emerging, list):
        emerging = []
    fading = raw.get("fading_patterns")
    if not isinstance(fading, list):
        fading = []
    trends_raw = raw.get("trends")
    if not isinstance(trends_raw, list):
        trends_raw = []
    norm_trends = [_normalize_trend_item(x) for x in trends_raw[:10]]
    qa = raw.get("query_answer")
    if qa is None:
        qa = ""
    qa = _truncate(str(qa), _MAX_QUERY_ANSWER)
    conf = raw.get("confidence")
    try:
        conf_f = float(conf) if conf is not None else 0.5
    except (TypeError, ValueError):
        conf_f = 0.5
    conf_f = max(0.0, min(1.0, conf_f))
    return {
        "themes": norm_themes,
        "emerging_patterns": [str(x) for x in emerging if str(x).strip()][:10],
        "fading_patterns": [str(x) for x in fading if str(x).strip()][:10],
        "trends": norm_trends,
        "query_answer": qa,
        "confidence": conf_f,
        "insight_schema_version": 1,
    }


def normalize_agent3_sections(raw: dict | None, insight: dict) -> dict[str, str]:
    """Fill Agent 3 section strings; fall back to insight JSON when LLM omits fields."""
    if not raw or not isinstance(raw, dict):
        raw = {}
    themes = insight.get("themes") or []
    theme_bits = []
    for t in themes[:8]:
        if isinstance(t, dict):
            nm = str(t.get("name", "")).strip()
            ds = str(t.get("description", "")).strip()
            if nm:
                theme_bits.append(f"{nm}: {ds}" if ds else nm)
    default_key_themes = "\n".join(f"- {x}" for x in theme_bits) if theme_bits else ""
    em = insight.get("emerging_patterns") or []
    fa = insight.get("fading_patterns") or []
    em_fade = ""
    if em:
        em_fade += "Emerging: " + "; ".join(str(x) for x in em[:5]) + "\n"
    if fa:
        em_fade += "Fading: " + "; ".join(str(x) for x in fa[:5])

    def _s(key: str, default: str = "") -> str:
        v = raw.get(key)
        if v is not None and str(v).strip():
            return str(v).strip()
        return default

    overall = _s("overall_summary")
    if not overall and theme_bits:
        overall = "Themes in this window include: " + "; ".join(theme_bits[:4]) + "."

    key_t = _s("key_themes_observed", _s("key_themes"))
    if not key_t and default_key_themes:
        key_t = default_key_themes

    em_par = _s("emerging_patterns_paragraph")
    fa_par = _s("fading_patterns_paragraph")
    ef_combined = _s("emerging_and_fading", _s("emerging_fading"))
    if (not em_par or em_par == "—") and em:
        em_par = " ".join(str(x) for x in em[:8])
    if (not fa_par or fa_par == "—") and fa:
        fa_par = " ".join(str(x) for x in fa[:8])
    if em_par == "—" and fa_par == "—" and ef_combined.strip():
        em_par = ef_combined

    qa = _s("user_question_answer", _s("query_answer_section"))
    if not qa:
        qa = str(insight.get("query_answer") or "")

    tr_prose = _s("trends_prose")
    co_par = _s("correlations_paragraph")
    tr_legacy = _s("trends_and_correlations")
    if not tr_prose and tr_legacy:
        parts = tr_legacy.split("\n\n", 1)
        tr_prose = parts[0].strip()
        if len(parts) > 1:
            co_par = co_par or parts[1].strip()
    if not tr_prose:
        tr_parts: list[str] = []
        for t in insight.get("trends") or []:
            if isinstance(t, dict):
                lab = str(t.get("label", "")).strip()
                note = str(t.get("note", "")).strip()
                direction = str(t.get("direction", "")).strip()
                if lab:
                    bit = lab
                    if direction and direction != "unclear":
                        bit += f" ({direction})"
                    if note:
                        bit += f": {note}"
                    tr_parts.append(bit)
            elif str(t).strip():
                tr_parts.append(str(t))
        if tr_parts:
            tr_prose = "Trends noted: " + " ".join(tr_parts)

    ns = _s("suggested_next_steps", _s("next_steps"))
    k10n = _s("k10_summary_narrative", "")
    k10_tr = _s("k10_trend_narrative", "")
    k10_dh = _s("k10_domain_highlights", "")

    return {
        "overall_summary": overall or "See themes and patterns below.",
        "key_themes_observed": key_t or "—",
        "emerging_patterns_paragraph": em_par or "—",
        "fading_patterns_paragraph": fa_par or "—",
        "emerging_and_fading": ef_combined or "—",
        "user_question_answer": (qa or "—").strip() or "—",
        "trends_prose": tr_prose or "—",
        "correlations_paragraph": co_par or "—",
        "trends_and_correlations": tr_legacy or (tr_prose if tr_prose and tr_prose != "—" else "") or "—",
        "suggested_next_steps": ns or "—",
        "k10_summary_narrative": k10n,
        "k10_trend_narrative": k10_tr,
        "k10_domain_highlights": k10_dh,
    }
