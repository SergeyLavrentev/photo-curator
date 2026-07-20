from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENTRYPOINT = ROOT / "packaging/macos/native_backend_main.py"


def test_minimal_native_backend_runs_demo_jsonl_without_web_server(tmp_path: Path) -> None:
    requests = "\n".join(
        json.dumps(request)
        for request in (
            {"schema_version": 1, "id": "status", "method": "status", "params": {}},
            {"schema_version": 1, "id": "bye", "method": "shutdown", "params": {}},
        )
    )
    environment = dict(os.environ, HOME=str(tmp_path))

    result = subprocess.run(
        [sys.executable, str(ENTRYPOINT), "native-worker", "--demo"],
        input=requests + "\n",
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
        env=environment,
    )
    responses = [json.loads(line) for line in result.stdout.splitlines()]

    assert result.returncode == 0
    assert responses[0]["result"]["status"] == "ready"
    assert responses[1]["result"]["status"] == "bye"
    assert "http" not in result.stdout.lower()


def test_minimal_native_backend_rejects_legacy_commands(tmp_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, str(ENTRYPOINT), "doctor"],
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
        env=dict(os.environ, HOME=str(tmp_path)),
    )

    assert result.returncode == 2
    assert "only supports the native-worker command" in result.stderr
