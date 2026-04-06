# Supabase setup for Journal Analyzer

Hosts **[journal_entries.csv](journal_entries.csv)** in a dedicated **`journal_entry`** table on your Supabase project. It does **not** modify Traffic-Predictor tables (`traffic`, `event`).

**Project URL (example):** `https://stnfxxjktzznvlfhczcz.supabase.co`

---

## 1. Create the table

Run the SQL in **[supabase_migration_journal_entry.sql](supabase_migration_journal_entry.sql)** in the Supabase Dashboard **SQL Editor**, or apply it via Supabase MCP as a migration.

---

## 2. Credentials

In **Project Settings → API**, copy **Project URL** and a key (**service_role** recommended for loaders and server-side API; never expose `service_role` in the browser).

Add to **JournalAnalyzer/.env** (gitignored):

```bash
SUPABASE_URL=https://stnfxxjktzznvlfhczcz.supabase.co
SUPABASE_KEY=your-key-here
```

---

## 3. Load CSV data

```bash
cd JournalAnalyzer
pip install -r requirements.txt
python load_journal_to_supabase.py
```

By default this **deletes existing rows in `journal_entry`** then inserts from `journal_entries.csv` (idempotent re-runs). Use `--no-truncate` only if you intend to append and accept duplicates.

---

## 4. API

With `SUPABASE_URL` and `SUPABASE_KEY` set, **[api.py](api.py)** serves `GET /entries` from Supabase. If credentials are missing or the query fails, it falls back to **journal_entries.csv**.

---

## 5. Verify

In **SQL Editor**:

```sql
SELECT COUNT(*) FROM journal_entry;
```

---

## 6. Optional: RAG (pgvector + chunks)

For **semantic retrieval** in reports ([`embedding_pipeline.py`](embedding_pipeline.py), [`retrieval.py`](retrieval.py)), aligned with the course template [`../dsai/07_rag/05_embed.py`](../dsai/07_rag/05_embed.py) (local **sentence-transformers**) while keeping vectors in Supabase:

1. Run **[supabase_migration_journal_rag.sql](supabase_migration_journal_rag.sql)** in the SQL Editor (creates `journal_chunk`, `journal_chunk_embedding`, **`vector(384)`**, and `match_journal_chunks` RPC). Default Python embedder is **`all-MiniLM-L6-v2`** (`EMBEDDING_MODEL`).
2. Install **`sentence-transformers`** (`pip install -r requirements.txt`). No API key is required for embeddings with the default backend.
3. **Optional OpenAI embeddings:** use **`EMBEDDING_BACKEND=openai`**, set **`OPENAI_API_KEY`**, and apply **[supabase_migration_journal_rag_openai_1536.sql](supabase_migration_journal_rag_openai_1536.sql)** instead of the default file (pick **one** vector size per database).
4. If you already applied an older **1536**-column migration and want the default MiniLM path, run **[supabase_migration_journal_rag_upgrade_1536_to_384.sql](supabase_migration_journal_rag_upgrade_1536_to_384.sql)** once, then re-embed by generating a report.

---

## 7. Optional: K10 snapshot history (`k10_snapshot`)

Longitudinal K10 charts read from **`k10_snapshot`** ([`snapshot.py`](snapshot.py)). The app does **not** create this table automatically—you must apply the migration once.

1. **SQL Editor:** open **[supabase_migration_k10_snapshot.sql](supabase_migration_k10_snapshot.sql)**, paste into **SQL → New query**, **Run**.
2. **CLI (optional):** add **`DATABASE_URL`** (Postgres URI from **Project Settings → Database**) to `.env`, then from `JournalAnalyzer`:

   ```bash
   python scripts/apply_k10_snapshot_migration.py
   ```

3. **Verify:** with `SUPABASE_URL` + key in `.env`:

   ```bash
   python scripts/verify_k10_snapshot_table.py
   ```

   You should see `k10_snapshot: OK`. After generating a report with **K10 summary** enabled, new rows should appear (inserts log a warning if the table is still missing).

---

## Schema note

The CSV column `date` is stored as **`entry_date`** in Postgres (avoids reserved-word friction). The JSON API still returns **`date`** for the Shiny app.
