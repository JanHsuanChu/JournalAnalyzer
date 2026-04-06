# agents/agent3_merge.py
# Agent 3: deterministic charts + structured LLM prose + K10 HTML fragment.

from __future__ import annotations

import html
import json
import os
import re
from datetime import datetime

import pandas as pd

from agents.theme_frequency import (
    bar_chart_html,
    count_entries_per_theme,
    monthly_mood_proxy_chart_html,
    monthly_trend_mentions_bar_chart_html,
)
from json_utils import extract_json_from_reply, normalize_agent3_sections
from k10_report_html import K10_SECTION_CSS, render_k10_section_fragment
from k10_utils import K10_ITEM_LABELS, compute_k10_history_facts, k10_domain_highlights_text
from ollama_client import simple_chat

def _trend_total_mentions(df: pd.DataFrame, phrase: str) -> int:
    """Total substring matches across all entries in df for one phrase (case-insensitive)."""
    p = (phrase or "").strip()
    if not p or df is None or df.empty:
        return 0
    if "text" not in df.columns:
        return 0
    try:
        pat = re.compile(re.escape(p), re.IGNORECASE)
    except re.error:
        return 0
    return int(df["text"].fillna("").astype(str).map(lambda t: len(pat.findall(str(t)))).sum())


def _agent3_call(api_key: str | None, model: str, system: str, brief: dict, *, timeout: int = 120) -> dict | None:
    """Single Agent 3 JSON call; returns parsed dict or None."""
    if not api_key:
        return None
    user_prompt = (
        "DATA (JSON):\n"
        + json.dumps(brief, ensure_ascii=False, default=str, indent=2)[:48_000]
        + "\n\nReturn ONLY JSON for the requested keys.\n"
    )
    raw = simple_chat(user_prompt, model=model, system=system, timeout=timeout)
    parsed = extract_json_from_reply(raw or "") if raw else None
    return parsed if isinstance(parsed, dict) else None

_REPORT_CSS = """
    body { font-family: Arial, sans-serif; max-width: 980px; margin: 40px auto; padding: 20px; background-color: #FEECEA; color: #333; }
    h1 { color: #DD4633; border-bottom: 3px solid #DD4633; padding-bottom: 10px; }
    h2 { color: #DD4633; margin-top: 28px; border-bottom: 2px solid #DD4633; padding-bottom: 5px; }
    h3 { color: #DD4633; margin-top: 20px; }
    h4 { color: #555; margin-top: 16px; font-size: 1rem; }
    hr { border: 2px solid #DD4633; margin: 30px 0; }
    table { border-collapse: collapse; width: 100%; margin: 15px 0; background-color: white; }
    th, td { border: 1px solid #ddd; padding: 8px 12px; }
    th { background-color: #DD4633; color: white; text-align: left; }
    small { font-size: 0.85em; color: #666; }
    .trend-chart-wrap { max-width: 50%; min-width: 280px; margin: 0.75rem 0 1rem 0; }
    .report-gi-section ul {
      display: block;
      list-style-type: disc;
      list-style-position: outside;
      padding-left: 1.4rem;
      margin: 0.75rem 0 1rem 0;
    }
    .report-gi-section ul > li {
      display: list-item;
      margin: 0.45rem 0;
      line-height: 1.45;
      white-space: normal;
    }
""" + K10_SECTION_CSS


def _correlation_sidecar_html(sidecar: list) -> str:
    if not sidecar:
        return ""
    lines = ["<ul>"]
    for run in sidecar:
        ma = html.escape(str(run.get("metric_a", "")))
        mb = html.escape(str(run.get("metric_b", "")))
        r = run.get("r")
        n = run.get("n")
        caveats = html.escape(str(run.get("caveats", "")))
        lines.append(
            f"<li><strong>{ma}</strong> vs <strong>{mb}</strong>: r={r}, n={n}. {caveats}</li>"
        )
    lines.append("</ul>")
    return "\n".join(lines)


