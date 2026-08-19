
import pytest

from rag_discover.discover import run_discovery

CONFIG = None  # the copy shipped in rag-config


def test_unknown_source_id_rejected_before_any_io():
    with pytest.raises(ValueError, match="unknown source"):
        run_discovery(CONFIG, source_ids=["nope"])
