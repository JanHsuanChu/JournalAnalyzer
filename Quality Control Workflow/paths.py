# paths.py
# Resolve JournalAnalyzer package root so imports match the Shiny app.

from __future__ import annotations

import sys
from pathlib import Path

QC_ROOT = Path(__file__).resolve().parent
JA_ROOT = QC_ROOT.parent


def ensure_ja_on_path() -> None:
    """Allow `import report_builder`, `import ollama_client`, etc."""
    s = str(JA_ROOT)
    if s not in sys.path:
        sys.path.insert(0, s)
