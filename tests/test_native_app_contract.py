from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_native_app_uses_swiftui_jsonl_worker_without_browser_or_localhost() -> None:
    app = (ROOT / "packaging/macos/PhotoCuratorApp.swift").read_text()
    worker = (ROOT / "packaging/macos/NativeWorkerClient.swift").read_text()

    assert "NavigationSplitView" in app
    assert "LazyVGrid" in app
    assert "worker.request(method:" in app
    assert '"native-worker", "--demo"' in worker
    assert "PHOTO_CURATOR_NATIVE_DEMO" in worker
    assert "PHOTO_CURATOR_PHOTOKIT_HELPER" in worker
    assert "PHOTO_CURATOR_VISION_HELPER" in worker
    assert "PHOTO_CURATOR_PUBLISH_HELPER" in worker
    native_worker = (ROOT / "src/photo_curator/native_worker.py").read_text()
    assert "PhotoKitProvider.from_environment" in native_worker
    assert "OSXPhotosProvider" not in native_worker
    assert 'call("taste_pair"' in app
    assert 'call("taste_preference"' in app
    assert '"from_stage": "decisions"' in app
    assert "Какой кадр вы бы оставили?" in app
    assert "QuickLookController.shared.show" in app
    assert '.keyboardShortcut("1", modifiers: [])' in app
    assert ".keyboardShortcut(.space, modifiers: [])" in app
    assert "undoLastDecision" in app
    assert 'call("resume_analysis"' in app
    assert 'call("cancel_analysis"' in app
    assert "Продолжить с прерванного этапа" in app
    assert "UserDefaults.standard" in app
    assert 'call("taste_export")' in app
    assert 'call("quality_export"' in app
    assert 'call("quality_evaluate"' in app
    assert "photo-curator-labels.json" in app
    assert "photo-curator-swipe-scores.json" in app
    assert "Автоматические решения и найденные сервисом дубли не копируются" in app
    assert "Оценить заполненный набор…" in app
    assert 'call("quality_top_k"' in app
    assert 'call("quality_series"' in app
    assert 'call("quality_custom_series"' in app
    assert 'call("quality_status"' in app
    assert "Это лучший кадр серии" in app
    assert "Сохранить; текущий кадр — лидер" in app
    assert "Структура corpus готова" in app
    assert 'call("taste_status"' in app
    assert 'call("taste_reset")' in app
    assert "refreshDecisionsForTaste" in app
    assert "tasteCalibrationExamples" in app
    assert "tasteHeldOutExamples" in app
    assert '"incompatible": "Нужна повторная настройка после обновления анализа"' in app
    assert "Удалить профиль и все сравнения" in app
    assert ".accessibilityHint" in app
    assert "localhost" not in app + worker
    assert "127.0.0.1" not in app + worker


def test_native_app_requests_photos_permission_before_worker_bootstrap() -> None:
    app = (ROOT / "packaging/macos/PhotoCuratorApp.swift").read_text()

    assert "import Photos" in app
    assert "requestPhotoLibraryAccess" in app
    assert "PHPhotoLibrary.requestAuthorization(for: .readWrite)" in app
    assert "guard await requestPhotoLibraryAccess() else { return }" in app
    assert "photoAccessNeedsAction" in app
    assert "Открыть настройки доступа к Фото" in app
    assert "Privacy_Photos" in app


def test_native_workflow_keeps_publish_behind_dry_run_and_confirmation() -> None:
    app = (ROOT / "packaging/macos/PhotoCuratorApp.swift").read_text()

    assert app.index('call("publish_dry_run"') < app.index('call("publish_apply"')
    assert '"confirmed": true' in app
    assert "confirmationDialog" in app


