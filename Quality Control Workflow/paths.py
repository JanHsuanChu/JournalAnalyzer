# paths.py
# Resolve JournalAnalyzer package root so imports match the Shiny app.

from __future__ import annotations

import sys
from pathlib import Path

QC_ROOT = Path(__file__).resolve().parent
JA_ROOT = QC_ROOT.parent


def uniquify_output_path(path: Path) -> Path:
    """
    Return a path that does not name an existing file.

    If *path* is unused, return it (resolved; parent directories are created).
    Otherwise return ``{stem}_1{suffix}``, ``{stem}_2{suffix}``, … until free.
    """
    path = path.expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    parent = path.parent
    n = 1
    while True:
        candidate = (parent / f"{stem}_{n}{suffix}").resolve()
        if not candidate.exists():
            return candidate
        n += 1


def ensure_ja_on_path() -> None:
    """Allow `import report_builder`, `import ollama_client`, etc."""
    s = str(JA_ROOT)
    if s not in sys.path:
        sys.path.insert(0, s)
