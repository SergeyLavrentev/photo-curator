#!/bin/bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
APP="${1:-$PROJECT_ROOT/build/macos/PhotoCurator.app}"
OUTPUT="${2:-$PROJECT_ROOT/build/macos/PhotoCurator.dmg}"
STAGING="$PROJECT_ROOT/build/macos/dmg-staging"

fail() {
  echo "DMG build failed: $*" >&2
  exit 1
}

[[ -d "$APP" ]] || fail "app not found: $APP"
/bin/rm -rf "$STAGING"
/bin/rm -f "$OUTPUT"
mkdir -p "$STAGING"
COPYFILE_DISABLE=1 /usr/bin/ditto --norsrc --noextattr "$APP" "$STAGING/PhotoCurator.app"
/bin/ln -s /Applications "$STAGING/Applications"

/usr/bin/hdiutil create \
  -volname "Photo Curator" \
  -srcfolder "$STAGING" \
  -ov \
  -format UDZO \
  "$OUTPUT"
/bin/rm -rf "$STAGING"
bash "$PROJECT_ROOT/packaging/macos/verify_dmg.sh" "$OUTPUT"

echo "$OUTPUT"
