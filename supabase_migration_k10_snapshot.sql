-- K10 snapshot history for trend charts (single-user MVP; no RLS)

CREATE TABLE IF NOT EXISTS k10_snapshot (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  window_start_date DATE,
  window_end_date DATE,
  total_score INTEGER NOT NULL CHECK (total_score >= 10 AND total_score <= 50),
  item_scores JSONB NOT NULL,
  severity_band TEXT NOT NULL,
  entry_count INTEGER DEFAULT 0,
  evidence_density TEXT,
  confidence_score DOUBLE PRECISION,
  model TEXT
);

CREATE INDEX IF NOT EXISTS idx_k10_snapshot_created_at ON k10_snapshot (created_at);

COMMENT ON TABLE k10_snapshot IS 'Append-only K10 runs for longitudinal charts.';
