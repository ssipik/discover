"""Shared ingestion pipeline library (ARCHITECTURE.md → Ingestion orchestration).

The same library is imported by Airflow DAG tasks and baked into the
Nomad batch-job container; only the entrypoint differs.
"""
