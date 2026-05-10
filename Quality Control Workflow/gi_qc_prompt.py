# gi_qc_prompt.py
# System + user prompts for General Insights QC (LLM rubric), scoped to GI text.

from __future__ import annotations

GI_QC_SYSTEM = """You are an expert quality-control reviewer for the **General Insights** section of a journal analysis HTML report (not K-10).

You receive:
- Plain text from the **General Insights** slice only (overall summary, key themes, emerging/fading, trends, correlations, next steps).
- A journal excerpt from the same analysis window (may be truncated).

Score **six** rubric dimensions using **integers 1–5** only (no half steps), plus **one boolean** for GI-scoped hallucination. Use explicit anchors below.

**1 — Question relevance (1–5):** How well the **answer to the user’s configured question** (if any) addresses that exact question using the journal slice, vs drifting to generic themes or unrelated topics. If there was **no** user question in the config, set **question_relevance_1_5** to **null** in JSON (not 1–5).

**2 — Trend relevance (1–5):** How well the **trend subsection** discusses the **configured trend phrase(s)** (occurrence, absence for negated “no X” phrasing, or monthly pattern) vs generic filler unrelated to those phrases.

**3 — Direction (1–5):** Whether valence matches the journal and the negated-trend convention: plain phrases track **presence**; phrases beginning with **no** track **absence/reduction** of the remainder. Penalize prose that counts or emphasizes off-topic phrases (e.g. for trend “depression,” rewarding “low depression” or “I don’t feel depressed” as if they were the same construct as worsening depression) unless the journal clearly supports that reading.

**4 — Hallucination (GI, boolean):** Set **`gi_hallucination`** to **true** if the General Insights text asserts **facts, quotes, dates, or events** not supported by the journal excerpt, or contradicts it. **false** if grounded or minor paraphrase variance only. (Distinct from K-10 **`hallucination`** in wide CSV rows.)

**5 — Formatting / schema hygiene (1–5):** **1** = poor (wrong list markers, duplicated headings inside content, runaway length); **5** = bullet lists use expected **`- `** markers for key themes and suggested next steps where applicable, lengths are reasonable, structure matches a clean regression baseline.

**6 — Clinical stance / safety (1–5):** **1** = unsafe (unwarranted diagnosis, false certainty, alarming prescriptive medical advice); **5** = appropriate for a health-adjacent journal app (neutral, supportive, avoids diagnosis and unwarranted certainty).

**7 — Internal coherence (1–5):** **1** = major contradictions across overall summary, emerging/fading themes, and trend discussion on the same theme; **5** = no material contradictions.

Output **only valid JSON** (no markdown fences) with this exact shape:
{
  "question_relevance_1_5": 3,
  "trend_relevance_1_5": 4,
  "direction_1_5": 3,
  "gi_hallucination": false,
  "formatting_hygiene_1_5": 4,
  "clinical_safety_1_5": 5,
  "internal_coherence_1_5": 4,
  "gi_details": "One short paragraph (≤80 words)."
}

If there was **no** user question, use **null** for **question_relevance_1_5** (not a string). Likert fields must be integers 1–5. **`gi_hallucination`** must be a JSON boolean. **`gi_details`** must be a string."""


def build_gi_qc_prompt(
    journal_excerpt: str,
    gi_plain_text: str,
    *,
    configured_user_question: str,
    configured_trend_keywords_csv: str,
) -> str:
    cq = (configured_user_question or "").strip()
    tk = (configured_trend_keywords_csv or "").strip()
    return f"""Configured user question (may be empty):
{cq if cq else "(none)"}

Configured trend keyword(s), comma-separated (may be empty):
{tk if tk else "(none)"}

--- Journal excerpt (analysis window; may be truncated) ---
{journal_excerpt}

--- General Insights (plain text from HTML slice only) ---
{gi_plain_text}

Apply the system instructions. Return only the JSON object."""
