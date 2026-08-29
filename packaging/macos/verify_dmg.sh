#!/bin/bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
DMG="${1:-$PROJECT_ROOT/build/macos/PhotoCurator.dmg}"

fail() {
  echo "DMG verification failed: $*" >&2
  exit 1
}

[[ -f "$DMG" ]] || fail "image not found: $DMG"
work_root="$(mktemp -d)"
mount_point="$work_root/volume"
mkdir -p "$mount_point"
mounted=0
cleanup() {
  if [[ "$mounted" == "1" ]]; then
    for _ in 1 2 3; do
      if /usr/bin/hdiutil detach "$mount_point" -quiet; then
        mounted=0
        break
      fi
      /bin/sleep 1
    done
  fi
  if [[ "$mounted" == "1" ]]; then
    if /usr/bin/hdiutil detach "$mount_point" -force -quiet; then
      mounted=0
    else
      echo "DMG verification cleanup failed: volume is still mounted at $mount_point" >&2
      return 1
    fi
  fi
  /bin/rm -rf "$work_root"
}
trap cleanup EXIT

/usr/bin/hdiutil attach "$DMG" -readonly -nobrowse -mountpoint "$mount_point" -quiet
mounted=1
app="$mount_point/PhotoCurator.app"
[[ -d "$app" ]] || fail "PhotoCurator.app is missing"
[[ -L "$mount_point/Applications" ]] || fail "Applications shortcut is missing"
[[ "$(readlink "$mount_point/Applications")" == "/Applications" ]] \
  || fail "Applications shortcut has an unexpected target"
bash "$PROJECT_ROOT/packaging/macos/verify_app.sh" "$app"
if ! /usr/sbin/spctl --assess --type execute "$app" >/dev/null 2>&1; then
  signature="$(/usr/bin/codesign -d --verbose=4 "$app" 2>&1)"
  /usr/bin/grep -q '^TeamIdentifier=not set$' <<<"$signature" \
    || fail "Gatekeeper rejected a signed release application"
fi

size_kib="$(/usr/bin/du -k "$DMG" | /usr/bin/awk '{print $1}')"
[[ "$size_kib" -gt 0 ]] || fail "image is empty"
echo "DMG verified: $DMG (${size_kib} KiB)"
