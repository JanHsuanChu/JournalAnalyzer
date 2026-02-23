# report_builder.py
# Builds the Journal Analyzer AI report: excerpts, life-activity/emotion summaries, trend-by-keyword charts.
# Used by app.py when the user clicks "Generate report".

import json
import re
from datetime import datetime
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

from utils import ollama_chat

# Directory for saved reports (next to this file)
_REPORTS_DIR = Path(__file__).resolve().parent / "reports"

# Character limits per group for Ollama token safety
_EXCERPT_CHARS_PER_GROUP = 600
_OVERALL_SAMPLE_CHARS = 2500

# Report CSS (match travel_friendliness_caucasus_report.html)
_REPORT_CSS = """
    body { font-family: Arial, sans-serif; max-width: 980px; margin: 40px auto; padding: 20px; background-color: #FEECEA; color: #333; }
    h1 { color: #DD4633; border-bottom: 3px solid #DD4633; padding-bottom: 10px; }
    h2 { color: #DD4633; margin-top: 28px; border-bottom: 2px solid #DD4633; padding-bottom: 5px; }
    h3 { color: #DD4633; margin-top: 20px; }
    hr { border: 2px solid #DD4633; margin: 30px 0; }
    table { border-collapse: collapse; width: 100%; margin: 15px 0; background-color: white; }
    th, td { border: 1px solid #ddd; padding: 8px 12px; }
    th { background-color: #DD4633; color: white; text-align: left; font-weight: bold; }
    tr:nth-child(even) { background-color: #f9f9f9; }
    small { font-size: 0.85em; color: #666; display: block; margin-top: 10px; }
    .appendix { color: #555; font-size: 0.9em; }
    .appendix table { font-size: 0.9em; }
    .appendix h2, .appendix h3 { color: #555; border-bottom-color: #999; }
"""


def _ensure_reports_dir():
    _REPORTS_DIR.mkdir(parents=True, exist_ok=True)


def _bar_chart_html(df, x_col: str, y_col: str, title: str, color: str = "#DD4633") -> str:
    """Build a Plotly bar chart and return HTML fragment (include_plotlyjs='cdn')."""
    if df is None or df.empty:
        fig = go.Figure().add_annotation(text="No data", showarrow=False)
    else:
        fig = px.bar(df, x=x_col, y=y_col, title=title)
        fig.update_traces(marker_color=color)
        fig.update_layout(margin=dict(t=40, b=60, l=60, r=40), xaxis_tickangle=-45)
    return fig.to_html(full_html=False, include_plotlyjs="cdn")


def _excerpt(text: str, max_chars: int) -> str:
    """Return text truncated to max_chars, at word boundary if possible."""
    if not text or len(text) <= max_chars:
        return (text or "").strip()
    s = text[:max_chars].rsplit(maxsplit=1)
    return (s[0] if s else text[:max_chars]).strip()


def _build_grouped_excerpts(df: pd.DataFrame, group_col: str, max_chars_per_group: int = _EXCERPT_CHARS_PER_GROUP) -> list[dict]:
    """Build list of {group: label, excerpts: string} with excerpt text per group, capped at max_chars_per_group."""
    result = []
    for name, grp in df.groupby(group_col, sort=False):
        parts = []
        total = 0
        for _, row in grp.iterrows():
            t = (row.get("text") or "").strip()
            if not t:
                continue
            take = min(len(t), max_chars_per_group - total)
            if take <= 0:
                break
            parts.append(_excerpt(t, take))
            total += len(parts[-1])
            if total >= max_chars_per_group:
                break
        result.append({"group": str(name), "excerpts": " ".join(parts)})
    return result


def _overall_sample(df: pd.DataFrame, max_chars: int = _OVERALL_SAMPLE_CHARS) -> str:
    """Concatenate truncated entry texts for overall activity/emotion prompt."""
    parts = []
    total = 0
    for _, row in df.iterrows():
        t = (row.get("text") or "").strip()
        if not t:
            continue
        take = min(len(t), 200)
        chunk = _excerpt(t, take)
        parts.append(chunk)
        total += len(chunk)
        if total >= max_chars:
            break
    return " ".join(parts)


