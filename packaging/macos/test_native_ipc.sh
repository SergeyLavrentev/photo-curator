#!/bin/bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
mkdir -p "$ROOT/build/evidence"
TEST_ROOT="$(mktemp -d "$ROOT/build/evidence/native-ipc.XXXXXX")"
# Cleanup is restricted to this script's unique service-owned test directory.
trap 'rm -rf "$TEST_ROOT"' EXIT
TEST_APP="$TEST_ROOT/IPCBehavior.app/Contents"
mkdir -p "$TEST_APP/MacOS" "$TEST_APP/Resources/backend"
cat > "$TEST_APP/Resources/backend/photo-curator-backend" <<'PY'
#!/usr/bin/python3
import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
lock = threading.Lock()
def reply(request):
    time.sleep((request.get('params', {}).get('value', 0) % 7) * .001)
    with lock:
        print(json.dumps({'schema_version': 1, 'id': request['id'],
                          'result': request.get('params', {})}), flush=True)
with ThreadPoolExecutor(max_workers=32) as pool:
    for line in sys.stdin:
        request = json.loads(line)
        if request['method'] == 'shutdown':
            reply(request)
            break
        pool.submit(reply, request)
PY
chmod +x "$TEST_APP/Resources/backend/photo-curator-backend"
xcrun swiftc -swift-version 5 -parse-as-library -O \
  "$SCRIPT_DIR/NativeIPC.swift" "${NATIVE_WORKER_CLIENT_SOURCE:-$SCRIPT_DIR/NativeWorkerClient.swift}" \
  "$SCRIPT_DIR/NativeIPCBehavior.swift" -o "$TEST_APP/MacOS/IPCBehavior"
"$TEST_APP/MacOS/IPCBehavior"