def _split_trend_keywords(phrases: list[str]) -> tuple[list[str], list[str]]:
    pos: list[str] = []
    neg: list[str] = []
    for p in phrases:
        p = (p or "").strip()
        if not p:
            continue
        low = p.lower()
        if low.startswith("no ") and len(low) > 3:
            neg.append(p[3:].strip())
        else:
            pos.append(p)
    return pos, neg


def _para_html(s: str) -> str:
    s = (s or "").strip()
    if not s or s == "—":
        return "<em>—</em>"
    return html.escape(s)


def _strip_leading_bullet_token(line: str) -> str:
    line = line.strip()
    line = re.sub(r"^[-•]\s*", "", line)
    line = re.sub(r"^\d+[\).\s]+\s*", "", line).strip()
    return line


def _expand_line_into_bullet_parts(line: str) -> list[str]:
    """
    One model line may contain several '- item' segments without newlines.
    Split on spaced hyphens only when it yields multiple short segments (list-like).
    """
    line = _strip_leading_bullet_token(line)
    if not line:
        return []
    if not re.search(r"\s+-\s+", line):
        return [line]
    chunks = [c.strip() for c in re.split(r"\s+-\s+", line) if c.strip()]
    if len(chunks) >= 3:
        return chunks
    if len(chunks) == 2 and max(len(c) for c in chunks) <= 140:
        return chunks
    return [line]


def _bullet_items_from_text(text: str) -> list[str]:
    """Turn key themes / next-steps prose into a flat list of bullet strings."""
    text = (text or "").replace("\r", "\n").strip()
    if not text or text == "—":
        return []
    items: list[str] = []
    for raw_line in text.split("\n"):
        raw_line = raw_line.strip()
        if not raw_line:
            continue
        items.extend(_expand_line_into_bullet_parts(raw_line))
    return items


def _ul_from_items(items: list[str]) -> str:
    if not items:
        return "<p><em>—</em></p>"
    body = "\n".join(f"<li>{html.escape(line)}</li>" for line in items)
    return f"<ul>\n{body}\n</ul>"


def _key_themes_block(text: str, insight: dict) -> str:
    """Prefer bullet list from section text; else bullets from insight.themes."""
    t = (text or "").strip()
    if t and t != "—":
        items = _bullet_items_from_text(t)
        if items:
            return _ul_from_items(items)
        return _ul_from_items([t])
    lines: list[str] = []
    themes = insight.get("themes") or []
    dict_themes = [th for th in themes if isinstance(th, dict)]
    themes_sorted = sorted(
        dict_themes,
        key=lambda x: (x.get("order") is None, x.get("order") if x.get("order") is not None else 999),
    )
    for th in (themes_sorted if themes_sorted else dict_themes)[:8]:
        if isinstance(th, dict):
            nm = str(th.get("name", "")).strip()
            ds = str(th.get("description", "")).strip()
            if nm:
                lines.append(f"<li><strong>{html.escape(nm)}</strong>: {html.escape(ds)}</li>")
    if lines:
        return f"<ul>\n" + "\n".join(lines) + "\n</ul>"
    return "<p><em>—</em></p>"


def _suggested_next_steps_html(text: str) -> str:
    """Render suggested next steps as <ul>; split newlines, bullets, numbers, semicolons, or inline '- '."""
    t = (text or "").strip()
    if not t or t == "—":
        return "<p><em>—</em></p>"
    lines: list[str] = []
    if "\n" in t or t.startswith("-") or t.startswith("•"):
        for line in t.replace("\r", "\n").split("\n"):
            line = line.strip()
            if not line:
                continue
            lines.extend(_expand_line_into_bullet_parts(line))
    elif ";" in t:
        for part in t.split(";"):
            part = part.strip()
            if part:
                lines.extend(_expand_line_into_bullet_parts(part))
    else:
        lines.extend(_expand_line_into_bullet_parts(t))
    if not lines:
        return "<p><em>—</em></p>"
    return _ul_from_items(lines)