def _phrase_matches_entry(text: str, phrase: str) -> bool:
    """True if entry text contains every word of the phrase (case-insensitive). Enables flexible matching."""
    if not text or not phrase:
        return False
    text_lower = (text or "").lower()
    words = [w.strip().lower() for w in phrase.split() if w.strip()]
    return all(word in text_lower for word in words)


def _phrase_counts_by_month(df: pd.DataFrame, phrase: str) -> pd.DataFrame:
    """Count entries per month where the phrase matches (all-words match)."""
    df = df.copy()
    df["month"] = df["date"].dt.to_period("M").astype(str)
    df["match"] = df["text"].fillna("").apply(lambda t: _phrase_matches_entry(str(t), phrase))
    out = df.groupby("month", as_index=False)["match"].sum()
    out = out.rename(columns={"match": "count"})
    out["count"] = out["count"].astype(int)
    return out


def _extract_json_from_reply(reply: str) -> dict | None:
    """Try to extract a JSON object from the model reply (may be wrapped in markdown or extra text)."""
    reply = (reply or "").strip()
    # Strip markdown code fences (```json ... ``` or ``` ... ```)
    for pattern in (r"```(?:json)?\s*([\s\S]*?)```", r"```\s*([\s\S]*?)```"):
        match = re.search(pattern, reply)
        if match:
            try:
                return json.loads(match.group(1).strip())
            except json.JSONDecodeError:
                pass
    # Try to find {...} block
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


def _observations_to_html_tables(parsed: dict, month_labels: list, dow_order: list, tod_order: list) -> str:
    """Render one parsed observations JSON as HTML tables (by month, by day, by time)."""
    def table_for(key: str, row_key: str, labels: list) -> str:
        rows = parsed.get(key) or []
        lookup = {str(r.get(row_key, "")).strip(): r.get("observation", "") for r in rows if isinstance(r, dict)}
        name_col = "Month" if row_key == "month" else ("Day" if row_key == "day" else "Time")
        lines = [f"<table><thead><tr><th>{name_col}</th><th>Observation</th></tr></thead><tbody>"]
        for label in labels:
            obs = lookup.get(str(label), "—")
            lines.append(f"<tr><td>{label}</td><td>{obs}</td></tr>")
        lines.append("</tbody></table>")
        return "\n".join(lines)

    return (
        "<h4>By month</h4>" + table_for("by_month", "month", month_labels)
        + "<h4>By day of week</h4>" + table_for("by_day_of_week", "day", dow_order)
        + "<h4>By time of day</h4>" + table_for("by_time_of_day", "time", tod_order)
    )


def _raw_to_bullet_list(raw: str) -> str:
    """Turn raw reply into a simple bullet list for fallback."""
    lines = [ln.strip() for ln in (raw or "").splitlines() if ln.strip()]
    if not lines:
        return f"<p>{raw}</p>" if raw else "<p>—</p>"
    return "<ul>" + "".join(f"<li>{ln}</li>" for ln in lines) + "</ul>"


