# agents/agent1_k10.py
# Agent 1: single tool call for K10 (Nemotron / Ollama).

from __future__ import annotations

import json
import os

import pandas as pd

from context_builder import (
    K10_PER_ITEM_RAG_MARKER,
    build_k10_corpus_note,
    build_k10_structured_full_diary_prompt,
    slice_last_n_calendar_days,
)
from k10_utils import tool_estimate_k10_from_journal_for_blob
from ollama_client import chat_completion

TOOL_ESTIMATE_K10 = {
    "type": "function",
    "function": {
        "name": "estimate_k10_from_journal",
        "description": (
            "Record a K10 psychological distress profile from diary evidence. "
            "Scoring is **per item** (10 independent items): each item has its own retrieval query (when RAG is used), "
            "its own evidence, its own Likert 1–5, and its own application of the system-prompt frequency rubric—do not "
            "mix items. "
            "Infer how often **that item's symptom theme** appears in the text allowed for that item only, across the "
            "K10 window (~last 30 calendar days), using the frequency rubric (day/entry patterns for scores 1–5), "
            "combined with **severity** (intensity of distress in language when that theme appears). "
            "Sum of items is 10–50. For each item, provide brief evidence grounded in that item's allowed text only. "
            "Do not use a bare numeral, Likert digit, or scale label alone as evidence. "
            "Use an empty string only when the score is 1 (none of the time)."
        ),
        "parameters": {
            "type": "object",
            "required": ["item_scores", "item_evidence", "journal_text"],
            "properties": {
                "item_scores": {
                    "type": "array",
                    "items": {"type": "integer", "minimum": 1, "maximum": 5},
                    "minItems": 10,
                    "maxItems": 10,
                    "description": (
                        "Ten integers 1–5 (Likert), K10 item order. Each score applies only to that item's theme, using "
                        "the per-item frequency rubric on that item's allowed evidence. Orchestrator recomputes total/severity."
                    ),
                },
                "item_evidence": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 10,
                    "maxItems": 10,
                    "description": (
                        "Per item k only: narrative support taken exclusively from the text permitted for item k "
                        "(## Item k for per-item RAG—passages from that item's query only; or journal lines relevant to stem k). "
                        "Do not cite passages retrieved or written for another item. "
                        "Empty string only when the corresponding item score is 1. "
                        "Never use only a number (e.g. 3) or only a frequency label as evidence."
                    ),
                },
                "journal_text": {
                    "type": "string",
                    "description": "Full diary text used for scoring (same as user message body).",
                },
            },
        },
    },
}

AGENT1_SYSTEM = """You analyze diary entries and produce a structured Kessler K10 assessment via a tool call.

**Per-item rule (required):** The K10 has **10 separate items**. Everything is scored **per item**, independently:
- **Retrieval / corpus:** When the diary has **## Item 1 … Item 10** sections, section k contains text retrieved **only** for item k's official question (one RAG query per item). You must not use Item j's passages to score or justify Item k.
- **Evidence:** `item_evidence[k]` must reflect **only** item k's allowed text (that section, or the journal lines you deem relevant to stem k when the full diary is shown).
- **Frequency rubric:** Apply the table below **separately for each item**. For item k, count **days and entries where that item's symptom theme** appears in the text you are allowed to use for k—not other themes and not the diary overall. A high score on item 4 does not imply anything about item 7; judge each row on its own.

Rules:

- The user message contains journal text from a **limited date window** (typically the **last 30 days** of entries).
- For each K10 item, combine **frequency** (mapped to the rubric below using **only** that item's allowed evidence) and **severity** (how strong or distressing the language is **for that item's theme** when it appears) to choose a **Likert 1–5**. The ten item scores sum to **10–50** (standard K10).

**Frequency rubric (apply independently to each item k, within the K10 window ~30 calendar days):** For **that item's theme only**, use these bands—by **days with relevant mentions** and/or **how many entries** discuss **this item's theme** in your allowed text for k, not keyword counts alone. If the window has fewer entries than a full month, interpret proportionally.

| Score | Label | Guide (for **this item's theme** in the text allowed for this item) |
| ----- | ----- | ----- |
| 1 | None of the time | 0 days, or zero mentions across entries for **this** theme. |
| 2 | A little of the time | 1–3 days with mentions / appears in about one entry, not dwelt on. |
| 3 | Some of the time | 4–8 days / mentioned in 2–3 entries or briefly recurring. |
| 4 | Most of the time | 9–20 days / mentioned in 4+ entries or a dominant thread. |
| 5 | All of the time | 21–28+ days / present in nearly every entry, pervasive tone (for **this** theme). |

- **Severity** adjusts within a band when language is unusually mild or intense; stay anchored to the frequency guide above **for that item**.
- When the diary is split into **## Item k** sections (per-item retrieval), use **only** section k to justify `item_scores[k]`, `item_evidence[k]`, **and** your frequency judgment for item k—do not use Item j text for Item k.
- When the diary is a **full journal** with listed stems, map each item to the parts of the journal relevant to **that stem only**, and apply the rubric to **that** theme's presence in those parts.
- For each item, supply a **short evidence string** in **natural language** grounded in the permitted text (paraphrase or a few quoted words). Use an **empty string only** when the score for that item is **1** (none of the time). For scores 2–5, evidence must be **narrative**, not a **bare numeral**, **not only a Likert score**, and **not only a scale label** (e.g. do not write \"3\" or \"Most of the time\" alone).
- Be conservative when evidence is weak or sparse.
- Do not assume symptoms without textual support.
- Do not diagnose any condition.

Output:

- You MUST call the tool `estimate_k10_from_journal` exactly once.
- Do not produce any text outside the tool call."""