def _assemble_general_insights_html(
    sections: dict[str, str],
    n_entries: int,
    d0: str,
    d1: str,
    correlation_sidecar: list,
    insight: dict,
    user_query: str | None,
    trend_blocks: list[dict],
    correlation_tools_used: bool,
) -> str:
    """General Insights: synthesis-first flow with trends chart embedded under Trends."""
    data_p = (
        f'<p class="report-data-source">This analysis is based on <strong>{n_entries}</strong> journal entries '
        f"from <strong>{html.escape(d0)}</strong> to <strong>{html.escape(d1)}</strong>. "
        f"Findings are inferred from the text in this date range.</p>"
    )
    uq = (user_query or "").strip()
    your_q_block = ""
    if uq:
        your_q_block = (
            f"<p><strong>Your question:</strong> {html.escape(uq)}</p>"
            f"<p>{_para_html(sections.get('user_question_answer', ''))}</p>"
        )
    else:
        your_q_block = f"<p>{_para_html(sections.get('user_question_answer', ''))}</p>"

    trends_prose = (sections.get("trends_prose") or "").strip()
    if trends_prose in ("", "—"):
        trends_prose = (sections.get("trends_and_correlations") or "").strip()
    co_par = (sections.get("correlations_paragraph") or "").strip()
    if co_par == "—":
        co_par = ""

    if not correlation_tools_used and not correlation_sidecar:
        if not co_par:
            co_par = (
                "No pairwise correlation was computed for this run (no correlation tools were invoked "
                "or metrics had insufficient variance)."
            )

    overall_raw = (sections.get("overall_summary", "") or "").strip()
    if overall_raw and overall_raw != "—" and (d0 not in overall_raw) and (d1 not in overall_raw):
        overall_raw = (
            f"In the period {d0} to {d1}, "
            + (overall_raw[0].lower() + overall_raw[1:] if len(overall_raw) > 1 else overall_raw)
        )

    inner_parts = [
        data_p,
        "<h3>Overall summary</h3>",
        f"<p>{_para_html(overall_raw)}</p>",
        "<h3>Key themes observed</h3>",
        _key_themes_block(sections.get("key_themes_observed", ""), insight),
        "<h3>Emerging patterns</h3>",
        f"<p>{_para_html(sections.get('emerging_patterns_paragraph', ''))}</p>",
        "<h3>Fading patterns</h3>",
        f"<p>{_para_html(sections.get('fading_patterns_paragraph', ''))}</p>",
        "<h3>Your question</h3>",
        your_q_block,
        "<h3>Trends and correlations</h3>",
    ]

    if trend_blocks:
        for tb in trend_blocks:
            label = html.escape(str(tb.get("label", "") or "").strip() or "trend")
            prose = str(tb.get("prose", "") or "").strip()
            chart_html = str(tb.get("chart_html", "") or "").strip()
            inner_parts.append(f"<h4>Trend: {label}</h4>")
            inner_parts.append(f"<p>{_para_html(prose)}</p>")
            if chart_html:
                inner_parts.append('<div class="trend-chart-wrap">')
                inner_parts.append(chart_html)
                inner_parts.append("</div>")
    else:
        inner_parts.append("<h4>Trend</h4>")
        inner_parts.append(f"<p>{_para_html(trends_prose)}</p>")

    inner_parts.append("<h4>Correlation analysis</h4>")
    inner_parts.append(f"<p>{_para_html(co_par) if co_par else _para_html('—')}</p>")
    inner_parts.append(_correlation_sidecar_html(correlation_sidecar))
    inner_parts.append("<h3>Suggested next steps</h3>")
    inner_parts.append(_suggested_next_steps_html(sections.get("suggested_next_steps", "")))
    if insight.get("confidence") is not None:
        try:
            c = float(insight["confidence"])
            inner_parts.append(
                f'<p><small>Model confidence for this interpretation: {c:.0%}.</small></p>'
            )
        except (TypeError, ValueError):
            pass
    return '<div class="report-gi-section">\n' + "\n".join(inner_parts) + "\n</div>"


