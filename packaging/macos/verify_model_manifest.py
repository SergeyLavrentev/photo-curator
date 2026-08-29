#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path


def content_digest(path: Path, manifest_name: str) -> str:
    files = [path] if path.is_file() else sorted(item for item in path.rglob("*") if item.is_file())
    lines = []
    for item in files:
        relative = manifest_name if item == path else f"{manifest_name}/{item.relative_to(path)}"
        lines.append(f"{hashlib.sha256(item.read_bytes()).hexdigest()}  {relative}\n".encode())
    return hashlib.sha256(b"".join(lines)).hexdigest()


def main() -> int:
    if len(sys.argv) not in {2, 3}:
        raise SystemExit("usage: verify_model_manifest.py MODEL_ROOT [MANIFEST]")
    root = Path(sys.argv[1])
    manifest_path = Path(sys.argv[2]) if len(sys.argv) == 3 else root / "models.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for row in manifest.get("models", []):
        name = str(row["name"])
        expected = str(row["content_sha256"])
        actual = content_digest(root / name, name)
        if actual != expected:
            raise SystemExit(f"model digest mismatch: {name}: {actual} != {expected}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