def test_frozen_worker_excludes_legacy_web_and_osxphotos_runtime() -> None:
    spec = (ROOT / "packaging/macos/backend.spec").read_text()

    assert "native_backend_main.py" in spec
    assert '"fastapi"' in spec
    assert '"jinja2"' in spec
    assert '"osxphotos"' in spec
    assert "collect_all" not in spec
    assert "collect_submodules" not in spec


def test_native_bundle_compiles_public_photokit_source_helper() -> None:
    build = (ROOT / "packaging/macos/build_app.sh").read_text()
    helper = (ROOT / "src/photo_curator/photos/native/photo_curator_photokit.swift").read_text()

    assert "photo_curator_photokit.swift" in build
    assert "-framework Photos" in build
    assert "PhotoCuratorSource-Info.plist" in build
    assert "PHAssetCollection.fetchAssetCollections" in helper
    assert "PHImageManager.default().requestImage" in helper
    assert "Photos.sqlite" not in helper
    assert "photo_curator_publish.swift" in build
    assert "-framework QuickLookUI" in build
    assert '"$RESOURCES/native/photo-curator-photokit"' in build
    assert '--entitlements "$SCRIPT_DIR/Photos.entitlements"' in build


def test_build_never_reads_or_mutates_login_keychain() -> None:
    makefile = (ROOT / "Makefile").read_text()
    build = (ROOT / "packaging/macos/build_app.sh").read_text()
    packaging_scripts = "\n".join(
        path.read_text() for path in (ROOT / "packaging" / "macos").glob("*.sh")
    )
    build_surface = f"{makefile}\n{packaging_scripts}"

    assert "SIGN_IDENTITY ?= -" in makefile
    assert "local-signing-identity" not in makefile
    assert not (ROOT / "packaging/macos/local_signing_identity.sh").exists()
    assert "Photo Curator Local Development" not in build
    assert "security import" not in build_surface
    assert "add-trusted-cert" not in build_surface
    assert "login.keychain" not in build_surface
    assert "--timestamp --sign" in build
    entitlements = (ROOT / "packaging/macos/Photos.entitlements").read_text()
    assert "com.apple.security.personal-information.photos-library" in entitlements


def test_bundle_verifier_guards_tcc_identity_and_native_only_contents() -> None:
    verifier = (ROOT / "packaging/macos/verify_app.sh").read_text()
    makefile = (ROOT / "Makefile").read_text()

    assert 'require_identifier "$MAIN" "local.photo-curator.app"' in verifier
    assert 'require_identifier "$SOURCE" "local.photo-curator.source-helper"' in verifier
    assert 'require_identifier "$PUBLISH" "local.photo-curator.publish-helper"' in verifier
    assert "PhotoKit helper has no signed embedded Info.plist" in verifier
    assert "require_photos_entitlement" in verifier
    assert "Photos Library entitlement is disabled" in verifier
    assert "web assets are present in the native bundle" in verifier
    assert "disable-library-validation" in verifier
    assert '"method":"albums"' in verifier
    assert '"method":"shutdown"' in verifier
    assert "frozen native worker smoke failed" in verifier
    assert 'bash packaging/macos/verify_app.sh "$(APP)"' in makefile


def test_notarization_requires_developer_id_and_keychain_profile() -> None:
    helper = (ROOT / "packaging/macos/notarize_app.sh").read_text()
    makefile = (ROOT / "Makefile").read_text()

    assert "NOTARY_PROFILE" in makefile
    assert "notarytool submit" in helper
    assert "--keychain-profile" in helper
    assert "Developer ID Application:" in helper
    assert "stapler staple" in helper
    assert "stapler validate" in helper
    assert "spctl --assess" in helper
    assert "APPLE_ID" not in helper
    assert "PASSWORD" not in helper


def test_native_review_localizes_swipe_reasons() -> None:
    models = (ROOT / "packaging/macos/PhotoCuratorModels.swift").read_text()

    assert '"strong_aesthetics": "Сильное первое впечатление"' in models
    assert '"similar_scene": "Похожая сцена уже представлена"' in models
