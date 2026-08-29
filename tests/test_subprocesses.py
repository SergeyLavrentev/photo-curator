import sys
import time

import pytest

from photo_curator.utils.subprocesses import CommandCancelled, run_command


def test_run_command_stops_child_on_cooperative_cancellation() -> None:
    started = time.monotonic()

    with pytest.raises(CommandCancelled):
        run_command(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            timeout=60,
            cancelled=lambda: time.monotonic() - started >= 0.1,
        )

    assert time.monotonic() - started < 3
