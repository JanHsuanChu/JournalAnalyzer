# gi_qc_deterministic.py
# General Insights slice: trend chart bar total + correlation list presence. No LLM.

from __future__ import annotations

import base64
import html as html_mod
import re
import struct
from typing import Any

from paths import ensure_ja_on_path

ensure_ja_on_path()


def _strip_html_to_plain(fragment: str) -> str:
    """Minimal tag stripper for QC (GI slice only); avoids dependency on optional helpers."""
    t = re.sub(r"<script[\s\S]*?</script>", " ", fragment or "", flags=re.IGNORECASE)
    t = re.sub(r"<style[\s\S]*?</style>", " ", t, flags=re.IGNORECASE)
    t = re.sub(r"<[^>]+>", " ", t)
    t = html_mod.unescape(t)
    return re.sub(r"\s+", " ", t).strip()


def extract_general_insights_body(full_html: str) -> str:
    """
    Fragment after <h2>General Insights</h2> up to theme chart, K10 <hr>, or </body>.
    Matches assembly order in agents/agent3_merge.build_agent3_report.
    """
    m = re.search(
        r"<h2>\s*General\s+Insights\s*</h2>\s*(.*?)(?=<h3>\s*Theme\s+frequency\s*</h3>|<hr\s*/>|</body>)",
        full_html or "",
        re.DOTALL | re.IGNORECASE,
    )
    return (m.group(1) or "").strip() if m else ""


def _search_phrase_for_keyword(kw: str) -> str:
    p = (kw or "").strip()
    low = p.lower()
    if low.startswith("no ") and len(low) > 3:
        return p[3:].strip()
    return p


def _extract_trend_chart_inner(fragment: str) -> str | None:
    """Inner HTML inside the outer <div class=\"trend-chart-wrap\">…</div> (balanced)."""
    open_tag = '<div class="trend-chart-wrap">'
    i = fragment.find(open_tag)
    if i < 0:
        return None
    j = i + len(open_tag)
    depth = 1
    k = j
    low = fragment.lower()
    while k < len(fragment) and depth > 0:
        no = low.find("<div", k)
        nc = low.find("</div>", k)
        if nc < 0:
            return None
        if no >= 0 and no < nc:
            depth += 1
            k = no + 4
        else:
            depth -= 1
            if depth == 0:
                return fragment[j:nc]
            k = nc + len("</div>")
    return None


def _decode_plotly_bdata_values(dtype: str, b64: str) -> list[float] | None:
    """
    Decode Plotly.js compact array encoding {\"dtype\":\"...\",\"bdata\":\"...\"}.
    Multi-byte dtypes use big-endian (Plotly JSON convention).
    """
    dtype_key = (dtype or "").lower().strip()
    if not b64 or not isinstance(b64, str):
        return None
    try:
        raw = base64.b64decode(b64, validate=False)
    except Exception:
        return None
    if not raw:
        return None
    try:
        if dtype_key == "i1":
            return [float(x) for x in struct.unpack(f"{len(raw)}b", raw)]
        if dtype_key == "u1":
            return [float(x) for x in struct.unpack(f"{len(raw)}B", raw)]
        if dtype_key == "i2":
            n = len(raw) // 2
            if n * 2 != len(raw):
                return None
            return [float(x) for x in struct.unpack(f">{n}h", raw)]
        if dtype_key == "u2":
            n = len(raw) // 2
            if n * 2 != len(raw):
                return None
            return [float(x) for x in struct.unpack(f">{n}H", raw)]
        if dtype_key == "i4":
            n = len(raw) // 4
            if n * 4 != len(raw):
                return None
            return [float(x) for x in struct.unpack(f">{n}i", raw)]
        if dtype_key == "f4":
            n = len(raw) // 4
            if n * 4 != len(raw):
                return None
            return [float(x) for x in struct.unpack(f">{n}f", raw)]
        if dtype_key == "f8":
            n = len(raw) // 8
            if n * 8 != len(raw):
                return None
            return [float(x) for x in struct.unpack(f">{n}d", raw)]
    except struct.error:
        return None
    return None


