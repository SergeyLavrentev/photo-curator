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
mkdir -p "$RESOURCES/native"

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
  -O \
  -target "$(uname -m)-apple-macosx13.0" \
  -framework Vision \
  -framework CoreVideo \
  "$PROJECT_ROOT/src/photo_curator/analysis/native/photo_curator_vision.swift" \
  -o "$RESOURCES/native/photo-curator-vision"
xcrun swiftc \
  -swift-version 5 \
  -O \
  -target "$(uname -m)-apple-macosx13.0" \
  -framework Photos \
  -framework AppKit \
  "$PROJECT_ROOT/src/photo_curator/photos/native/photo_curator_photokit.swift" \
  -o "$RESOURCES/native/photo-curator-photokit" \
  -Xlinker -sectcreate \
  -Xlinker __TEXT \
  -Xlinker __info_plist \
  -Xlinker "$PROJECT_ROOT/src/photo_curator/photos/native/PhotoCuratorSource-Info.plist"
xcrun swiftc \
  -swift-version 5 \
  -O \
  -target "$(uname -m)-apple-macosx13.0" \
  -framework Photos \
  "$PROJECT_ROOT/src/photo_curator/photos/native/photo_curator_publish.swift" \
  -o "$RESOURCES/native/photo-curator-publish" \
  -Xlinker -sectcreate \
  -Xlinker __TEXT \
  -Xlinker __info_plist \
  -Xlinker "$PROJECT_ROOT/src/photo_curator/photos/native/PhotoCuratorPublish-Info.plist"
xcrun swiftc \
  -swift-version 5 \
  -parse-as-library \
  -O \
  -target "$(uname -m)-apple-macosx13.0" \
  -framework SwiftUI \
  -framework AppKit \
  -framework Photos \
  -framework QuickLookUI \
  "$SCRIPT_DIR/NativeWorkerClient.swift" \
  "$SCRIPT_DIR/PhotoCuratorModels.swift" \
  "$SCRIPT_DIR/PhotoCuratorApp.swift" \
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
  SIGN_OPTIONS=(--force --options runtime --sign -)
else
  SIGN_OPTIONS=(--force --options runtime --timestamp --sign "$SIGN_IDENTITY")
fi


# Plain executables under Resources are not reliably discovered by `codesign --deep`.
# Sign them first so TCC can bind Photos access to their embedded identities.
/usr/bin/codesign "${SIGN_OPTIONS[@]}" "$RESOURCES/native/photo-curator-vision"
for executable in \
  "$RESOURCES/native/photo-curator-photokit" \
  "$RESOURCES/native/photo-curator-publish"; do
  /usr/bin/codesign "${SIGN_OPTIONS[@]}" \
    --entitlements "$SCRIPT_DIR/Photos.entitlements" \
    "$executable"
done
/usr/bin/codesign "${SIGN_OPTIONS[@]}" \
  --entitlements "$SCRIPT_DIR/Photos.entitlements" \
  "$CONTENTS/MacOS/PhotoCurator"
if [[ "$SIGN_IDENTITY" == "-" ]]; then
  /usr/bin/codesign "${SIGN_OPTIONS[@]}" --deep \
    --entitlements "$SCRIPT_DIR/Backend.entitlements" \
    "$RESOURCES/backend/photo-curator-backend"
else
  /usr/bin/codesign "${SIGN_OPTIONS[@]}" --deep "$RESOURCES/backend/photo-curator-backend"
fi
/usr/bin/codesign "${SIGN_OPTIONS[@]}" --deep \
  --entitlements "$SCRIPT_DIR/Photos.entitlements" \
  "$APP"
/usr/bin/codesign --verify --deep --strict --verbose=2 "$APP"

echo "$APP"
