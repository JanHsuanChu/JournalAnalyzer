# agents/theme_frequency.py
# Deterministic theme counts for bar charts.

from __future__ import annotations

import re

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

def count_entries_per_theme(df: pd.DataFrame, theme_names: list[str]) -> pd.DataFrame:
    """Count rows whose text matches any word in each theme name (simple OR word match)."""
    if df is None or df.empty or not theme_names:
        return pd.DataFrame(columns=["theme", "count"])
    text_col = df["text"].fillna("").astype(str)
    rows = []
    for name in theme_names:
        words = [w for w in re.split(r"\W+", name.lower()) if len(w) > 2]
        if not words:
            continue
        pat = "|".join(re.escape(w) for w in words)
        try:
            mask = text_col.str.contains(pat, case=False, regex=True, na=False)
        except re.error:
            mask = text_col.str.contains(re.escape(name), case=False, regex=False, na=False)
        rows.append({"theme": name[:80], "count": int(mask.sum())})
    return pd.DataFrame(rows)


def _monthly_metric_series(df: pd.DataFrame, metric_fn) -> pd.DataFrame:
    """Rows: month, value (mean per entry metric)."""
    if df is None or df.empty or "date" not in df.columns:
        return pd.DataFrame(columns=["month", "value"])
    d = df.copy()
    d["month"] = pd.to_datetime(d["date"]).dt.to_period("M").astype(str)
    vals = []
    for _, row in d.iterrows():
        t = str(row.get("text", ""))
        vals.append(metric_fn(t))
    d["mv"] = vals
    g = d.groupby("month", as_index=False)["mv"].mean()
    return g.rename(columns={"mv": "value"})


def _compile_trend_pattern(phrases: list[str]) -> re.Pattern | None:
    parts: list[str] = []
    for p in phrases:
        p = (p or "").strip()
        if not p:
            continue
        parts.append(re.escape(p))
    if not parts:
        return None
    return re.compile("|".join(parts), re.IGNORECASE)


def monthly_trend_mentions_bar_chart_html(df: pd.DataFrame, phrases: list[str]) -> str:
    """
    Bar chart: total substring match counts per calendar month (sum of matches across entries).
    Half-width layout for embedding under General Insights trend prose.
    """
    pat = _compile_trend_pattern(phrases)
    if pat is None:
        return "<p><em>No trend phrases to chart.</em></p>"
    if df is None or df.empty or "date" not in df.columns:
        return "<p><em>No monthly trend data.</em></p>"
    d = df.copy()
    d["month"] = pd.to_datetime(d["date"]).dt.to_period("M").astype(str)
    d["mentions"] = d["text"].fillna("").astype(str).map(lambda t: len(pat.findall(str(t))))
    mdf = d.groupby("month", as_index=False)["mentions"].sum()
    if mdf.empty:
        return "<p><em>No monthly trend data.</em></p>"
    if int(mdf["mentions"].max()) == 0:
        label = ", ".join(p.strip() for p in phrases if p.strip()) or "trend"
        return f"<p><em>No mentions of “{label}” in this date range.</em></p>"
    label = ", ".join(p.strip() for p in phrases if p.strip()) or "trend"
    title = f"Trend to analyze: {label} (mention counts by month)"
    fig = px.bar(mdf, x="month", y="mentions", title=title)
    fig.update_traces(marker_color="#DD4633")
    ymax = float(mdf["mentions"].max())
    yaxis_opts: dict = {"tickformat": ".0f", "rangemode": "tozero"}
    if ymax <= 40:
        yaxis_opts["dtick"] = 1
    fig.update_layout(
        yaxis_title="Total mentions",
        width=520,
        height=320,
        margin=dict(t=50, b=80, l=56, r=28),
    )
    fig.update_yaxes(**yaxis_opts)
    return fig.to_html(full_html=False, include_plotlyjs="cdn")


def monthly_trend_phrase_chart_html(df: pd.DataFrame, phrases: list[str]) -> str:
    """
    Line chart: monthly mean count of substring matches for user-supplied trend phrase(s) (OR).
    """
    pat = _compile_trend_pattern(phrases)
    if pat is None:
        return "<p><em>No trend phrases to chart.</em></p>"

    def fn(t: str) -> float:
        return float(len(pat.findall(str(t))))

    mdf = _monthly_metric_series(df, fn)
    if mdf.empty:
        return "<p><em>No monthly trend data.</em></p>"
    label = ", ".join(p.strip() for p in phrases if p.strip()) or "trend"
    title = f"Trend to analyze: {label} (mean matches per entry, by month)"
    fig = px.line(mdf, x="month", y="value", title=title)
    fig.update_traces(line_color="#DD4633")
    fig.update_layout(yaxis_title="Mean matches per entry")
    return fig.to_html(full_html=False, include_plotlyjs="cdn")


def monthly_mood_proxy_chart_html(df: pd.DataFrame) -> str:
    """Fallback line chart when user wants a trend chart but did not specify trend keywords."""
    pos = re.compile(
        r"\b(happy|grateful|good day|great|joy|calm|peaceful|hopeful|motivated|productive)\b",
        re.I,
    )

    def fn(t: str) -> float:
        return float(len(pos.findall(str(t))))

    mdf = _monthly_metric_series(df, fn)
    if mdf.empty:
        return "<p><em>No monthly trend data.</em></p>"
    fig = px.line(mdf, x="month", y="value", title="Mood-related keyword density (monthly mean)")
    fig.update_traces(line_color="#DD4633")
    fig.update_layout(
        yaxis_title="Mean matches per entry",
        width=520,
        height=320,
        margin=dict(t=50, b=80, l=56, r=28),
    )
    return fig.to_html(full_html=False, include_plotlyjs="cdn")


def bar_chart_html(df: pd.DataFrame, title: str = "Theme frequency") -> str:
    if df is None or df.empty:
        fig = go.Figure().add_annotation(text="No theme data", showarrow=False)
    else:
        fig = px.bar(df, x="theme", y="count", title=title)
        fig.update_traces(marker_color="#DD4633")
        fig.update_layout(margin=dict(t=40, b=80, l=60, r=40), xaxis_tickangle=-35)
    return fig.to_html(full_html=False, include_plotlyjs="cdn")
