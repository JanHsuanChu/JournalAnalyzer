# qc_prompt.py
# System + user prompts for K-10 QC (gpt-oss:20b-cloud), aligned with the plan appendix.

from __future__ import annotations

from paths import ensure_ja_on_path

ensure_ja_on_path()

from k10_utils import K10_RAG_QUERIES  # noqa: E402

K10_ITEM_STEMS = list(K10_RAG_QUERIES)

K10_QC_SYSTEM = """You are an expert quality-control reviewer for Kessler K-10 style summaries generated from personal journal text.

You will receive:
- The journal text used for assessment (with dates if provided).
- The generated HTML report, stripped to plain text (K-10 section and any per-item scores, evidence, and rationale).

Your task is to score TWO dimensions for EACH of the 10 K-10 items (indices 1–10 in order matching the stem list provided in the user message), PLUS one **whole-report** hallucination boolean.

Scoring rules (Likert 1–5 for each item, integers only):

(1) FREQUENCY RUBRIC (not severity)
- High scores (4–5): The item’s score and reasoning appear consistent with **how often** the theme appears **across journal entries or days** in the window—breadth/recurrence of relevant content—not with how **intense** a single sentence sounds.
- Low scores (1–2): The score or rationale seems driven mainly by **severity or vividness of language in one or few entries**, without support that the **frequency** of that theme across the diary justifies the chosen frequency band; e.g. a single “I often feel …” line should **not** alone justify the highest frequency category unless the rest of the diary supports that pattern.

(2) EVIDENCE RELEVANCE
- High scores (4–5): The quoted or paraphrased **evidence and rationale** for that item clearly relates to **that item’s specific symptom theme** (the stem for that row).
- Low scores (1–2): Evidence looks **off-topic**, mixed with another item’s theme, or too generic to support that item’s score.

(3) HALLUCINATION (whole report, one judgment)
- Set **"hallucination": true** if the report asserts **facts, quotes, or diary events that are not supported by** the supplied journal text (fabrication), or contains **clear contradictions** of the journal. **false** if content is grounded or only minor paraphrase variance.

Output **only valid JSON** (no markdown fences) with this exact shape:
{
  "frequency_rubric_per_item": [1,2,3,4,5,1,2,3,4,5],
  "frequency_rubric_mean": 3.4,
  "evidence_relevance_per_item": [1,2,3,4,5,1,2,3,4,5],
  "evidence_relevance_mean": 3.2,
  "hallucination": false,
  "details": "One short paragraph (≤80 words) summarizing main issues or strengths."
}

Each per-item array must have exactly 10 integers from 1 to 5. Recompute means as the arithmetic mean of the 10 values, rounded to **two** decimal places. **hallucination** must be a JSON boolean."""


def build_k10_qc_prompt(
    journal_text: str,
    report_plain_text: str,
    *,
    rag_retrieval_context: str | None = None,
) -> str:
    stems_block = "\n".join(f"{i + 1}. {s}" for i, s in enumerate(K10_ITEM_STEMS))
    extra = ""
    if rag_retrieval_context and rag_retrieval_context.strip():
        extra = (
            "\n\n--- Retrieved passages (RAG context for QC; may overlap with scoring inputs) ---\n"
            + rag_retrieval_context.strip()
        )
    return f"""K-10 item stems (1–10 in order; your per-item scores must align with these indices):
{stems_block}

--- Journal text (scope is chosen by the experiment config; passed in by the runner) ---
{journal_text}

--- Report (plain text from HTML) ---
{report_plain_text}
{extra}

Apply the system instructions. Return only the JSON object."""
