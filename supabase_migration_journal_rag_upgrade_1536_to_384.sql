-- JournalAnalyzer RAG: upgrade existing install from vector(1536) / OpenAI to vector(384) / sentence-transformers.
-- Run in Supabase SQL Editor ONLY if you already applied supabase_migration_journal_rag.sql with 1536-d vectors.
-- After this, re-run reports to re-embed chunks (incremental pipeline will fill empty embeddings).

DROP INDEX IF EXISTS idx_journal_chunk_embedding_hnsw;

-- Clear old vectors (incompatible dimension).
TRUNCATE journal_chunk_embedding;

ALTER TABLE journal_chunk_embedding
  ALTER COLUMN embedding TYPE vector(384);

CREATE INDEX IF NOT EXISTS idx_journal_chunk_embedding_hnsw
  ON journal_chunk_embedding
  USING hnsw (embedding vector_cosine_ops);

CREATE OR REPLACE FUNCTION match_journal_chunks(
  query_embedding vector(384),
  embedding_model_filter TEXT,
  filter_date_from DATE,
  filter_date_to DATE,
  match_count INT,
  min_similarity DOUBLE PRECISION
)
RETURNS TABLE (
  chunk_id UUID,
  journal_entry_id BIGINT,
  entry_date DATE,
  chunk_text TEXT,
  similarity DOUBLE PRECISION
)
LANGUAGE sql
STABLE
AS $$
  SELECT
    jc.id AS chunk_id,
    jc.journal_entry_id,
    jc.entry_date,
    jc.text AS chunk_text,
    (1 - (jce.embedding <=> query_embedding))::double precision AS similarity
  FROM journal_chunk_embedding jce
  INNER JOIN journal_chunk jc ON jc.id = jce.chunk_id
  WHERE jce.embedding_model = embedding_model_filter
    AND jc.entry_date >= filter_date_from
    AND jc.entry_date <= filter_date_to
    AND (1 - (jce.embedding <=> query_embedding)) >= min_similarity
  ORDER BY jce.embedding <=> query_embedding
  LIMIT match_count;
$$;

COMMENT ON FUNCTION match_journal_chunks IS 'RAG: top chunks by cosine similarity within date range (384-d embeddings).';
