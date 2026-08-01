from __future__ import annotations

import json
import os
from pathlib import Path

from photo_curator.analysis.codex_vision import (
    CodexVisionRunner,
    build_batches,
    codex_status,
    discover_codex,
)


def test_discovery_prefers_explicit_executable(tmp_path: Path, monkeypatch) -> None:
    executable = tmp_path / "codex"
    executable.write_text("#!/bin/sh\nexit 0\n")
    executable.chmod(0o700)
    monkeypatch.setenv("PHOTO_CURATOR_CODEX_BINARY", str(executable))

    assert discover_codex() == executable.resolve()


def test_status_accepts_chatgpt_and_rejects_api_key(tmp_path: Path, monkeypatch) -> None:
    executable = tmp_path / "codex"
    executable.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = "--version" ]; then echo codex-cli-test; exit 0; fi\n'
        'echo "${TEST_CODEX_LOGIN:-Logged in using ChatGPT}"\n'
    )
    executable.chmod(0o700)
    monkeypatch.setenv("PHOTO_CURATOR_CODEX_BINARY", str(executable))

    assert codex_status().ready
    monkeypatch.setenv("TEST_CODEX_LOGIN", "Logged in using an API key")
    status = codex_status()
    assert not status.ready
    assert status.state == "api_key"


def test_batches_keep_duplicate_series_together() -> None:
    assets = [
        {"asset_uuid": f"asset-{index}", "review_path": f"/{index}.jpg"} for index in range(8)
    ]
    groups = [
        {
            "group_id": "group-a",
            "kind": "near",
            "members": [{"asset_uuid": "asset-1"}, {"asset_uuid": "asset-2"}],
        },
        {
            "group_id": "group-b",
            "kind": "near",
            "members": [{"asset_uuid": "asset-3"}, {"asset_uuid": "asset-4"}],
        },
    ]

    batches = build_batches(assets, groups, limit=5)

    assert all(len(batch) <= 5 for batch in batches)
    locations = {
        str(asset["asset_uuid"]): batch_index
        for batch_index, batch in enumerate(batches)
        for asset in batch
    }
    assert locations["asset-1"] == locations["asset-2"]
    assert locations["asset-3"] == locations["asset-4"]
    assert {asset_id for asset_id in locations} == {f"asset-{index}" for index in range(8)}


def test_runner_uses_ephemeral_read_only_cli_and_validates_all_assets(
    tmp_path: Path, monkeypatch
) -> None:
    executable = tmp_path / "codex"
    executable.write_text(
        "#!/bin/sh\n"
        'printf \'%s\\n\' "$@" > "$TEST_CODEX_ARGS"\n'
        "out=''\n"
        "previous=''\n"
        'for value in "$@"; do\n'
        '  if [ "$previous" = \'--output-last-message\' ]; then out="$value"; fi\n'
        '  previous="$value"\n'
        "done\n"
        'cp "$TEST_CODEX_RESULT" "$out"\n'
    )
    executable.chmod(0o700)
    image = tmp_path / "photo.jpg"
    image.write_bytes(b"jpeg")
    result = tmp_path / "fake-result.json"
    result.write_text(
        json.dumps(
            {
                "photos": [
                    {
                        "asset_id": "asset-1",
                        "aesthetic_score": 70,
                        "composition_score": 72,
                        "interestingness_score": 60,
                        "moment_score": 65,
                        "face_visibility": "no_face",
                        "defects": [],
                        "series_rank": 0,
                        "reject_recommended": False,
                        "confidence": 0.8,
                        "reason": "ok",
                    }
                ]
            }
        )
    )
    args = tmp_path / "args.txt"
    monkeypatch.setenv("TEST_CODEX_ARGS", str(args))
    monkeypatch.setenv("TEST_CODEX_RESULT", str(result))

    output = CodexVisionRunner(executable).analyze(
        [{"asset_uuid": "asset-1", "review_path": str(image)}],
        work_dir=tmp_path / "work",
        model="gpt-test",
    )

    assert output["asset-1"]["aesthetic_score"] == 70
    command = args.read_text()
    assert "--ephemeral" in command
    assert "--ignore-user-config" in command
    assert "read-only" in command
    assert "gpt-test" in command
    assert os.fspath(image) in command
