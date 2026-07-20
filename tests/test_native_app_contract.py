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
    assert ".accessibilityHint" in app
    assert "NSWorkspace.shared.open" not in app + worker
    assert "localhost" not in app + worker
    assert "127.0.0.1" not in app + worker


def test_native_workflow_keeps_publish_behind_dry_run_and_confirmation() -> None:
    app = (ROOT / "packaging/macos/PhotoCuratorApp.swift").read_text()

    assert app.index('call("publish_dry_run"') < app.index('call("publish_apply"')
    assert '"confirmed": true' in app
    assert "confirmationDialog" in app


def test_frozen_worker_includes_osxphotos_uti_runtime_data() -> None:
    spec = (ROOT / "packaging/macos/backend.spec").read_text()

    assert 'collect_data_files("utitools")' in spec
    assert "osxphotos_data + utitools_data" in spec
    assert 'collect_data_files("photoscript")' in spec
    assert 'collect_data_files("osxmetadata")' in spec
    assert 'collect_submodules("bitstring")' in spec


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


def test_local_signing_identity_is_stable_and_optional() -> None:
    makefile = (ROOT / "Makefile").read_text()
    helper = (ROOT / "packaging/macos/local_signing_identity.sh").read_text()
    build = (ROOT / "packaging/macos/build_app.sh").read_text()

    assert "local-signing-identity" in makefile
    assert "Photo Curator Local Development" in helper
    assert "extendedKeyUsage = critical,codeSigning" in helper
    assert "security add-trusted-cert" in helper
    assert 'SIGN_IDENTITY" == "Photo Curator Local Development"' in build
    assert "--timestamp --sign" in build


def test_native_review_localizes_swipe_reasons() -> None:
    models = (ROOT / "packaging/macos/PhotoCuratorModels.swift").read_text()

    assert '"strong_aesthetics": "Сильное первое впечатление"' in models
    assert '"similar_scene": "Похожая сцена уже представлена"' in models
