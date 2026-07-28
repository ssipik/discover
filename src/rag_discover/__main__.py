"""Dev/ops entrypoint: ``python -m rag_discover``.

Thin argparse wrapper over :func:`rag_discover.discover.run_discovery` —
the library function remains the API that Airflow tasks import directly;
keep all stage logic out of this module.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

from rag_discover.discover import run_discovery


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m rag_discover",
        description="DISCOVER stage: list documents per source, upsert manifest rows.",
    )
    parser.add_argument(
        "--source",
        dest="source_ids",
        action="append",
        metavar="ID",
        help="source id from sources.yaml; repeatable (default: all sources)",
    )
    parser.add_argument(
        "--config",
        default=os.environ.get("RAG_SOURCES_PATH", "config/sources.yaml"),
        help="path to sources.yaml (default: $RAG_SOURCES_PATH or %(default)s)",
    )
    parser.add_argument(
        "--run-id",
        default=None,
        help="run id tying manifest rows to this run (default: local-<utc timestamp>)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    try:
        report = run_discovery(
            args.config, source_ids=args.source_ids, run_id=args.run_id
        )
    except (ValueError, FileNotFoundError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    for source_id, count in report.counts.items():
        print(f"{source_id}: {count} manifest rows")
    print(f"run {report.run_id}: {report.total} manifest rows upserted")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
