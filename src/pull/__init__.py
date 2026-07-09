"""Pull layer — one module per source (§2). Each exposes pull functions that
land raw data in the DuckDB/Parquet lake (store.append_parquet) and return
tidy pandas frames for the compute step. Sources fail soft: a raising pull is
caught by run.py, logged to run_log.jsonl, and the panel falls back to
last-good display JSON (§2 failure handling)."""
