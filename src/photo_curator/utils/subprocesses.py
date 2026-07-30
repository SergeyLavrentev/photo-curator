from __future__ import annotations

import shutil
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from queue import Empty, Queue


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


def run_streaming_command(
    args: list[str],
    *,
    on_stdout_line: Callable[[str], None],
    timeout: int = 300,
) -> CommandResult:
    """Run a line-buffered helper without waiting for its final response.

    Native PhotoKit helpers use this transport for progress JSONL. Reading stdout
    on a background thread keeps timeout enforcement working even while PhotoKit
    is waiting for iCloud.
    """

    process = subprocess.Popen(
        args,
        shell=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    assert process.stdout is not None
    assert process.stderr is not None
    stdout_lines: list[str] = []
    stderr_lines: list[str] = []
    queue: Queue[tuple[str, str | None]] = Queue()

    def read_stream(name: str, stream) -> None:
        try:
            for line in stream:
                queue.put((name, line))
        finally:
            queue.put((name, None))

    readers = [
        threading.Thread(target=read_stream, args=("stdout", process.stdout), daemon=True),
        threading.Thread(target=read_stream, args=("stderr", process.stderr), daemon=True),
    ]
    for reader in readers:
        reader.start()

    closed: set[str] = set()
    deadline = time.monotonic() + timeout
    try:
        while len(closed) < 2:
            try:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise Empty
                name, line = queue.get(timeout=remaining)
            except Empty:
                process.kill()
                process.wait()
                raise subprocess.TimeoutExpired(args, timeout) from None
            if line is None:
                closed.add(name)
                continue
            if name == "stdout":
                stdout_lines.append(line)
                on_stdout_line(line.rstrip("\r\n"))
            else:
                stderr_lines.append(line)
    except BaseException:
        if process.poll() is None:
            process.kill()
            process.wait()
        raise
    finally:
        process.stdout.close()
        process.stderr.close()
    return CommandResult(
        list(args),
        process.wait(),
        "".join(stdout_lines),
        "".join(stderr_lines),
    )
