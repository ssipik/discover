from pathlib import Path

import pytest
from pydantic import ValidationError

from rag_discover.sources import load_sources

CONFIG = Path(__file__).parents[1] / "config" / "sources.yaml"


def test_load_shipped_config():
    sources = load_sources(CONFIG)
    assert set(sources) == {
        "ecb_press",
        "ecb_statpress",
        "local_pdfs",
        "confluence_quant",
    }
    for sid, cfg in sources.items():
        assert cfg.id == sid

    for sid in ("ecb_press", "ecb_statpress"):
        cfg = sources[sid]
        assert cfg.type == "rss"
        assert cfg.source_system == "web_rss"
        assert cfg.url.startswith("https://www.ecb.europa.eu/rss/")
        # rolling-window feeds must be excluded from deletion detection
        assert cfg.detect_deletions is False

    local = sources["local_pdfs"]
    assert local.type == "local_files"
    assert local.source_system == "local_files"
    assert local.path == "./data"
    assert local.glob == "**/*.pdf"
    assert local.default_language == "de"
    # a full directory scan is a complete snapshot, so deletions are detectable
    assert local.detect_deletions is True

    confluence = sources["confluence_quant"]
    assert confluence.type == "confluence"
    assert confluence.source_system == "confluence"
    assert confluence.base_url.startswith("https://")
    assert confluence.space_key and confluence.label
    # attachments are deferred to a later iteration (ARCHITECTURE.md block 1)
    assert confluence.include_attachments is False
    # a space+label listing is a complete snapshot, so a page that is deleted
    # or unlabelled is really gone
    assert confluence.detect_deletions is True


def test_unknown_field_rejected(tmp_path):
    bad = tmp_path / "sources.yaml"
    bad.write_text(
        "sources:\n"
        "  s1:\n"
        "    type: rss\n"
        "    source_system: web_rss\n"
        "    url: https://example.com/feed\n"
        "    typo_field: oops\n"
    )
    with pytest.raises(ValidationError):
        load_sources(bad)


def test_empty_config_rejected(tmp_path):
    empty = tmp_path / "sources.yaml"
    empty.write_text("")
    with pytest.raises(ValueError):
        load_sources(empty)
