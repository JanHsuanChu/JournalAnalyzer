-- Alternative RAG schema when using EMBEDDING_BACKEND=openai + text-embedding-3-small (1536-d).
-- Do not mix with the default 384-d migration: pick ONE vector size per database.
-- Use this file instead of supabase_migration_journal_rag.sql if you rely on OpenAI embeddings only.

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS journal_chunk (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  journal_entry_id BIGINT NOT NULL REFERENCES journal_entry(id) ON DELETE CASCADE,
  chunk_index INT NOT NULL,
  entry_date DATE NOT NULL,
  text TEXT NOT NULL,
  char_start INT,
  char_end INT,
  content_hash TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (journal_entry_id, chunk_index)
);

CREATE INDEX IF NOT EXISTS idx_journal_chunk_entry_date ON journal_chunk (entry_date);
CREATE INDEX IF NOT EXISTS idx_journal_chunk_journal_entry_id ON journal_chunk (journal_entry_id);

CREATE TABLE IF NOT EXISTS journal_chunk_embedding (
  chunk_id UUID NOT NULL REFERENCES journal_chunk(id) ON DELETE CASCADE,
  embedding vector(1536) NOT NULL,
  embedding_model TEXT NOT NULL,
  embedded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (chunk_id)
);

CREATE INDEX IF NOT EXISTS idx_journal_chunk_embedding_hnsw
  ON journal_chunk_embedding
  USING hnsw (embedding vector_cosine_ops);

CREATE OR REPLACE FUNCTION match_journal_chunks(
  query_embedding vector(1536),
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
