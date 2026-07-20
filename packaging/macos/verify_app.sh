#!/bin/bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
APP="${1:-$PROJECT_ROOT/build/macos/PhotoCurator.app}"
CONTENTS="$APP/Contents"
RESOURCES="$CONTENTS/Resources"

fail() {
  echo "Bundle verification failed: $*" >&2
  exit 1
}

require_executable() {
  [[ -x "$1" ]] || fail "missing executable: $1"
}

identifier_for() {
  /usr/bin/codesign -d --verbose=4 "$1" 2>&1 \
    | /usr/bin/sed -n 's/^Identifier=//p' \
    | /usr/bin/head -1
}

require_identifier() {
  local executable="$1"
  local expected="$2"
  local actual
  actual="$(identifier_for "$executable")"
  [[ "$actual" == "$expected" ]] \
    || fail "unexpected identifier for $executable: $actual (expected $expected)"
}

require_photos_entitlement() {
  local executable="$1"
  local entitlements
  entitlements="$(/usr/bin/codesign -d --entitlements :- "$executable" 2>/dev/null)"
  /usr/bin/grep -Eq \
    '<key>com.apple.security.personal-information.photos-library</key>.*<true/>' \
    <<<"$entitlements" \
    || fail "Photos Library entitlement is disabled: $executable"
}

[[ -d "$APP" ]] || fail "app not found: $APP"
/usr/bin/codesign --verify --deep --strict --verbose=2 "$APP"
/usr/bin/plutil -lint "$CONTENTS/Info.plist" >/dev/null

MAIN="$CONTENTS/MacOS/PhotoCurator"
BACKEND="$RESOURCES/backend/photo-curator-backend"
VISION="$RESOURCES/native/photo-curator-vision"
SOURCE="$RESOURCES/native/photo-curator-photokit"
PUBLISH="$RESOURCES/native/photo-curator-publish"

for executable in "$MAIN" "$BACKEND" "$VISION" "$SOURCE" "$PUBLISH"; do
  require_executable "$executable"
  /usr/bin/codesign --verify --strict --verbose=2 "$executable"
done

require_identifier "$MAIN" "local.photo-curator.app"
require_identifier "$SOURCE" "local.photo-curator.source-helper"
require_identifier "$PUBLISH" "local.photo-curator.publish-helper"
require_photos_entitlement "$MAIN"
require_photos_entitlement "$SOURCE"
require_photos_entitlement "$PUBLISH"

for helper in "$SOURCE" "$PUBLISH"; do
  helper_signature="$(/usr/bin/codesign -d --verbose=4 "$helper" 2>&1)"
  /usr/bin/grep -Eq '^Info.plist entries=[1-9][0-9]*$' <<<"$helper_signature" \
    || fail "PhotoKit helper has no signed embedded Info.plist: $helper"
done

for excluded in osxphotos photoscript utitools fastapi jinja2; do
  [[ ! -e "$RESOURCES/backend/_internal/$excluded" ]] \
    || fail "legacy dependency is bundled: $excluded"
done

if [[ -n "$(/usr/bin/find "$APP" -type f \( -name '*.html' -o -name '*.js' \) -print -quit)" ]]; then
  fail "web assets are present in the native bundle"
fi

size_kib="$(/usr/bin/du -sk "$APP" | /usr/bin/awk '{print $1}')"
[[ "$size_kib" -le 81920 ]] || fail "bundle is larger than 80 MiB: ${size_kib} KiB"

echo "Bundle verified: $APP (${size_kib} KiB)"
