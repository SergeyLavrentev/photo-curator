#!/bin/bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
APP="${1:-$PROJECT_ROOT/build/macos/PhotoCurator.app}"
OUTPUT="${2:-$PROJECT_ROOT/build/macos/PhotoCurator.pkg}"
INSTALLER_SIGN_IDENTITY="${INSTALLER_SIGN_IDENTITY:-}"

fail() {
  echo "Installer build failed: $*" >&2
  exit 1
}

[[ -d "$APP" ]] || fail "app not found: $APP"
version="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' "$APP/Contents/Info.plist")"
[[ -n "$version" ]] || fail "app version is missing"
mkdir -p "$(dirname "$OUTPUT")"
/bin/rm -f "$OUTPUT"

arguments=(
  --component "$APP"
  --identifier "local.photo-curator.installer"
  --version "$version"
  --install-location "/Applications"
)
if [[ -n "$INSTALLER_SIGN_IDENTITY" ]]; then
  arguments+=(--sign "$INSTALLER_SIGN_IDENTITY")
fi
COPYFILE_DISABLE=1 /usr/bin/pkgbuild "${arguments[@]}" "$OUTPUT"
bash "$PROJECT_ROOT/packaging/macos/verify_installer.sh" "$OUTPUT"

echo "$OUTPUT"
