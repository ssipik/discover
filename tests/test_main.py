from rag_discover.__main__ import build_parser, main


def test_parser_defaults():
    args = build_parser().parse_args([])
    assert args.source_ids is None  # None → all sources
    assert args.run_id is None


def test_parser_repeatable_source():
    args = build_parser().parse_args(["--source", "a", "--source", "b"])
    assert args.source_ids == ["a", "b"]


def test_main_unknown_source_exits_2(capsys, tmp_path, monkeypatch):
    config = tmp_path / "sources.yaml"
    config.write_text(
        "sources:\n"
        "  s1:\n"
        "    type: rss\n"
        "    source_system: web_rss\n"
        "    url: https://example.com/feed\n"
    )
    monkeypatch.setenv("RAG_SOURCES_PATH", str(config))
    rc = main(["--source", "nope"])
    assert rc == 2
    assert "unknown source" in capsys.readouterr().err
