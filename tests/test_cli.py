from __future__ import annotations

import pytest

from photo_curator.cli import build_parser, main


def test_native_app_is_the_only_ui_entrypoint(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        main([])

    assert exit_info.value.code == 2
    assert "PhotoCurator.app" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        build_parser().parse_args(["legacy-web"])


def test_learning_corpus_commands_are_explicit() -> None:
    parser = build_parser()

    assert parser.parse_args(["learning-corpus-export"]).command == "learning-corpus-export"
    imported = parser.parse_args(["learning-corpus-import", "--input", "corpus.json"])
    assert imported.command == "learning-corpus-import"
    assert imported.input.name == "corpus.json"
