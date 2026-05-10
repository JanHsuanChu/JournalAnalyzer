# gi_qc_grader.py
# Ollama JSON grader for General Insights QC (LLM rubric).

from __future__ import annotations

import json
import os
import re
from typing import Any

import requests

from paths import ensure_ja_on_path

ensure_ja_on_path()

from gi_qc_prompt import GI_QC_SYSTEM, build_gi_qc_prompt  # noqa: E402
from ollama_client import get_chat_url  # noqa: E402


DEFAULT_GI_GRADER_MODEL = "gpt-oss:20b-cloud"


def _headers() -> dict[str, str]:
    key = os.environ.get("OLLAMA_API_KEY", "").strip()
    h: dict[str, str] = {"Content-Type": "application/json"}
    if key:
        h["Authorization"] = f"Bearer {key}"
    return h


def chat_completion_json(
    messages: list[dict[str, str]],
    model: str,
    timeout: int = 180,
) -> dict:
    body: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "stream": False,
        "format": "json",
    }
    r = requests.post(get_chat_url(), headers=_headers(), json=body, timeout=timeout)
    r.raise_for_status()
    return r.json()


def _clamp15(x: Any) -> int | None:
    if x is None:
        return None
    try:
        return max(1, min(5, int(x)))
    except (TypeError, ValueError):
        return None


def parse_gi_qc_json(text: str) -> dict[str, Any] | None:
    if not text:
        return None
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        text = m.group(0)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None

    qr = data.get("question_relevance_1_5")
    if qr is not None:
        try:
            qr = max(1, min(5, int(qr)))
        except (TypeError, ValueError):
            return None

    tr_i = _clamp15(data.get("trend_relevance_1_5"))
    dr_i = _clamp15(data.get("direction_1_5"))
    fh = _clamp15(data.get("formatting_hygiene_1_5"))
    cs = _clamp15(data.get("clinical_safety_1_5"))
    ic = _clamp15(data.get("internal_coherence_1_5"))
    if tr_i is None or dr_i is None or fh is None or cs is None or ic is None:
        return None

    hall = data.get("gi_hallucination")
    if isinstance(hall, str):
        hall = hall.strip().lower() in ("true", "1", "yes")
    else:
        hall = bool(hall)

    det = data.get("gi_details")
    if det is not None:
        det = str(det)

    return {
        "question_relevance_1_5": qr,
        "trend_relevance_1_5": tr_i,
        "direction_1_5": dr_i,
        "gi_hallucination": hall,
        "formatting_hygiene_1_5": fh,
        "clinical_safety_1_5": cs,
        "internal_coherence_1_5": ic,
        "gi_details": det or "",
    }


def run_gi_qc_grader(
    journal_excerpt: str,
    gi_plain_text: str,
    *,
    configured_user_question: str,
    configured_trend_keywords: list[str],
    model: str | None = None,
    timeout: int = 180,
) -> tuple[str | None, dict[str, Any] | None]:
    """
    Returns (raw_json_string, parsed_dict) or (None, None) on failure.
    """
    m = model or os.environ.get("OLLAMA_MODEL_GI_QC_GRADER", DEFAULT_GI_GRADER_MODEL)
    kw_csv = ", ".join(str(x).strip() for x in (configured_trend_keywords or []) if str(x).strip())
    user = build_gi_qc_prompt(
        journal_excerpt,
        gi_plain_text,
        configured_user_question=configured_user_question or "",
        configured_trend_keywords_csv=kw_csv,
    )
    messages = [
        {"role": "system", "content": GI_QC_SYSTEM},
        {"role": "user", "content": user},
    ]
    try:
        data = chat_completion_json(messages, model=m, timeout=timeout)
        content = (data.get("message") or {}).get("content") or ""
        raw = content.strip()
        parsed = parse_gi_qc_json(raw)
        return raw, parsed
    except Exception:
        return None, None
