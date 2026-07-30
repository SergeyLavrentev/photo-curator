#!/bin/bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
PKG="${1:-$PROJECT_ROOT/build/macos/PhotoCurator.pkg}"
PACKAGE_IDENTIFIER="local.photo-curator.installer"
BUNDLE_IDENTIFIER="local.photo-curator.app"

fail() {
  echo "Installer verification failed: $*" >&2
  exit 1
}

[[ -f "$PKG" ]] || fail "package not found: $PKG"
work_root="$(mktemp -d)"
trap '/bin/rm -rf "$work_root"' EXIT
expanded="$work_root/expanded"
/usr/sbin/pkgutil --expand "$PKG" "$expanded"

package_info="$expanded/PackageInfo"
[[ -f "$package_info" ]] || fail "PackageInfo is missing"
/usr/bin/grep -Fq "identifier=\"$PACKAGE_IDENTIFIER\"" "$package_info" \
  || fail "unexpected package identifier"
/usr/bin/grep -Fq 'install-location="/Applications"' "$package_info" \
  || fail "package does not install into /Applications"
/usr/bin/grep -Fq "id=\"$BUNDLE_IDENTIFIER\"" "$package_info" \
  || fail "Photo Curator bundle is missing from package metadata"

payload_files="$(/usr/sbin/pkgutil --payload-files "$PKG")"
/usr/bin/grep -Fqx './PhotoCurator.app/Contents/MacOS/PhotoCurator' <<<"$payload_files" \
  || fail "main executable is missing from package payload"
/usr/bin/grep -Fqx './PhotoCurator.app/Contents/Resources/backend/photo-curator-backend' \
  <<<"$payload_files" \
  || fail "native backend is missing from package payload"

size_kib="$(/usr/bin/du -k "$PKG" | /usr/bin/awk '{print $1}')"
[[ "$size_kib" -gt 0 ]] || fail "package is empty"
echo "Installer verified: $PKG (${size_kib} KiB)"
