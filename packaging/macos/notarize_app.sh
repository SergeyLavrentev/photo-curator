#!/bin/bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
APP="${1:-$PROJECT_ROOT/build/macos/PhotoCurator.app}"
API_KEY="${2:-}"
API_KEY_ID="${3:-}"
API_ISSUER_ID="${4:-}"
DIST_DIR="$PROJECT_ROOT/build/dist"

fail() {
  echo "Notarization failed: $*" >&2
  exit 1
}

[[ -d "$APP" ]] || fail "app not found: $APP"
[[ -f "$API_KEY" ]] || fail "set NOTARY_KEY to an App Store Connect API .p8 file"
[[ -n "$API_KEY_ID" ]] || fail "set NOTARY_KEY_ID to the App Store Connect key ID"
[[ -n "$API_ISSUER_ID" ]] || fail "set NOTARY_ISSUER_ID to the App Store Connect issuer ID"
command -v xcrun >/dev/null 2>&1 || fail "xcrun is unavailable"

signature="$(/usr/bin/codesign -d --verbose=4 "$APP" 2>&1)"
/usr/bin/grep -Eq '^Authority=Developer ID Application:' <<<"$signature" \
  || fail "the app is not signed with Developer ID Application"
team_id="$(/usr/bin/sed -n 's/^TeamIdentifier=//p' <<<"$signature" | /usr/bin/head -1)"
[[ -n "$team_id" && "$team_id" != "not set" ]] || fail "signed app has no TeamIdentifier"

bash "$PROJECT_ROOT/packaging/macos/verify_app.sh" "$APP"
version="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' "$APP/Contents/Info.plist")"
mkdir -p "$DIST_DIR"
submission="$DIST_DIR/PhotoCurator-${version}-submission.zip"
release="$DIST_DIR/PhotoCurator-${version}.zip"
/bin/rm -f "$submission" "$release"
/usr/bin/ditto -c -k --keepParent "$APP" "$submission"

xcrun notarytool submit "$submission" \
  --key "$API_KEY" \
  --key-id "$API_KEY_ID" \
  --issuer "$API_ISSUER_ID" \
  --wait
xcrun stapler staple "$APP"
xcrun stapler validate "$APP"
/usr/sbin/spctl --assess --type execute --verbose=2 "$APP"

/usr/bin/ditto -c -k --keepParent "$APP" "$release"
/bin/rm -f "$submission"
echo "$release"
