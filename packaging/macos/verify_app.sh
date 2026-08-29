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
LOCAL_MODELS="$RESOURCES/native/photo-curator-local-models"
SOURCE="$RESOURCES/native/photo-curator-photokit"
PUBLISH="$RESOURCES/native/photo-curator-publish"

for executable in "$MAIN" "$BACKEND" "$VISION" "$LOCAL_MODELS"; do
  require_executable "$executable"
  /usr/bin/codesign --verify --strict --verbose=2 "$executable"
done
require_executable "$SOURCE"
require_executable "$PUBLISH"

for resource in \
  "$RESOURCES/models/nima-inception-v2-ava.mlpackage" \
  "$RESOURCES/models/mobileclip_s0_image.mlpackage" \
  "$RESOURCES/models/mobileclip-prompts.json" \
  "$RESOURCES/models/models.json" \
  "$RESOURCES/models/musiq-koniq10k.mlpackage"; do
  [[ -e "$resource" ]] || fail "missing local model resource: $resource"
done

/usr/bin/python3 "$PROJECT_ROOT/packaging/macos/verify_model_manifest.py" \
  "$RESOURCES/models" || fail "local model manifest verification failed"

require_identifier "$MAIN" "local.photo-curator.app"
require_photos_entitlement "$MAIN"

/usr/bin/grep -Fq -- '--photo-curator-source-helper' "$SOURCE" \
  || fail "PhotoKit source launcher does not exec the main TCC identity"
/usr/bin/grep -Fq -- '--photo-curator-publish-helper' "$PUBLISH" \
  || fail "PhotoKit publish launcher does not exec the main TCC identity"
[[ "$("$SOURCE" --capability)" == "photokit-source-media-v2" ]] \
  || fail "PhotoKit source capability is unavailable through the main app"
[[ "$("$PUBLISH" --capability)" == "photokit-publish-reserved-album-v7" ]] \
  || fail "PhotoKit publish capability is unavailable through the main app"

backend_entitlements="$(/usr/bin/codesign -d --entitlements :- "$BACKEND" 2>/dev/null)"
backend_team="$(/usr/bin/codesign -d --verbose=4 "$BACKEND" 2>&1 \
  | /usr/bin/sed -n 's/^TeamIdentifier=//p' | /usr/bin/head -1)"
if [[ -z "$backend_team" || "$backend_team" == "not set" ]]; then
  /usr/bin/grep -Eq \
    '<key>com.apple.security.cs.disable-library-validation</key>.*<true/>' \
    <<<"$backend_entitlements" \
    || fail "ad-hoc/local backend cannot load its signed Python framework"
fi

for excluded in osxphotos photoscript utitools fastapi jinja2; do
  [[ ! -e "$RESOURCES/backend/_internal/$excluded" ]] \
    || fail "legacy dependency is bundled: $excluded"
done

if [[ -n "$(/usr/bin/find "$APP" -type f \( -name '*.html' -o -name '*.js' \) -print -quit)" ]]; then
  fail "web assets are present in the native bundle"
fi

size_kib="$(/usr/bin/du -sk "$APP" | /usr/bin/awk '{print $1}')"
[[ "$size_kib" -le 327680 ]] || fail "bundle is larger than 320 MiB: ${size_kib} KiB"

SMOKE_HOME="$(mktemp -d)"
trap '/bin/rm -rf "$SMOKE_HOME"' EXIT
smoke_output="$(
  /usr/bin/printf '%s\n' \
    '{"schema_version":1,"id":"verify-albums","method":"albums","params":{}}' \
    '{"schema_version":1,"id":"verify-shutdown","method":"shutdown","params":{}}' \
  | HOME="$SMOKE_HOME" "$BACKEND" native-worker --demo
)" || fail "frozen native worker smoke failed"
/usr/bin/grep -Eq '"id":[[:space:]]*"verify-albums"' <<<"$smoke_output" \
  || fail "frozen native worker returned no albums response"
/usr/bin/grep -Eq '"id":[[:space:]]*"verify-shutdown"' <<<"$smoke_output" \
  || fail "frozen native worker did not shut down cleanly"

echo "Bundle verified: $APP (${size_kib} KiB)"
