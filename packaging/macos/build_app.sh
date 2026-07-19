#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
BUILD_ROOT="$PROJECT_ROOT/build/macos"
APP="$BUILD_ROOT/PhotoCurator.app"
CONTENTS="$APP/Contents"
RESOURCES="$CONTENTS/Resources"
BACKEND_DIST="$BUILD_ROOT/pyinstaller-dist"
BACKEND_WORK="$BUILD_ROOT/pyinstaller-work"
ICON_WORK="$BUILD_ROOT/icon-work"
SIGN_IDENTITY="${SIGN_IDENTITY:--}"
VERSION="$(sed -n 's/^version = "\([^"]*\)"/\1/p' "$PROJECT_ROOT/pyproject.toml" | head -1)"

if [[ -z "$VERSION" ]]; then
  echo "Не удалось определить версию приложения" >&2
  exit 1
fi

/bin/rm -rf "$BUILD_ROOT"
mkdir -p "$CONTENTS/MacOS" "$RESOURCES/backend" "$BACKEND_DIST" "$BACKEND_WORK" "$ICON_WORK"

cd "$PROJECT_ROOT"
uv run pyinstaller \
  --noconfirm \
  --clean \
  --distpath "$BACKEND_DIST" \
  --workpath "$BACKEND_WORK" \
  "$SCRIPT_DIR/backend.spec"

/usr/bin/ditto "$BACKEND_DIST/photo-curator-backend" "$RESOURCES/backend"
xcrun swiftc \
  -swift-version 5 \
  -parse-as-library \
  -O \
  -target "$(uname -m)-apple-macosx13.0" \
  -framework AppKit \
  "$SCRIPT_DIR/PhotoCuratorLauncher.swift" \
  -o "$CONTENTS/MacOS/PhotoCurator"

cp "$SCRIPT_DIR/Info.plist" "$CONTENTS/Info.plist"
/usr/libexec/PlistBuddy -c "Set :CFBundleShortVersionString $VERSION" "$CONTENTS/Info.plist"

/usr/bin/qlmanage -t -s 1024 -o "$ICON_WORK" "$SCRIPT_DIR/AppIcon.svg" >/dev/null 2>&1
ICON_SOURCE="$ICON_WORK/AppIcon.svg.png"
if [[ ! -f "$ICON_SOURCE" ]]; then
  echo "Не удалось отрисовать иконку приложения" >&2
  exit 1
fi
ICONSET="$ICON_WORK/PhotoCurator.iconset"
mkdir -p "$ICONSET"
for size in 16 32 128 256 512; do
  /usr/bin/sips -z "$size" "$size" "$ICON_SOURCE" --out "$ICONSET/icon_${size}x${size}.png" >/dev/null
  double=$((size * 2))
  /usr/bin/sips -z "$double" "$double" "$ICON_SOURCE" --out "$ICONSET/icon_${size}x${size}@2x.png" >/dev/null
done
/usr/bin/iconutil -c icns "$ICONSET" -o "$RESOURCES/PhotoCurator.icns"

if [[ "$SIGN_IDENTITY" == "-" ]]; then
  /usr/bin/codesign --force --deep --sign - "$APP"
else
  /usr/bin/codesign --force --deep --options runtime --timestamp --sign "$SIGN_IDENTITY" "$APP"
fi
/usr/bin/codesign --verify --deep --strict --verbose=2 "$APP"

echo "$APP"