def _default_k10_narrative(k10: dict) -> str:
    total = k10.get("total_score", "")
    lab = k10.get("severity_label", k10.get("severity_band", ""))
    return (
        f"Estimated total K10 score is {total} ({lab}). "
        "This is a non-clinical snapshot from journal language; use it as a prompt for reflection, not a diagnosis."
    )


def build_agent3_report(
    entries_df: pd.DataFrame,
    date_from,
    date_to,
    k10_payload: dict | None,
    insight: dict,
    correlation_sidecar: list,
    include_k10_section: bool,
    include_k10_trends: bool,
    user_query: str | None,
    want_trend_chart: bool,
    trend_keywords: list[str] | None = None,
    k10_history: list[dict] | None = None,
    model_agent3: str | None = None,
    correlation_tools_used: bool = False,
) -> str:
    """
    Full HTML document: charts embedded + structured LLM sections + K10 fragment.
    """
    model = model_agent3 or os.environ.get("OLLAMA_MODEL_AGENT3", "gpt-oss:20b-cloud")
    api_key = os.environ.get("OLLAMA_API_KEY")
    trend_keywords = trend_keywords or []
    pos_kw, neg_kw = _split_trend_keywords(trend_keywords)

    n_entries = len(entries_df)
    d0 = date_from.isoformat() if hasattr(date_from, "isoformat") else str(date_from)
    d1 = date_to.isoformat() if hasattr(date_to, "isoformat") else str(date_to)

    theme_names = [t.get("name", "") for t in insight.get("themes", []) if isinstance(t, dict)]
    tf_df = count_entries_per_theme(entries_df, theme_names)
    theme_chart = bar_chart_html(tf_df, "Entries mentioning theme keywords (approximate)")

    embedded_trend_chart = ""
    trend_blocks: list[dict] = []
    if want_trend_chart:
        if trend_keywords:
            for p in trend_keywords:
                p0 = (p or "").strip()
                if not p0:
                    continue
                if p0.lower().startswith("no ") and len(p0) > 3:
                    search_phrase = p0[3:].strip()
                    label = p0
                else:
                    search_phrase = p0
                    label = p0
                total_mentions = _trend_total_mentions(entries_df, search_phrase)
                chart_html = (
                    monthly_trend_mentions_bar_chart_html(entries_df, [search_phrase]) if search_phrase else ""
                )
                trend_blocks.append(
                    {
                        "label": label,
                        "search_phrase": search_phrase,
                        "chart_html": chart_html,
                        "prose": "",
                        "total_mentions": total_mentions,
                    }
                )
        else:
            embedded_trend_chart = monthly_mood_proxy_chart_html(entries_df)

    show_theme_chart = bool(theme_names) and not trend_keywords

    k10_trend_chart_html = ""
    k10_history_facts: dict = {}
    if include_k10_trends and k10_history:
        import plotly.graph_objects as go

        xs = [row.get("created_at") or row.get("window_end_date") for row in k10_history]
        ys = [row.get("total_score") for row in k10_history]
        fig = go.Figure(go.Scatter(x=xs, y=ys, mode="lines+markers", line_color="#DD4633"))
        fig.update_layout(
            title="K10 total score (snapshots)",
            xaxis_title="Time",
            yaxis_title="Score",
            width=520,
            height=320,
        )
        k10_trend_chart_html = fig.to_html(full_html=False, include_plotlyjs="cdn")
        k10_history_facts = compute_k10_history_facts(k10_history)

    brief_gi = {
        "meta": {"entries": n_entries, "date_from": d0, "date_to": d1},
        "insight": insight,
        "correlation_sidecar": correlation_sidecar,
        "correlation_tools_used": correlation_tools_used,
        "include_k10_section": include_k10_section,
        "include_k10_trends": include_k10_trends,
        "user_query": user_query or "",
        "trend_keywords": trend_keywords,
        "trend_keywords_positive": pos_kw,
        "trend_keywords_negated": neg_kw,
        "trend_totals": {
            str(tb.get("label")): int(tb.get("total_mentions", 0)) for tb in (trend_blocks or [])
        },
    }
    system_gi = (
        "You help write supportive, neutral, non-clinical text for a journal analysis app. "
        "Rules: do not diagnose; do not use direct quotes from journals; do not invent numbers — "
        "only use data from the JSON brief (including correlation_sidecar for r values). "
        "overall_summary must synthesize themes, emerging/fading patterns, the user question (if any), "
        "and trend/correlation findings for this journal window — not a standalone anecdote unrelated to the rest. "
        f"When describing the journal window in any field, use the exact date range {d0} to {d1} (do not invent seasons "
        "or year ranges that contradict these dates). "
        "Output ONLY a JSON object with these string keys (plain text, no HTML tags): "
        "overall_summary, "
        "key_themes_observed (one bullet per line starting with - ; each bullet roughly 20–40 words describing what you observe in the journal), "
        "emerging_patterns_paragraph, fading_patterns_paragraph (each 3–5 full sentences), "
        "user_question_answer (3–5 sentences; reference time structure within the analysis range, e.g. months, and change over time when supported by the brief; if user_query empty, say there was no specific question), "
        "trends_by_keyword (JSON object: keys are each original entry in trend_keywords; values are 3–5 sentence paragraphs ONLY about that single keyword; "
        "use trend_totals to stay grounded: if a keyword has 0 mentions, explicitly say it is not mentioned in this date range; "
        "for phrases like 'no X', interpret as tracking absence or reduction of X; do not merge multiple keywords into one paragraph), "
        "correlations_paragraph (a separate paragraph interpreting correlation_sidecar; if correlation_tools_used is false and sidecar empty, say no tool-backed correlations were run), "
        "suggested_next_steps (several concrete actions; use one line per item starting with - so they render as bullets). "
        "Do not use legacy combined keys unless you must: avoid relying on emerging_and_fading or trends_and_correlations alone. "
        "Do not prefix lines with letters like A. or B. Do not repeat section titles inside the values."
    )
    system_k10 = (
        "You help write supportive, neutral, non-clinical text for a journal analysis app. "
        "Rules: do not diagnose; do not use direct quotes from journals; do not invent numbers — "
        "only use data from the JSON brief. "
        f"When describing the journal window in any field, use the exact date range {d0} to {d1}. "
        "Output ONLY a JSON object with these string keys (plain text, no HTML tags): "
        "k10_summary_narrative (2–4 sentences for the K10 table summary; no diagnosis; reflect the K10 payload and per-item patterns), "
        "k10_domain_highlights (optional 2–4 short lines grouping domains; may be left empty if unsure), "
        "k10_trend_narrative (only when include_k10_trends is true and k10_history_facts is non-empty: 3–5 sentences using ONLY k10_history_facts numbers; caveat sparse snapshots)."
    )

    parsed_gi = _agent3_call(api_key, model, system_gi, brief_gi, timeout=120)
    parsed_k10: dict | None = None
    if include_k10_section and k10_payload:
        brief_k10 = {
            "meta": {"entries": n_entries, "date_from": d0, "date_to": d1},
            "include_k10_trends": include_k10_trends,
            "k10_history_facts": k10_history_facts,
            "k10": k10_payload,
            "k10_labels": K10_ITEM_LABELS,
        }
        parsed_k10 = _agent3_call(api_key, model, system_k10, brief_k10, timeout=90)
    trends_by_keyword: dict[str, str] = {}
    if parsed_gi and isinstance(parsed_gi, dict):
        tbk = parsed_gi.get("trends_by_keyword")
        if isinstance(tbk, dict):
            for k, v in tbk.items():
                ks = str(k).strip()
                vs = str(v).strip()
                if ks and vs:
                    trends_by_keyword[ks] = vs

    sections = normalize_agent3_sections(parsed_gi, insight)
    if trend_blocks:
        for tb in trend_blocks:
            label = str(tb.get("label", "") or "").strip()
            search = str(tb.get("search_phrase", "") or "").strip()
            total = int(tb.get("total_mentions", 0) or 0)
            if total == 0:
                tb["prose"] = f"No mentions of “{label}” in this date range."
            else:
                tb["prose"] = trends_by_keyword.get(label) or trends_by_keyword.get(search) or sections.get(
                    "trends_prose", ""
                )
    elif embedded_trend_chart.strip():
        trend_blocks = [{"label": "trend", "prose": sections.get("trends_prose", ""), "chart_html": embedded_trend_chart}]

    # Attach K10 narrative fields from the K10-only Agent 3 call (prevents cross-contamination).
    if parsed_k10 and isinstance(parsed_k10, dict):
        tnn = str(parsed_k10.get("k10_trend_narrative") or "").strip()
        if tnn:
            sections["k10_trend_narrative"] = tnn
        dh2 = str(parsed_k10.get("k10_domain_highlights") or "").strip()
        if dh2:
            sections["k10_domain_highlights"] = dh2
    if include_k10_trends and k10_payload:
        dh = k10_domain_highlights_text(k10_payload)
        if dh and not (sections.get("k10_domain_highlights") or "").strip():
            sections["k10_domain_highlights"] = dh

    general_inner = _assemble_general_insights_html(
        sections,
        n_entries,
        d0,
        d1,
        correlation_sidecar,
        insight,
        user_query,
        trend_blocks,
        correlation_tools_used,
    )

    k10_block = ""
    if include_k10_section and k10_payload:
        narr = ""
        if parsed_k10 and isinstance(parsed_k10, dict):
            narr = str(parsed_k10.get("k10_summary_narrative") or "").strip()
        if not narr:
            narr = _default_k10_narrative(k10_payload)
        dh = ""
        if include_k10_trends and parsed_k10 and isinstance(parsed_k10, dict):
            dh = str(parsed_k10.get("k10_domain_highlights") or "").strip()
        if not dh and include_k10_trends:
            dh = k10_domain_highlights_text(k10_payload)
        k10_block = render_k10_section_fragment(
            k10_payload,
            narr,
            domain_highlights=dh or None,
        )

    k10_trend_narr_html = ""
    if include_k10_trends and k10_history and k10_history_facts:
        tn = (sections.get("k10_trend_narrative") or "").strip()
        if tn:
            k10_trend_narr_html = (
                f'<div class="k10-trend-narrative"><h3 class="k10-history-analysis-heading">'
                f"Longitudinal note</h3><p>{html.escape(tn)}</p></div>"
            )

    parts = [
        "<h1>Journal analysis report</h1>",
        f"<p><em>Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}</em></p>",
        "<h2>General Insights</h2>",
        general_inner,
    ]
    if show_theme_chart:
        parts.append("<h3>Theme frequency</h3>")
        parts.append(theme_chart)

    if include_k10_section:
        parts.append("<hr />")
        parts.append("<h2>K10 Summary</h2>")
        if k10_payload:
            parts.append(k10_block)
        else:
            parts.append(
                "<p><em>K10 could not be estimated for this run (model error, timeout, or insufficient journal text).</em></p>"
            )
        if k10_trend_chart_html:
            parts.append("<h3>K10 history</h3>")
            parts.append(k10_trend_chart_html)
            if k10_trend_narr_html:
                parts.append(k10_trend_narr_html)

    body = "\n".join(parts)
    return f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>Journal analysis</title>
  <style>{_REPORT_CSS}</style>
</head>
<body>
{body}
</body>
</html>"""
