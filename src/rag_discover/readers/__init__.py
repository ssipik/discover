from __future__ import annotations

from rag_discover.readers.base import BaseReader
from rag_discover.readers.confluence import ConfluenceReader
from rag_discover.readers.local_files import LocalFilesReader
from rag_discover.readers.rss import RssReader
from rag_config import SourceConfig

# One reader per source type (ARCHITECTURE.md → Source connectors). Future readers
# ("bitbucket", "sitemap") register here without touching the CLI or DB code.
READER_REGISTRY: dict[str, type[BaseReader]] = {
    "rss": RssReader,
    "local_files": LocalFilesReader,
    "confluence": ConfluenceReader,
}


def get_reader(config: SourceConfig) -> BaseReader:
    try:
        reader_cls = READER_REGISTRY[config.type]
    except KeyError:
        raise ValueError(
            f"no reader registered for source type {config.type!r} "
            f"(source {config.id!r}); known types: {sorted(READER_REGISTRY)}"
        ) from None
    return reader_cls(config)
