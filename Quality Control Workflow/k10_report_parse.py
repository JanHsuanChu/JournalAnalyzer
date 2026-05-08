# k10_report_parse.py
# Deterministic K-10 total from saved report HTML (no LLM).

from __future__ import annotations

import html as html_module
import re
from html.parser import HTMLParser


class _HTMLToText(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._chunks: list[str] = []

    def handle_data(self, data: str) -> None:
        self._chunks.append(data)

    def get_text(self) -> str:
        return "".join(self._chunks)


def strip_html_to_text(html: str) -> str:
    """Strip tags; unescape entities. Good enough for QC prompts."""
    parser = _HTMLToText()
    try:
        parser.feed(html)
        parser.close()
        raw = parser.get_text()
    except Exception:
        raw = re.sub(r"<[^>]+>", " ", html)
    return html_module.unescape(re.sub(r"\s+", " ", raw).strip())


def parse_k10_total_from_html(html: str) -> int | None:
    """
    Read total from K10 section: `<strong>Total score: NN</strong> / 50`
    (see k10_report_html.render_k10_section_fragment).
    """
    m = re.search(
        r"Total score:\s*(\d+)\s*</strong>\s*/\s*50",
        html,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if not m:
        m = re.search(r"Total score:\s*(\d+)", html, flags=re.IGNORECASE)
    if not m:
        return None
    try:
        v = int(m.group(1))
        if 10 <= v <= 50:
            return v
        return v
    except ValueError:
        return None
