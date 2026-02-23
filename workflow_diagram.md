# Journal Analyzer — Report workflow

This diagram describes the high-level flow for report generation. Use it as context for related development.

```mermaid
flowchart LR
  A["Input: Open text (Journal)"]
  B["Summarize (trend of user specified keywords and rating summary to user specified questions)"]
  C["Interpret (plain language summary to user specified questions)"]
  D["Format"]
  E["Output: HTML report with charts and AI summaries"]
  A --> B --> C --> D --> E
```

## Node summary

| Node | Role |
|------|------|
| **A** | Input: open text (journal entries). |
| **B** | Summarize: trends for user-specified phrases and rating/summary for user-specified questions (Ollama). |
| **C** | Interpret: plain-language summary answering user-specified questions (life activity, emotion, observations). |
| **D** | Format: HTML with tables and charts (observations by month/day/time, trend-by-phrase). |
| **E** | Output: HTML report with charts and AI summaries (served via `/reports/{filename}`). |