def _user_task(corpus_note: str, diary_blob: str) -> str:
    return (
        f"{corpus_note}\n\n"
        "Below is the journal corpus. Call the tool `estimate_k10_from_journal` exactly once. "
        "Pass item_scores (10 integers 1–5, each scored per item per system rules), item_evidence (10 strings—one per item, "
        "grounded only in that item's allowed text; empty only if score is 1; no numeric-only evidence), "
        "and journal_text set to the full diary text in the ---DIARY--- block.\n\n"
        "---DIARY---\n"
        f"{diary_blob}\n"
        "---END DIARY---"
    )


def _execute_tool_calls(tool_calls: list, registry: dict) -> dict | None:
    for tc in tool_calls:
        fn = tc.get("function") or {}
        name = fn.get("name", "")
        raw_args = fn.get("arguments", {})
        if isinstance(raw_args, str):
            try:
                args = json.loads(raw_args) if raw_args.strip() else {}
            except json.JSONDecodeError:
                args = {}
        else:
            args = raw_args or {}
        if name == "estimate_k10_from_journal":
            func = registry.get(name)
            if func:
                return func(**args)
    return None


def run_agent1_k10(
    df_analysis: pd.DataFrame,
    model: str,
    rag_diary_blob: str | None = None,
) -> dict | None:
    """
    Slice last 30 days, run one tool round. Returns k10_proxy_v2 dict or None.
    If rag_diary_blob is set, use it as the diary corpus (semantic retrieval); else full entries.
    """
    df_k10 = slice_last_n_calendar_days(df_analysis, 30)
    if df_k10.empty:
        return None
    budget = int(os.environ.get("RAG_K10_CHAR_BUDGET", "12000"))
    use_rag = bool(rag_diary_blob and str(rag_diary_blob).strip())
    if use_rag:
        diary_blob = str(rag_diary_blob).strip()
        corpus_note = build_k10_corpus_note(df_k10)
        if K10_PER_ITEM_RAG_MARKER in diary_blob:
            corpus_note += (
                "\n\nThe ---DIARY--- block is organized by K10 item: each ## Item k section contains "
                "passages retrieved **only** for that item's RAG query (one query per item). For item k, set "
                "item_scores[k] and item_evidence[k] using **only** the text in that section. Apply the system "
                "frequency rubric **per item**: count days/entries for **item k's theme** in section k only—do not "
                "borrow other sections. Combine **frequency** and **severity** to choose Likert 1–5 for each item independently."
            )
        else:
            corpus_note += (
                "\n\nThe ---DIARY--- block below is semantically retrieved excerpts (not the full journal), not split "
                "by item. Still score **each** K10 item independently: infer frequency and severity **per item** from "
                "passages relevant to that item's stem only; apply the frequency rubric separately for each item; do "
                "not let one item's theme dictate another's score."
            )
    else:
        diary_blob = build_k10_structured_full_diary_prompt(df_k10, char_budget=budget)
        corpus_note = (
            build_k10_corpus_note(df_k10)
            + "\n\nThe ---DIARY--- block is the full K10 window plus listed stems (no RAG). For each item k, "
            "use only journal content relevant to stem k; item_evidence[k] must be grounded there. Apply the system "
            "frequency rubric **per item**: count days/entries for **that item's theme** in the parts of the journal "
            "relevant to k. Combine **frequency** and **severity** for each item independently (1–5)."
        )
    attempts: list[tuple[str, str]] = [(corpus_note, diary_blob)]
    for lim in (8000, 5000, 3000, 2000):
        if len(diary_blob) > lim:
            attempts.append(
                (
                    f"{corpus_note} (Diary truncated to {lim} characters for tool reliability.)",
                    diary_blob[:lim],
                )
            )
    for note, blob in attempts:
        user_task = _user_task(note, blob)
        registry = {"estimate_k10_from_journal": tool_estimate_k10_from_journal_for_blob(blob)}
        messages = [
            {"role": "system", "content": AGENT1_SYSTEM},
            {"role": "user", "content": user_task},
        ]
        try:
            data = chat_completion(messages, model=model, tools=[TOOL_ESTIMATE_K10], timeout=180)
        except Exception:
            continue
        msg = data.get("message") or {}
        tool_calls = msg.get("tool_calls")
        if not tool_calls:
            continue
        payload = _execute_tool_calls(tool_calls, registry)
        if payload is not None:
            return payload
    return None
