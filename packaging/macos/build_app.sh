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
SWIFT_MODULES="$BUILD_ROOT/swift-modules"
MODEL_SOURCE="$PROJECT_ROOT/.model-cache"
SIGN_IDENTITY="${SIGN_IDENTITY:--}"
VERSION="$(sed -n 's/^version = "\([^"]*\)"/\1/p' "$PROJECT_ROOT/pyproject.toml" | head -1)"

if [[ -z "$VERSION" ]]; then
  echo "Не удалось определить версию приложения" >&2
  exit 1
fi

for resource in \
  nima-inception-v2-ava.mlpackage \
  mobileclip_s0_image.mlpackage \
  mobileclip-prompts.json \
  musiq-koniq10k.mlpackage; do
  if [[ ! -e "$MODEL_SOURCE/$resource" ]]; then
    echo "Отсутствует ресурс локальной модели: $MODEL_SOURCE/$resource" >&2
    exit 1
  fi
done

mkdir -p "$BUILD_ROOT"
/bin/rm -rf \
  "$APP" "$BACKEND_DIST" "$BACKEND_WORK" "$ICON_WORK" "$SWIFT_MODULES" \
  "$BUILD_ROOT/PhotoCurator.dmg"
mkdir -p \
  "$CONTENTS/MacOS" "$RESOURCES/backend" "$BACKEND_DIST" "$BACKEND_WORK" \
  "$ICON_WORK" "$SWIFT_MODULES"
mkdir -p "$RESOURCES/native" "$RESOURCES/models"

cd "$PROJECT_ROOT"
uv run pyinstaller \
  --noconfirm \
  --clean \
  --distpath "$BACKEND_DIST" \
  --workpath "$BACKEND_WORK" \
  "$SCRIPT_DIR/backend.spec"

/usr/bin/ditto "$BACKEND_DIST/photo-curator-backend" "$RESOURCES/backend"
for resource in \
  nima-inception-v2-ava.mlpackage \
  mobileclip_s0_image.mlpackage \
  musiq-koniq10k.mlpackage; do
  /usr/bin/ditto "$MODEL_SOURCE/$resource" "$RESOURCES/models/$resource"
done
/bin/cp "$MODEL_SOURCE/mobileclip-prompts.json" "$RESOURCES/models/mobileclip-prompts.json"
/bin/cp "$PROJECT_ROOT/packaging/models/models.json" "$RESOURCES/models/models.json"
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
  -framework Vision \
  -framework CoreML \
  -framework AppKit \
  "$PROJECT_ROOT/src/photo_curator/analysis/native/photo_curator_local_models.swift" \
  -o "$RESOURCES/native/photo-curator-local-models"
xcrun swiftc \
  -swift-version 5 \
  -O \
  -parse-as-library \
  -target "$(uname -m)-apple-macosx13.0" \
  -framework Photos \
  -framework AppKit \
  "$PROJECT_ROOT/src/photo_curator/photos/native/photo_curator_photokit.swift" \
  -emit-library \
  -static \
  -emit-module \
  -module-name PhotoCuratorSourceHelper \
  -emit-module-path "$SWIFT_MODULES/PhotoCuratorSourceHelper.swiftmodule" \
  -o "$SWIFT_MODULES/libPhotoCuratorSourceHelper.a"
xcrun swiftc \
  -swift-version 5 \
  -O \
  -parse-as-library \
  -target "$(uname -m)-apple-macosx13.0" \
  -framework Photos \
  "$PROJECT_ROOT/src/photo_curator/photos/native/photo_curator_publish.swift" \
  -emit-library \
  -static \
  -emit-module \
  -module-name PhotoCuratorPublishHelper \
  -emit-module-path "$SWIFT_MODULES/PhotoCuratorPublishHelper.swiftmodule" \
  -o "$SWIFT_MODULES/libPhotoCuratorPublishHelper.a"
xcrun swiftc \
  -swift-version 5 \
  -parse-as-library \
  -O \
  -whole-module-optimization \
  -target "$(uname -m)-apple-macosx13.0" \
  -framework SwiftUI \
  -framework AppKit \
  -framework Photos \
  -framework QuickLookUI \
  -I "$SWIFT_MODULES" \
  "$SWIFT_MODULES/libPhotoCuratorSourceHelper.a" \
  "$SWIFT_MODULES/libPhotoCuratorPublishHelper.a" \
  "$SCRIPT_DIR/NativeIPC.swift" \
  "$SCRIPT_DIR/NativeWorkerClient.swift" \
  "$SCRIPT_DIR/PhotoCuratorModels.swift" \
  "$SCRIPT_DIR/PhotoCuratorWorkerDTOs.swift" \
  "$SCRIPT_DIR/PhotoCuratorImagePipeline.swift" \
  "$SCRIPT_DIR/PhotoCuratorSettingsView.swift" \
  "$SCRIPT_DIR/PhotoCuratorQualityWizardView.swift" \
  "$SCRIPT_DIR/PhotoCuratorQualityModel.swift" \
  "$SCRIPT_DIR/PhotoCuratorAppModel.swift" \
  "$SCRIPT_DIR/PhotoCuratorGalleryModel.swift" \
  "$SCRIPT_DIR/PhotoCuratorSeriesModel.swift" \
  "$SCRIPT_DIR/PhotoCuratorSelectionModel.swift" \
  "$SCRIPT_DIR/PhotoCuratorHelpViews.swift" \
  "$SCRIPT_DIR/PhotoCuratorRootView.swift" \
  "$SCRIPT_DIR/PhotoCuratorGalleryViews.swift" \
  "$SCRIPT_DIR/PhotoCuratorApp.swift" \
  -o "$CONTENTS/MacOS/PhotoCurator"

/usr/bin/ditto \
  "$SCRIPT_DIR/photo-curator-photokit-wrapper.sh" \
  "$RESOURCES/native/photo-curator-photokit"
/usr/bin/ditto \
  "$SCRIPT_DIR/photo-curator-publish-wrapper.sh" \
  "$RESOURCES/native/photo-curator-publish"
/bin/chmod 0755 \
  "$RESOURCES/native/photo-curator-photokit" \
  "$RESOURCES/native/photo-curator-publish"

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


# The PhotoKit launchers exec the main application binary so every Photos call
# uses the one TCC identity the user granted to Photo Curator.
/usr/bin/codesign "${SIGN_OPTIONS[@]}" "$RESOURCES/native/photo-curator-vision"
/usr/bin/codesign "${SIGN_OPTIONS[@]}" "$RESOURCES/native/photo-curator-local-models"
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

bash "$SCRIPT_DIR/verify_app.sh" "$APP"
bash "$SCRIPT_DIR/build_dmg.sh" "$APP" "$BUILD_ROOT/PhotoCurator.dmg"

echo "$APP"
echo "$BUILD_ROOT/PhotoCurator.dmg"
