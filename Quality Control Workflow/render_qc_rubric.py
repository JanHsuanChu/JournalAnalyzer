# render_qc_rubric.py
# One-page PNG rubric summary for homework screenshots (“visual rubric”).
#
# Text is abbreviated from qc_prompt.K10_QC_SYSTEM — update both if scoring rules change.
#
# Typical use (from JournalAnalyzer/):
#   python "Quality Control Workflow/render_qc_rubric.py" \
#     --out "Quality Control Workflow/plots/qc_rubric_summary.png"

from __future__ import annotations

import argparse
import os
import textwrap
from pathlib import Path

import matplotlib.pyplot as plt

from paths import QC_ROOT

DEFAULT_OUT = QC_ROOT / "plots" / "qc_rubric_summary.png"

# Condensed bullets (keep aligned with qc_prompt.K10_QC_SYSTEM).
ROWS: list[list[str]] = [
    [
        "Frequency rubric\n(not severity)",
        "Per K-10 item\n(×10 stems)",
        "Likert integer\n**1–5**",
        (
            "**High (4–5):** score/rationale matches how often the theme appears across entries/days. "
            "**Low (1–2):** driven by vivid single lines without diary-wide recurrence support."
        ),
    ],
    [
        "Evidence relevance",
        "Per K-10 item\n(×10 stems)",
        "Likert integer\n**1–5**",
        (
            "**High (4–5):** evidence/rationale fits that item’s stem. "
            "**Low (1–2):** off-topic, confused with another item, or too generic."
        ),
    ],
    [
        "Hallucination",
        "Whole report\n(one flag)",
        "JSON **boolean**",
        "**true** if assertions contradict or are unsupported by supplied journal text; else **false**.",
    ],
    [
        "Details",
        "Whole report",
        "Plain text\n(≤ ~80 words in schema)",
        "Short narrative summarizing strengths / issues (reviewer rationale).",
    ],
    [
        "Means (validated)",
        "Derived in\nparse step",
        "2 decimal places",
        "Reviewer may echo means; qc_grader **recomputes** from the 10 per-item integers.",
    ],
]


def _wrap(cell: str, width: int) -> str:
    """Wrap prose for table cells; keep ** markdown out of mpl if desired — strip minimal."""
    t = cell.replace("**", "")
    return "\n".join(textwrap.wrap(t, width=width))


def main() -> None:
    os.environ.setdefault("MPLCONFIGDIR", str(QC_ROOT / ".mplconfig"))

    ap = argparse.ArgumentParser(description="Render QC rubric summary PNG for screenshots")
    ap.add_argument(
        "--out",
        type=Path,
        default=DEFAULT_OUT,
        help="Output PNG path",
    )
    args = ap.parse_args()

    col_labels = ["Dimension", "Granularity", "Scale / type", "Scoring anchors (abbrev.)"]

    wrapped_rows: list[list[str]] = []
    for r in ROWS:
        wrapped_rows.append(
            [_wrap(r[0], 18), _wrap(r[1], 16), _wrap(r[2], 14), _wrap(r[3], 52)]
        )

    nrows = len(wrapped_rows)
    fig_h = max(6.0, 1.0 + nrows * 1.35)
    fig, ax = plt.subplots(figsize=(13, fig_h))
    ax.axis("off")
    fig.suptitle(
        "JournalAnalyzer QC reviewer rubric\n"
        "(full prompt text: qc_prompt.py → K10_QC_SYSTEM; output shape enforced in qc_grader.parse_qc_json)",
        fontsize=11,
        fontweight="bold",
        color="#333333",
    )

    table = ax.table(
        cellText=wrapped_rows,
        colLabels=col_labels,
        cellLoc="left",
        loc="upper center",
        colColours=["#FEECEA"] * len(col_labels),
    )
    table.auto_set_font_size(False)
    table.set_fontsize(9)
    for (row, col), cell in table.get_celld().items():
        if row == 0:
            cell.set_text_props(fontweight="bold")
            cell.set_facecolor("#DD4633")
            cell.get_text().set_color("white")
            cell.set_height(0.12)
        else:
            cell.set_facecolor("#FFFAFA" if row % 2 == 1 else "#FFFFFF")
            cell.set_height(0.28)

    table.scale(1.0, 2.2)
    plt.subplots_adjust(top=0.88, bottom=0.02, left=0.03, right=0.97)

    args.out = args.out.resolve()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(args.out, dpi=150, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print(f"✅ Saved rubric figure: {args.out}")


if __name__ == "__main__":
    main()
