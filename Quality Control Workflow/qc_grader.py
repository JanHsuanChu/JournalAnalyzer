# qc_grader.py
# Call Ollama Cloud grader with JSON output; parse and validate QC fields.

from __future__ import annotations

import json
import os
import re
from typing import Any

import requests

from paths import ensure_ja_on_path

ensure_ja_on_path()

from ollama_client import get_chat_url  # noqa: E402


def _headers() -> dict[str, str]:
    key = os.environ.get("OLLAMA_API_KEY", "").strip()
    h: dict[str, str] = {"Content-Type": "application/json"}
    if key:
        h["Authorization"] = f"Bearer {key}"
    return h

from qc_prompt import K10_QC_SYSTEM, build_k10_qc_prompt  # noqa: E402


DEFAULT_GRADER_MODEL = "gpt-oss:20b-cloud"


def chat_completion_json(
    messages: list[dict[str, str]],
    model: str,
    timeout: int = 180,
) -> dict:
    """POST /api/chat with format=json (Ollama)."""
    body: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "stream": False,
        "format": "json",
    }
    r = requests.post(get_chat_url(), headers=_headers(), json=body, timeout=timeout)
    r.raise_for_status()
    return r.json()


def run_k10_qc_grader(
    journal_text: str,
    report_plain_text: str,
    *,
    rag_retrieval_context: str | None = None,
    model: str | None = None,
    timeout: int = 180,
) -> tuple[str | None, dict[str, Any] | None]:
    """
    Returns (raw_json_string, parsed_dict) or (None, None) on failure.
    """
    m = model or os.environ.get("OLLAMA_MODEL_QC_GRADER", DEFAULT_GRADER_MODEL)
    user = build_k10_qc_prompt(
        journal_text,
        report_plain_text,
        rag_retrieval_context=rag_retrieval_context,
    )
    messages = [
        {"role": "system", "content": K10_QC_SYSTEM},
        {"role": "user", "content": user},
    ]
    try:
        data = chat_completion_json(messages, model=m, timeout=timeout)
        content = (data.get("message") or {}).get("content") or ""
        raw = content.strip()
        parsed = parse_qc_json(raw)
        return raw, parsed
    except Exception:
        return None, None


def parse_qc_json(text: str) -> dict[str, Any] | None:
    """Extract JSON object; validate and recompute means."""
    if not text:
        return None
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        text = m.group(0)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None

    fr = data.get("frequency_rubric_per_item")
    er = data.get("evidence_relevance_per_item")
    if not isinstance(fr, list) or not isinstance(er, list):
        return None
    if len(fr) != 10 or len(er) != 10:
        return None
    try:
        fr_i = [max(1, min(5, int(x))) for x in fr]
        er_i = [max(1, min(5, int(x))) for x in er]
    except (TypeError, ValueError):
        return None

    fr_mean = round(sum(fr_i) / 10.0, 2)
    er_mean = round(sum(er_i) / 10.0, 2)
    hall = data.get("hallucination")
    if isinstance(hall, str):
        hall = hall.strip().lower() in ("true", "1", "yes")
    else:
        hall = bool(hall)

    details = data.get("details")
    if details is not None:
        details = str(details)

    return {
        "frequency_rubric_per_item": fr_i,
        "frequency_rubric_mean": fr_mean,
        "evidence_relevance_per_item": er_i,
        "evidence_relevance_mean": er_mean,
        "hallucination": hall,
        "details": details or "",
    }
