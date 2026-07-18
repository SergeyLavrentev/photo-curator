from __future__ import annotations

import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class CommandResult:
    args: list[str]
    returncode: int
    stdout: str
    stderr: str


def find_executable(name: str) -> str | None:
    found = shutil.which(name)
    if found:
        return found
    sibling = Path(sys.executable).parent / name
    return str(sibling) if sibling.is_file() else None


def run_command(args: list[str], *, timeout: int = 300) -> CommandResult:
    result = subprocess.run(
        args,
        shell=False,
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return CommandResult(list(result.args), result.returncode, result.stdout, result.stderr)