def build_report(
    entries_df: pd.DataFrame,
    trend_keywords: list[str],
    api_key: str | None,
    date_from,
    date_to,
) -> str:
    """
    Build the AI report: overall life activity and emotion (from excerpts), observations by month/day/time,
    trends by keyword (charts + summaries), appendix. Writes HTML and returns the file path.
    """
    _ensure_reports_dir()
    now = datetime.now()
    filename = f"journal_report_{now.strftime('%Y%m%d_%H%M%S')}.html"
    out_path = _REPORTS_DIR / filename

    n_entries = len(entries_df)
    date_from_str = date_from.isoformat() if hasattr(date_from, "isoformat") else str(date_from)
    date_to_str = date_to.isoformat() if hasattr(date_to, "isoformat") else str(date_to)

    df = entries_df.copy()
    df["month"] = df["date"].dt.to_period("M").astype(str)

    # Overall sample for activity and emotion
    overall_text = _overall_sample(df, _OVERALL_SAMPLE_CHARS)

    # Overall activity (life activity: what was documented, trends, changes — NOT writing frequency)
    activity_summary = "Not available (set OLLAMA_API_KEY for AI summaries)."
    if api_key and overall_text:
        prompt = (
            "You are summarizing LIFE ACTIVITY from journal entries: what types of things were documented "
            "(e.g. work, exercise, social, routines), noteworthy observations, trends, and changes over the period. "
            "Do NOT discuss how often or when the person wrote. Use ONLY the following journal excerpts.\n\n"
            "JOURNAL EXCERPTS:\n" + overall_text + "\n\n"
            "Write 3-5 sentences on overall life activity. Be concise and data-driven; do not invent details."
        )
        reply = ollama_chat(prompt, api_key)
        if reply:
            activity_summary = reply.strip()

    # Overall emotion (3-5 sentences)
    emotion_summary = "Not available (set OLLAMA_API_KEY for AI summaries)."
    if api_key and overall_text:
        prompt = (
            "You are summarizing emotional or mood-related trends from journal entries. Use ONLY the following excerpts.\n\n"
            "JOURNAL EXCERPTS:\n" + overall_text + "\n\n"
            "Write 3-5 sentences on overall emotion or mood. Be concise and data-driven; do not invent details."
        )
        reply = ollama_chat(prompt, api_key)
        if reply:
            emotion_summary = reply.strip()

    # Grouped excerpts and group labels for observations
    by_month = _build_grouped_excerpts(df, "month")
    dow_order = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    tod_order = ["morning", "afternoon", "evening"]
    month_labels = sorted(df["month"].unique().tolist())
    df_dow = df.copy()
    df_dow["day_of_week"] = pd.Categorical(df_dow["day_of_week"], categories=dow_order, ordered=True)
    df_dow = df_dow.sort_values("day_of_week").dropna(subset=["day_of_week"])
    by_dow = _build_grouped_excerpts(df_dow, "day_of_week")
    by_tod = _build_grouped_excerpts(df, "time_of_day")

    # Observations by month / day of week / time of day — request JSON, render as tables
    obs_activity = "Not available (set OLLAMA_API_KEY for AI summaries)."
    obs_emotion = "Not available (set OLLAMA_API_KEY for AI summaries)."
    if api_key and (by_month or by_dow or by_tod):
        payload = {
            "by_month": by_month,
            "by_day_of_week": by_dow,
            "by_time_of_day": by_tod,
        }
        json_instruction = (
            f'Respond with ONLY a JSON object (no other text) with three arrays: "by_month", "by_day_of_week", "by_time_of_day". '
            f'Each array has objects with a key (e.g. "month", "day", "time") and "observation" (1-2 sentences). '
            f'Include one object per group. Months to include: {json.dumps(month_labels)}. '
            f'Days: {json.dumps(dow_order)}. Times: {json.dumps(tod_order)}.'
        )
        # Activity
        prompt_activity = (
            "Based on the following journal excerpts grouped by month, day of week, and time of day, "
            "write 1-2 sentences PER GROUP describing emerging trends in LIFE ACTIVITY "
            "(what kinds of things were documented, notable patterns or changes). Do NOT discuss how often they wrote.\n\n"
            "DATA (JSON): " + json.dumps(payload, ensure_ascii=False) + "\n\n" + json_instruction
        )
        reply_act = ollama_chat(prompt_activity, api_key)
        if reply_act:
            reply_act = reply_act.strip()
            parsed_act = _extract_json_from_reply(reply_act)
            if parsed_act:
                obs_activity = _observations_to_html_tables(parsed_act, month_labels, dow_order, tod_order)
            else:
                obs_activity = _raw_to_bullet_list(reply_act)
        # Emotion
        prompt_emotion = (
            "Based on the following journal excerpts grouped by month, day of week, and time of day, "
            "write 1-2 sentences PER GROUP describing emotional or mood-related trends.\n\n"
            "DATA (JSON): " + json.dumps(payload, ensure_ascii=False) + "\n\n" + json_instruction
        )
        reply_emo = ollama_chat(prompt_emotion, api_key)
        if reply_emo:
            reply_emo = reply_emo.strip()
            parsed_emo = _extract_json_from_reply(reply_emo)
            if parsed_emo:
                obs_emotion = _observations_to_html_tables(parsed_emo, month_labels, dow_order, tod_order)
            else:
                obs_emotion = _raw_to_bullet_list(reply_emo)

    # Per-phrase charts and AI summaries (all-words match)
    trend_sections = []
    for kw in trend_keywords:
        kw_df = _phrase_counts_by_month(entries_df, kw)
        chart_kw = _bar_chart_html(kw_df, "month", "count", f'Occurrences of "{kw}" by month')
        summary = "Not available (set OLLAMA_API_KEY for AI summaries)."
        if api_key and not kw_df.empty:
            payload = kw_df.to_dict(orient="records")
            prompt = (
                "You are summarizing a trend from journal data. Use ONLY the given counts.\n"
                f'Phrase: "{kw}". Monthly occurrence counts: '
                + json.dumps(payload)
                + "\nWrite 1-2 sentences summarizing this trend. Be concise and data-driven."
            )
            reply = ollama_chat(prompt, api_key)
            if reply:
                summary = reply.strip()
        trend_sections.append({"keyword": kw, "chart_html": chart_kw, "summary": summary})

    # Build HTML body: purpose -> Overall activity -> Overall emotion -> Observations by month/dow/tod -> Trends by keyword -> Appendix
    parts = []
    parts.append("<h1>Journal Analysis Report</h1>")
    parts.append(f"<p><em>Generated: {now.strftime('%Y-%m-%d %H:%M')} (local time)</em></p>")
    parts.append("<p><strong>Purpose of this report</strong></p>")
    parts.append(
        f"<p>This report summarizes journal entries from {date_from_str} to {date_to_str} "
        f"({n_entries} entries). It provides AI-generated summaries of <strong>life activity</strong> (what was documented, trends, changes) and "
        "<strong>emotion</strong>; observations by month, day of week, and time of day; and trends for user-specified phrases with occurrence counts by month. "
        "Conclusions are based on journal excerpts and optional Ollama Cloud summaries.</p>"
    )
    parts.append('<hr style="border: 2px solid #DD4633; margin: 30px 0;" />')

    parts.append("<h2>Overall activity</h2>")
    parts.append(f"<p>{activity_summary}</p>")
    parts.append('<hr style="border: 2px solid #DD4633; margin: 30px 0;" />')
    parts.append("<h2>Overall emotion</h2>")
    parts.append(f"<p>{emotion_summary}</p>")
    parts.append('<hr style="border: 2px solid #DD4633; margin: 30px 0;" />')

    parts.append("<h2>Observations by month, day of week, and time of day</h2>")
    parts.append("<h3>Activity</h3>")
    parts.append(obs_activity if obs_activity.strip().startswith("<") else f"<p>{obs_activity}</p>")
    parts.append("<h3>Emotion</h3>")
    parts.append(obs_emotion if obs_emotion.strip().startswith("<") else f"<p>{obs_emotion}</p>")
    parts.append('<hr style="border: 2px solid #DD4633; margin: 30px 0;" />')

    if trend_sections:
        parts.append("<h2>Trends by phrase</h2>")
        for sec in trend_sections:
            parts.append(f'<h3>"{sec["keyword"]}"</h3>')
            parts.append(sec["chart_html"])
            parts.append(f"<p>{sec['summary']}</p>")
        parts.append('<hr style="border: 2px solid #DD4633; margin: 30px 0;" />')

    parts.append('<div class="appendix">')
    parts.append("<h2>How conclusions were drawn</h2>")
    parts.append(
        "<p>Conclusions are based on journal entry excerpts (truncated for length) and on summaries "
        "generated by Ollama Cloud (model: gpt-oss:20b-cloud) when OLLAMA_API_KEY is set. "
        f"The analysis used {n_entries} entries from {date_from_str} to {date_to_str}. "
        '"Activity" refers to life activity (what was documented, trends, changes), not how often entries were written. '
        "Trend-by-phrase charts show occurrence counts per month; AI summaries use only aggregated or excerpted content.</p>"
    )
    parts.append("</div>")

    body_html = "\n".join(parts)
    full_doc = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>Journal Analysis Report</title>
  <style>
{_REPORT_CSS}
  </style>
</head>
<body>
{body_html}
</body>
</html>"""

    with open(out_path, "w", encoding="utf-8") as f:
        f.write(full_doc)

    return str(out_path)
