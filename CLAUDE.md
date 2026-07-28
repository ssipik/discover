# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

An agentic RAG pipeline over heterogeneous sources (internal PDFs/DOCX, Confluence, Bitbucket, BaFin/ECB regulator sites), built in Python on LlamaIndex. **`../ARCHITECTURE.md` (one level up, at the project root) is the master plan and the authoritative design document** — it defines 12 building blocks and a six-stage ingestion skeleton (DISCOVER → DELTA → PLAN → PARSE+CHUNK → EMBED → FINALIZE), orchestrated by Airflow dispatching Nomad batch jobs. Read the relevant block there before designing anything new; code comments reference it by block number.

Only **stage 1 (DISCOVER)** is implemented so far, in this directory as the `rag_discover` package. Everything else (parsing, chunking, embedding, Qdrant, agent layer) is design-only.

## Commands

All from this directory:

```bash
# Local Postgres — separate service in ../postgres (schema auto-applied from its sql/ on first start; host port 5433)
(cd ../postgres && docker compose up -d)

# Run discovery against the live ECB feeds (flags: --source ID, repeatable; --run-id; --config)
docker compose run --rm --build pipeline python -m rag_discover
# compose live-mounts src/rag_discover and config/ into the container, so code
# edits don't need --build — only dependency (uv.lock) changes do

# Tests (offline; fixtures under tests/fixtures/)
.venv/bin/python -m pytest
.venv/bin/python -m pytest tests/test_rss_reader.py -k test_parse_feed_yields_manifest_entries

# Host venv setup (lock-exact from uv.lock; Python 3.13 is the settled runtime)
uv sync --frozen --extra dev
```

Config via env vars (see `.env.example`): `RAG_PG_DSN`, `RAG_HTTP_TIMEOUT`, `RAG_HTTP_USER_AGENT`; `RAG_SOURCES_PATH` overrides the default `--config` path (set to the baked-in copy in the Docker image).

## Architecture & invariants

- **Library-first, thin `__main__`.** Stage logic lives in importable functions (`rag_discover.discover.run_discovery()`) — that is the API Airflow tasks call in-process. `__main__.py` is a thin stdlib-argparse wrapper (`python -m rag_discover`) for dev runs and later Nomad dispatch: no click/console-scripts, no stage logic in it. Same convention for every future stage package.
- **Discovery is listings-only.** `BaseReader.discover(run_id)` yields `ManifestEntry` rows with identifiers and cheap change signals (guid, ETag, mtime, commit SHA); it must **never download document content**. Raw bytes are fetched in stage 4, after the stage-2 delta check discards unchanged docs.
- **Stage outputs carry only IDs and counts, never document content** (the XCom contract from ARCHITECTURE.md block 11). `run_discovery()` returns a `DiscoveryReport` of counts; the data goes to the Postgres `ingest_manifest` table.
- **Reader registry pattern**: one reader per source type. New source type = a `BaseReader` subclass in `src/rag_discover/readers/` registered in `READER_REGISTRY` (`readers/__init__.py`); new source instance = an entry in `config/sources.yaml`. Nothing else changes. Implemented types: `rss`, `local_files` (directory scan; `path`+`glob`, `file:///<relpath>` doc_urls, mtime+size change signal). Planned: `confluence`, `bitbucket`, `sitemap`.
- **Manifest primary key is `(run_id, source_system, doc_url)` — deliberately without `source_id`**: the same document listed by two feeds of one source system (happens with ECB) is one row and must never be parsed/embedded twice. Last writer wins on non-key columns, so per-source counts can sum above row count.
- **Rolling-window sources** (RSS feeds exposing only the ~15 newest items) carry `detect_deletions: false` in `sources.yaml`: absence from a run is *not* a deletion signal, and stage 2 must exclude them from deletion detection.
- **Schema authority**: `../postgres/sql/001_discover.sql` is authoritative for the DB schema (Postgres is its own decoupled service, started manually from `../postgres/`); the SQLAlchemy `Table` in `db.py` mirrors it — keep both in sync when changing the schema.
- Re-running discovery with the same `run_id` is idempotent (upsert). `run_id` is caller-supplied (later the Airflow run_id); local runs default to a timestamped `local-…` id.
