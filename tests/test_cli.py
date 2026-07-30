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
