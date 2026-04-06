# k10_report_html.py
# K10 table + summary fragment aligned with dsai/08_function_calling/journal_k10_workflow.render_k10_report_html.

from __future__ import annotations

import html
from typing import Any

FREQUENCY_LABELS = [
    "None of the time",
    "A little of the time",
    "Some of the time",
    "Most of the time",
    "All of the time",
]

OFFICIAL_K10_ROWS: list[tuple[str, int]] = [
    ("About how often did you feel tired out for no good reason?", 1),
    ("About how often did you feel nervous?", 2),
    ("About how often did you feel so nervous that nothing could calm you down?", 3),
    ("About how often did you feel hopeless?", 4),
    ("About how often did you feel restless or fidgety?", 5),
    ("About how often did you feel so restless you could not sit still?", 10),
    ("About how often did you feel depressed?", 9),
    ("About how often did you feel that everything was an effort?", 7),
    ("About how often did you feel so sad that nothing could cheer you up?", 6),
    ("About how often did you feel worthless?", 8),
]

K10_SCORING_EXPLAINER = (
    "Each of the K10's 10 items receives a score on a Likert scale from 1 (“None of the time”) "
    "to 5 (“All of the time”). To calculate the total K10 score, users sum all item scores, "
    "resulting in a range from 10 to 50. Higher scores clearly indicate greater distress. "
    "For interpretation, professionals often use conventional bands: "
    "10–15: low distress; 16–21: moderate distress; 22–29: high distress; 30–50: very high distress."
)


def _items_by_index(payload: dict[str, Any]) -> dict[int, dict]:
    out: dict[int, dict] = {}
    for row in payload.get("items") or []:
        try:
            idx = int(row["item_index"])
        except (KeyError, TypeError, ValueError):
            continue
        out[idx] = row
    return out


def _item_likert_1_to_5(item: dict) -> int:
    if item.get("score_1_to_5") is not None:
        try:
            return max(1, min(5, int(item["score_1_to_5"])))
        except (TypeError, ValueError):
            pass
    if item.get("score_0_to_4") is not None:
        try:
            return max(1, min(5, int(item["score_0_to_4"]) + 1))
        except (TypeError, ValueError):
            pass
    return -1


def render_k10_section_fragment(
    k10_payload: dict[str, Any],
    narrative_summary: str,
    domain_highlights: str | None = None,
) -> str:
    """
    HTML fragment for embedding under <h2>K10 Summary</h2> (no outer <html>).
    narrative_summary: plain text or paragraphs separated by blank lines (escaped).
    domain_highlights: optional compact grouped summary (plain text).
    """
    by_idx = _items_by_index(k10_payload)
    total = k10_payload.get("total_score", "")
    sev_label = html.escape(str(k10_payload.get("severity_label", "")))
    disclaimer = html.escape(str(k10_payload.get("disclaimer", "")))
    safety = k10_payload.get("safety_flags") or {}
    safety_any = bool(safety.get("any"))
    ds = k10_payload.get("data_source") or {}
    n_ent = ds.get("entry_count")
    p0, p1 = ds.get("period_start"), ds.get("period_end")
    if p0 and p1:
        period_line = f"Diary entries in this report: <strong>{n_ent}</strong> entries, "
        period_line += f"dated from <strong>{html.escape(str(p0))}</strong> to <strong>{html.escape(str(p1))}</strong>."
    else:
        period_line = f"Diary entries in this report: <strong>{n_ent if n_ent is not None else '—'}</strong>."
    if ds.get("recent_days"):
        period_line += f" (K10 window: last <strong>{ds['recent_days']}</strong> calendar days of the analysis range.)"

    body_rows: list[str] = []
    for i, (qtext, item_idx) in enumerate(OFFICIAL_K10_ROWS, start=1):
        item = by_idx.get(item_idx, {})
        s = _item_likert_1_to_5(item)
        raw_ev = str(item.get("evidence") or "").strip()
        if s == 1:
            ev_inner = "" if not raw_ev else html.escape(raw_ev)
        else:
            ev_inner = html.escape(raw_ev if raw_ev else "—")
        ev_cell = f'<td class="evcell">{ev_inner}</td>'
        freq_cells = []
        for v in range(1, 6):
            selected = s == v
            cls = "freq-cell selected" if selected else "freq-cell"
            mark = "●" if selected else ""
            freq_cells.append(
                f'<td class="{cls}" title="{html.escape(FREQUENCY_LABELS[v - 1])}">{mark}</td>'
            )
        row_cls = "row-alt" if i % 2 == 0 else ""
        body_rows.append(
            f'<tr class="{row_cls}">'
            f'<td class="qcell">{html.escape(qtext)}</td>'
            f'{"".join(freq_cells)}'
            f"{ev_cell}"
            f"</tr>"
        )

    header_freq = []
    for v in range(1, 6):
        lab = FREQUENCY_LABELS[v - 1]
        header_freq.append(
            f'<th scope="col" class="likert-hdr"><span class="likert-num">{v}</span><br/>'
            f'<span class="likert-lab">{html.escape(lab)}</span></th>'
        )
    header_freq_s = "".join(header_freq)

    summary_paras = []
    for block in (narrative_summary or "").strip().split("\n\n"):
        t = block.strip()
        if t:
            summary_paras.append(f"<p>{html.escape(t)}</p>")

    safety_banner = ""
    if safety_any:
        safety_banner = (
            '<div class="k10-safety-banner">If you are in crisis, contact local emergency services or a crisis line.</div>'
        )

    summary_inner = "".join(summary_paras) if summary_paras else "<p><em>No summary text.</em></p>"

    domain_block = ""
    dh = (domain_highlights or "").strip()
    if dh:
        domain_block = (
            '<h3 class="k10-domain-heading">Domain highlights</h3>'
            f'<p class="k10-domain-text">{html.escape(dh)}</p>'
        )

    return f"""<div class="k10-section-inner">
  <h3 class="k10-title">Kessler Psychological Distress Scale (K10) — journal-informed estimate</h3>
  <p class="k10-sub">Proxy scores inferred from diary text (not a self-administered K10).
  <strong>Total score: {html.escape(str(total))}</strong> / 50
  · <strong>{sev_label}</strong></p>
  <p class="k10-period">{period_line}</p>
  <p class="k10-scale">{html.escape(K10_SCORING_EXPLAINER)}</p>
  {safety_banner}
  {domain_block}
  <h3 class="k10-items-heading">Items</h3>
  <table class="k10-table">
    <thead>
      <tr>
        <th scope="col">Question</th>
        {header_freq_s}
        <th scope="col">Evidence / rationale<br/><span class="ev-hint">(blank only when score is 1)</span></th>
      </tr>
    </thead>
    <tbody>
      {"".join(body_rows)}
    </tbody>
  </table>
  <h3 class="k10-summary-heading">Summary</h3>
  <div class="k10-summary">
    {summary_inner}
  </div>
  <p class="k10-disclaimer">{disclaimer}</p>
</div>"""


