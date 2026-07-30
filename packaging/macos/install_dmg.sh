#!/bin/bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
DMG="${1:-$PROJECT_ROOT/build/macos/PhotoCurator.dmg}"
INSTALL_DIR="${2:-/Applications}"
TARGET="$INSTALL_DIR/PhotoCurator.app"

fail() {
  echo "DMG installation failed: $*" >&2
  exit 1
}

[[ -f "$DMG" ]] || fail "image not found: $DMG"
[[ -d "$INSTALL_DIR" && -w "$INSTALL_DIR" ]] \
  || fail "$INSTALL_DIR is not writable; use INSTALL_DIR=\"\$HOME/Applications\""

work_root="$(mktemp -d)"
mount_point="$work_root/volume"
incoming="$INSTALL_DIR/.PhotoCurator.app.installing"
backup="$INSTALL_DIR/.PhotoCurator.app.previous"
mkdir -p "$mount_point"
mounted=0
cleanup() {
  if [[ "$mounted" == "1" ]]; then
    /usr/bin/hdiutil detach "$mount_point" -quiet || true
  fi
  if [[ -e "$incoming" ]]; then
    /bin/rm -rf "$incoming"
  fi
  /bin/rm -rf "$work_root"
}
trap cleanup EXIT

/usr/bin/hdiutil attach "$DMG" -readonly -nobrowse -mountpoint "$mount_point" -quiet
mounted=1
source_app="$mount_point/PhotoCurator.app"
[[ -d "$source_app" ]] || fail "PhotoCurator.app is missing from image"

/usr/bin/osascript -e 'tell application id "local.photo-curator.app" to quit' \
  >/dev/null 2>&1 || true
for _ in 1 2 3 4 5; do
  /usr/bin/pgrep -x PhotoCurator >/dev/null || break
  /bin/sleep 1
done
/usr/bin/pgrep -x PhotoCurator >/dev/null \
  && fail "running PhotoCurator did not stop"

/bin/rm -rf "$incoming"
/bin/rm -rf "$backup"
COPYFILE_DISABLE=1 /usr/bin/ditto --norsrc --noextattr "$source_app" "$incoming"
/usr/bin/codesign --verify --deep --strict --verbose=2 "$incoming"
if [[ -e "$TARGET" ]]; then
  /bin/mv "$TARGET" "$backup"
fi
if ! /bin/mv "$incoming" "$TARGET"; then
  if [[ -e "$backup" ]]; then
    /bin/mv "$backup" "$TARGET"
  fi
  fail "could not activate the new application"
fi
/bin/rm -rf "$backup"
/usr/bin/codesign --verify --deep --strict --verbose=2 "$TARGET"
/usr/bin/open "$TARGET"
echo "Installed from DMG: $TARGET"
