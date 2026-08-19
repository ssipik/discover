# rag-discover

Shared ingestion pipeline library for the agentic RAG pipeline. Implements **stage 1 (DISCOVER)** of the six-stage ingestion skeleton — see [`../ARCHITECTURE.md`](../ARCHITECTURE.md) (block 11) for the master plan.

Discovery lists documents from configured sources and upserts manifest rows (URL, title, cheap change signals) into PostgreSQL. It is **listings only**: document content is never downloaded here — raw bytes are fetched in stage 4, after the stage-2 delta check has discarded unchanged documents.

**Library-first**: Airflow DAG tasks will import `rag_discover.discover.run_discovery()` directly. For dev runs (and later Nomad dispatch) there's a thin argparse entrypoint: `python -m rag_discover [--source ID ...] [--run-id ...]` — no stage logic lives in it.

## Layout

```
config/sources.yaml        # source registry (currently: two ECB RSS feeds)
src/rag_discover/
  models.py                # ManifestEntry — the stage-1 output contract
  sources.py               # sources.yaml loading + validation
  settings.py              # env config: RAG_PG_DSN, RAG_HTTP_TIMEOUT, RAG_HTTP_USER_AGENT
  db.py                    # ingest_manifest table + idempotent upsert
  discover.py              # run_discovery() — the stage runner
  readers/                 # one reader per source type
    base.py                #   BaseReader ABC (contract: listings only)
    rss.py                 #   RSS/Atom via httpx + feedparser
tests/                     # offline unit tests (fixture = real ECB feed snapshot)
```

## Run locally

Postgres is a **separate, manually started service** (own directory, own compose — like in production, the pipeline is only a TCP client of it). Its schema (`ingest_manifest` DDL) lives there too: `../postgres/sql/001_discover.sql`.

```bash
# 1. Postgres — once, from its own directory (schema auto-created on first start; host port 5433)
(cd ../postgres && docker compose up -d)

# 2. Discovery against the live ECB feeds (all sources; --source ecb_press to subset)
docker compose run --rm --build pipeline python -m rag_discover

# 3. Inspect the manifest
(cd ../postgres && docker compose exec postgres psql -U rag -d rag_ingestion \
  -c "select source_id, count(*) from ingest_manifest group by 1;")
```

From a host venv instead (needs Postgres from step 1; DSN in `.env.example`):

```bash
uv sync --frozen --extra dev        # lock-exact install from uv.lock (creates .venv)
.venv/bin/python -m pytest          # offline unit tests
```

Dependency versions are pinned in `uv.lock` — both the venv and the Docker image (`uv sync --frozen`) install exactly those versions. Change `pyproject.toml`, then run `uv lock` to update the lockfile.

## Building the image

`sources.yaml` and `parameters.yaml` come from `rag-config`, a **private** Bitbucket repo pinned by commit in `uv.lock`. The build clones it, so it needs read access:

```bash
export BB_TOKEN='<repository access token>'
docker build --secret id=git_token,env=BB_TOKEN -t rag-discover .
```

Create the token at the `config` repo → Settings → Security → Access tokens → **Create Repository Access Token**, scope **Repositories: Read**. One token covers every stage's build — they all pull the same repo.

It is passed as a BuildKit secret, never an `ARG` or `ENV`, and the `.netrc` it writes is deleted inside the same `RUN`, so no image layer holds it. With no secret passed the block is a no-op, so this keeps working if the repo is ever made public.

**When the token is needed**

- Rebuilding the dependency layer: a changed `uv.lock` or `pyproject.toml`, `--no-cache`, or a fresh machine or CI runner with no layer cache.

**When it is not**

- Stage code changes only. `COPY src ./src` comes *after* the dependency layer, so Docker reuses the cached `uv sync` and never contacts Bitbucket.
- **Running** an image — `rag-config` is already installed in `/opt/venv`.
- Host venvs. `uv sync` uses your own git credential helper, so the tests and local runs need no token.

So credentials are a **build-time** concern only: whoever runs a container or a Nomad job authenticates to nothing.

## Adding a source

Add an entry to `sources.yaml` in the shared [`rag-config`](../config) package — once, for every stage. For a new *source type*, implement a `BaseReader` subclass and register it in `readers/__init__.py` (`READER_REGISTRY`); nothing else changes.

Because `uv.lock` pins `rag-config` by commit, a config change reaches this stage only when its `rev` is bumped in `pyproject.toml` and re-locked.

## Design notes

- **Manifest key** is `(run_id, source_system, doc_url)` — deliberately without `source_id`: the same document listed by two feeds of one source system (e.g. ECB meeting accounts appear in both press feeds) is one row and is never parsed/embedded twice.
- **Rolling-window feeds** (ECB RSS shows only the ~15 newest items) carry `detect_deletions: false` — absence from a run is not a deletion signal, and stage 2 must exclude them from deletion detection.
- `run_id` is caller-supplied (later the Airflow run_id). In stage discover it only has the role to label the documents discovered in the current run. The following stages use the `run_id` to identify the most recent sightings. Running these stages is idempotent.