# CSS rules for k10-section-inner (merge into main report stylesheet)
K10_SECTION_CSS = """
    .k10-section-inner { margin-top: 0.5rem; }
    .k10-title { font-size: 1.15rem; margin-bottom: 0.25rem; color: #DD4633; }
    .k10-sub { color: #555; font-size: 0.95rem; margin-bottom: 1rem; }
    .k10-period { font-size: 0.95rem; margin-bottom: 0.75rem; }
    .k10-scale { font-size: 0.88rem; color: #444; margin-bottom: 1.25rem; max-width: 52rem; }
    .k10-items-heading, .k10-summary-heading, .k10-domain-heading { font-size: 1.05rem; margin-top: 1.25rem; margin-bottom: 0.5rem; color: #DD4633; }
    .k10-domain-text { font-size: 0.9rem; color: #444; max-width: 52rem; white-space: pre-wrap; }
    .k10-table {
      width: 100%;
      border-collapse: collapse;
      font-size: 0.9rem;
      box-shadow: 0 1px 3px rgba(221, 70, 51, 0.12);
      background-color: white;
    }
    .k10-table th, .k10-table td {
      border: 1px solid #ddd;
      padding: 0.5rem 0.6rem;
      vertical-align: top;
    }
    .k10-table thead th {
      background: #DD4633;
      color: #fff;
      font-weight: 600;
      text-align: center;
      border-color: #c73d2e;
    }
    .k10-table thead th:first-child {
      text-align: left;
      min-width: 14rem;
    }
    .likert-hdr { line-height: 1.25; }
    .likert-num { font-size: 1.15rem; font-weight: 700; color: #fff; }
    .likert-lab { font-size: 0.72rem; font-weight: 500; display: inline-block; max-width: 7rem; color: rgba(255, 255, 255, 0.95); }
    tr.row-alt td { background: #FFF8F6; }
    .qcell { font-weight: 500; }
    .freq-cell {
      text-align: center;
      width: 5.5rem;
      color: #888;
    }
    .freq-cell.selected {
      background: #FEECEA;
      color: #DD4633;
      font-weight: bold;
      border-color: #E8A090;
    }
    .evcell {
      font-size: 0.85rem;
      color: #333;
      min-width: 12rem;
      max-width: 22rem;
    }
    .ev-hint { font-size: 0.72rem; font-weight: 500; color: rgba(255, 255, 255, 0.88); }
    .k10-summary p { margin: 0.5rem 0; }
    .k10-disclaimer { font-size: 0.85rem; color: #666; margin-top: 1rem; }
    .k10-safety-banner {
      background: #fff8e6;
      border: 1px solid #e6d395;
      padding: 0.75rem 1rem;
      margin-bottom: 1rem;
      font-size: 0.9rem;
    }
    .report-data-source { font-size: 0.95rem; margin-bottom: 1rem; color: #444; }
"""
