from __future__ import annotations

import sys

from photo_curator.logging_setup import configure_logging
from photo_curator.native_worker import run_native_worker
from photo_curator.paths import default_application_paths


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if not arguments or arguments[0] != "native-worker":
        print("Native backend only supports the native-worker command", file=sys.stderr)
        return 2
    demo = arguments[1:] == ["--demo"]
    if arguments[1:] not in ([], ["--demo"]):
        print("Unsupported native-worker arguments", file=sys.stderr)
        return 2
    paths = default_application_paths()
    paths.ensure()
    configure_logging(paths.log_file)
    return run_native_worker(paths, demo=demo)


if __name__ == "__main__":
    raise SystemExit(main())