def _sum_plotly_y_arrays(chart_inner: str) -> int | None:
    """Sum numeric bar heights from Plotly JSON embedded in chart HTML (px.bar uses key \"y\")."""
    if not chart_inner or not chart_inner.strip():
        return None
    low = chart_inner.lower()
    if "no mentions" in low and "<p>" in low:
        return 0
    total = 0.0
    found = False
    for m in re.finditer(r'"y"\s*:\s*\[([^\]]*)\]', chart_inner):
        found = True
        chunk = m.group(1)
        for num in re.findall(r"-?\d+(?:\.\d+)?", chunk):
            total += float(num)
    if not found:
        # Plotly 3.x often serializes bar heights as binary {\"dtype\":\"i1\",\"bdata\":\"...\"}
        for m in re.finditer(r'"y"\s*:\s*\{([^{}]+)\}', chart_inner):
            inner = m.group(1)
            if '"dtype"' not in inner or '"bdata"' not in inner:
                continue
            dm = re.search(r'"dtype"\s*:\s*"([^"]+)"', inner)
            bm = re.search(r'"bdata"\s*:\s*"([^"]*)"', inner)
            if not dm or not bm:
                continue
            vals = _decode_plotly_bdata_values(dm.group(1), bm.group(1))
            if vals is None:
                continue
            found = True
            total += sum(vals)
    if not found:
        return None
    return int(round(total))


def _iter_trend_blocks(gi_body: str) -> list[tuple[str, str, str]]:
    """Each tuple: (label, prose_inner_html after <p>, raw tail after </p> for chart lookup)."""
    out: list[tuple[str, str, str]] = []
    for m in re.finditer(r"<h4>\s*Trend:\s*([^<]+)</h4>", gi_body, re.IGNORECASE):
        label = html_mod.unescape(m.group(1).strip())
        tail = gi_body[m.end() :]
        pm = re.match(r"\s*<p>(.*?)</p>", tail, re.DOTALL | re.IGNORECASE)
        prose_inner = pm.group(1) if pm else ""
        after_p = tail[pm.end() :] if pm else tail
        out.append((label, prose_inner, after_p))
    return out


def _first_trend_block_for_keywords(
    gi_body: str, trend_keywords: list[str]
) -> tuple[str, str, str] | None:
    blocks = _iter_trend_blocks(gi_body)
    if not blocks:
        return None
    if not trend_keywords:
        return blocks[0]
    want = {_search_phrase_for_keyword(k).lower() for k in trend_keywords if (k or "").strip()}
    for lab, prose, after in blocks:
        lab_l = lab.lower()
        sp = _search_phrase_for_keyword(lab).lower()
        if lab_l in want or sp in want:
            return (lab, prose, after)
        for k in trend_keywords:
            if k and k.strip().lower() == lab_l:
                return (lab, prose, after)
    return blocks[0]


def _slice_between(haystack: str, start_h3: str, end_markers: tuple[str, ...]) -> str:
    """Slice after start tag until the first of end_markers, or end of haystack."""
    alts = [re.escape(e) for e in end_markers if e]
    end_re = "(?=" + "|".join(alts + [r"\Z"]) + ")"
    m = re.search(
        re.escape(start_h3) + r"(.*?)" + end_re,
        haystack,
        re.DOTALL | re.IGNORECASE,
    )
    return (m.group(1) or "").strip() if m else ""


def run_gi_deterministic_qc(
    full_html: str,
    *,
    trend_keywords: list[str],
) -> dict[str, Any]:
    """
    Two deterministic fields for the GI HTML slice:
    - trend_keyword_total_count: sum of Plotly bar y-values in the trend chart for the block
      that matches configured trend_keywords; empty string if the slice or chart is missing.
    - correlation_analysis_present: substantive tool-backed list rows under Correlation analysis.
    """
    gi_body = extract_general_insights_body(full_html)
    out: dict[str, Any] = {
        "trend_keyword_total_count": "",
        "correlation_analysis_present": False,
    }
    if not gi_body:
        return out

    tb = _first_trend_block_for_keywords(gi_body, list(trend_keywords or []))
    chart_sum: int | None = None
    if tb:
        _lab, _prose_inner, after_p = tb
        inner = _extract_trend_chart_inner(after_p)
        if inner is not None:
            chart_sum = _sum_plotly_y_arrays(inner)

    out["trend_keyword_total_count"] = chart_sum if chart_sum is not None else ""

    corr_block = _slice_between(
        gi_body,
        "<h4>Correlation analysis</h4>",
        ("<h3>Suggested next steps</h3>", "<h3>"),
    )
    lis = re.findall(r"<li[\s>][\s\S]*?</li>", corr_block, re.IGNORECASE)
    substantive = 0
    for li in lis:
        t = _strip_html_to_plain(li)
        if len(t) > 12 and ("r=" in li.lower() or " vs " in t.lower()):
            substantive += 1
    out["correlation_analysis_present"] = substantive > 0

    return out
