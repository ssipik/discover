from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Iterator

from rag_discover.models import ManifestEntry
from rag_config import SourceConfig


class BaseReader(ABC):
    """Discovery reader for one configured source (the discover stage).

    Contract: discover() lists the source's documents and yields one
    ManifestEntry per document, carrying cheap change signals only. It must
    NEVER download document content — raw bytes are fetched in the parse_chunk stage,
    after the delta stage has discarded unchanged documents.
    """

    def __init__(self, config: SourceConfig):
        self.config = config

    @abstractmethod
    def discover(self, run_id: str) -> Iterator[ManifestEntry]: ...
