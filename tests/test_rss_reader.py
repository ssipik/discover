from pathlib import Path

import pytest

from rag_discover.db import deduplicate_entries
from rag_discover.readers import get_reader
from rag_discover.readers.rss import RssReader
from rag_discover.sources import SourceConfig

FIXTURE = Path(__file__).parent / "fixtures" / "ecb_press_sample.xml"


def make_config() -> SourceConfig:
    return SourceConfig(
        id="ecb_press",
        type="rss",
        source_system="web_rss",
        url="https://www.ecb.europa.eu/rss/press.html",
        doc_type="press_release",
        default_language="en",
        detect_deletions=False,
    )


def test_parse_feed_yields_manifest_entries():
    reader = RssReader(make_config())
    entries = list(reader.parse_feed(FIXTURE.read_bytes(), run_id="test-run"))

    assert len(entries) == 3
    entry = entries[0]
    assert entry.run_id == "test-run"
    assert entry.source_system == "web_rss"
    assert entry.source_id == "ecb_press"
    assert entry.doc_url.startswith("https://www.ecb.europa.eu/")
    assert entry.title
    assert entry.published_at is not None
    assert entry.published_at.tzinfo is not None
    assert entry.doc_version
    assert entry.extra["doc_type"] == "press_release"
    assert entry.extra["default_language"] == "en"
    assert entry.extra["feed_url"] == "https://www.ecb.europa.eu/rss/press.html"
    # listings only: no content fields exist on the entry at all
    assert "content" not in entry.model_dump()


def test_parse_feed_rejects_non_feed_content():
    reader = RssReader(make_config())
    with pytest.raises(ValueError, match="unparseable feed"):
        list(reader.parse_feed(b"<html><body>not a feed</body></html>", run_id="r"))


def test_registry_resolves_rss_reader():
    assert isinstance(get_reader(make_config()), RssReader)


def test_registry_rejects_unknown_type():
    cfg = make_config().model_copy(update={"type": "carrier_pigeon"})
    with pytest.raises(ValueError, match="no reader registered"):
        get_reader(cfg)


def test_deduplicate_keeps_last_entry_per_url():
    reader = RssReader(make_config())
    entries = list(reader.parse_feed(FIXTURE.read_bytes(), run_id="r"))
    duplicated = entries + [entries[0]]
    assert len(deduplicate_entries(duplicated)) == len(entries)
