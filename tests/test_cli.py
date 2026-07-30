from __future__ import annotations

import pytest

from photo_curator.cli import build_parser, main


def test_legacy_web_requires_explicit_command(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        main([])

    assert exit_info.value.code == 2
    assert "explicit legacy-web" in capsys.readouterr().err
    assert build_parser().parse_args(["legacy-web", "--demo"]).command == "legacy-web"
