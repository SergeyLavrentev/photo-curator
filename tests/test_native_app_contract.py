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
    assert "maxLogBytes: UInt64 = 5 * 1024 * 1024" in worker
    assert "native-worker.previous.log" in worker
    native_worker = (ROOT / "src/photo_curator/native_worker.py").read_text()
    assert "PhotoKitProvider.from_environment" in native_worker
    assert "OSXPhotosProvider" not in native_worker
    assert "legacy_cli_enabled=False" in native_worker
    assert '"taste_round_prepare"' in app
    assert 'call("taste_round_submit"' in app
    assert '"from_stage": "decisions"' in app
    assert "Настроить вкус" in app
    assert "Остальные останутся нейтральными" in app
    assert "TasteGridCard" in app
    assert "tasteRejectedIDs" in app
    assert '"rejected_uuids": rejected' in app
    assert "прежде чем запускать первый анализ" in app
    assert "QuickLookController.shared.show" in app
    assert '.keyboardShortcut("1", modifiers: [])' in app
    assert ".keyboardShortcut(.space, modifiers: [])" in app
    assert "undoLastDecision" in app
    assert 'call("resume_analysis"' in app
    assert 'call("cancel_analysis"' in app
    assert "Продолжить с прерванного этапа" in app
    assert "Остановить и сохранить прогресс" in app
    assert "Удалить анализ и его локальный кэш" in app
    assert 'private let retainedProjectDefaultsKey = "retainedProjectID"' in app
    assert 'DisclosureGroup("Детали этапов", isExpanded: $analysisDetailsExpanded)' in app
    assert "Размер итогового Best‑альбома" in app
    assert "Исходный альбом анализируется целиком" in app
    assert "Продолжить без персонализации" not in app
    workflow_step = app.split("enum WorkflowStep", 1)[1].split("enum SelectionBucket", 1)[0]
    assert "case .album" in workflow_step
    assert "case .taste" not in workflow_step
    assert ".allowsHitTesting(false)" in app
    assert "struct PhotoCard: View, Equatable" in app
    assert ".equatable()" in app
    assert "photos[initialIndex].disposition = disposition" in app
    assert ".onTapGesture(count: 2, perform: openDetails)" in app
    assert "PhotoDetailView" in app
    assert "togglePhotoSelection" in app
    assert "setSelectedPhotosDecision" in app
    assert 'call("decisions_batch"' in app
    assert "Переместить в плохие" in app
    assert "Переместить в хорошие" in app
    assert 'title: "Отбор фотографий"' not in app
    assert "Проверьте две готовые подборки" not in app
    assert "Выбрать видимые" not in app
    assert "Отметьте фотографии галочками" not in app
    assert "selectAllVisiblePhotos" not in app
    assert "Решить позже" not in app
    assert "finalReview" not in app
    assert "private let selectionCardMinimumWidth: CGFloat = 210" in app
    assert "private let selectionCardMaximumWidth: CGFloat = 260" in app
    assert ".adaptive(" in app
    assert ".aspectRatio(4 / 3, contentMode: .fit)" in app
    assert 'systemImage: bucket == .keep ? "checkmark.circle" : "xmark.circle"' in app
    assert 'call("binary_decisions"' in app
    assert '"disposition": selectionBucket.rawValue' in app
    assert "SelectionBucket.allCases" in app
    assert '"keep", "Хорошие", "checkmark", Color.green' in app
    assert '"reject", "Плохие", "xmark", Color.red' in app
    assert "currentDecisionModelVersion = 2" in app
    assert "Обновляем критерии отбора" in app
    assert 'Label("Почему?"' not in app
    assert 'Image(systemName: "info.circle.fill")' in app
    assert "Создать Best‑альбом" in app
    assert 'call("delete_project"' in app
    assert "Отменить новый анализ" in app
    assert "ForEach(model.projects)" in app
    assert "ToolbarItemGroup(placement: .primaryAction)" in app
    assert "TasteProfileEditorView" in app
    assert 'call("cleanup_abandoned_projects"' not in app
    assert 'call("taste_round_cancel"' in app
    assert "Сменить альбом" in app
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
    assert "PHOTO_CURATOR_DEVELOPER_TOOLS" in app
    assert "if model.developerToolsEnabled" in app
    assert ".accessibilityHint" in app
    assert 'Window("Справка Photo Curator", id: "photo-curator-help")' in app
    assert 'Button("Справка Photo Curator")' in app
    assert 'case .howItWorks: return "Как это работает"' in app
    assert "struct HowItWorksHelpView: View" in app
    assert "struct AnalysisPipelineDiagram: View" in app
    assert "Swipe Score" in app
    assert "Технические дефекты" in app
    assert "Apple Vision" in app
    assert "struct AppleVisionHelpView: View" in app
    assert "VNCalculateImageAestheticsScoresRequest" in app
    assert "VNGenerateImageFeaturePrintRequest" in app
    assert "VNGenerateAttentionBasedSaliencyImageRequest" in app
    assert "VNDetectFaceLandmarksRequest + VNDetectFaceCaptureQualityRequest" in app
    assert "Что анализируем" in app
    assert "Движок и фреймворк" in app
    assert "Ваше ручное решение всегда имеет приоритет" in app
    assert "localhost" not in app + worker
    assert "127.0.0.1" not in app + worker


def test_native_app_explains_first_run_before_requesting_photos_permission() -> None:
    app = (ROOT / "packaging/macos/PhotoCuratorApp.swift").read_text()

    assert "import Photos" in app
    assert "didCompleteOnboardingV1" in app
    assert "OnboardingView" in app
    assert "Продолжить и настроить вкус" in app
    assert "На следующем шаге macOS попросит доступ к Фото" in app
    assert "guard hasCompletedOnboarding" in app
    assert "requestPhotoLibraryAccess" in app
    assert "PHPhotoLibrary.requestAuthorization(for: .readWrite)" in app
    assert "guard await requestPhotoLibraryAccess() else { return }" in app
    assert "photoAccessNeedsAction" in app
    assert "Открыть настройки доступа к Фото" in app
    assert "Privacy_Photos" in app


def test_native_progress_uses_fixed_stages_and_exposes_current_work() -> None:
    app = (ROOT / "packaging/macos/PhotoCuratorApp.swift").read_text()
    models = (ROOT / "packaging/macos/PhotoCuratorModels.swift").read_text()

    assert 'private let stageOrder = ["inventory", "previews", "metrics"' in app
    assert "completed / Double(stageOrder.count)" in app
    assert "Этап \\(stage) из \\(stageOrder.count)" in app
    assert "operationMessage" in app
    assert "Создаём \\(publishPlan.itemCount) независимых копий в Photos" in app
    assert 'warnings = value["warnings"]' in models
    assert 'errors = value["errors"]' in models


def test_native_workflow_keeps_publish_behind_dry_run_and_confirmation() -> None:
    app = (ROOT / "packaging/macos/PhotoCuratorApp.swift").read_text()

    assert app.index('call("publish_dry_run"') < app.index('call("publish_apply"')
    assert '"confirmed": true' in app
    assert "confirmationDialog" in app


def test_frozen_worker_excludes_legacy_web_and_osxphotos_runtime() -> None:
    spec = (ROOT / "packaging/macos/backend.spec").read_text()

    assert "native_backend_main.py" in spec
    assert "fastapi" not in spec
    assert "jinja2" not in spec
    assert '"osxphotos"' in spec
    assert "collect_all" not in spec
    assert "collect_submodules" not in spec


def test_legacy_web_source_and_runtime_dependencies_are_removed() -> None:
    pyproject = (ROOT / "pyproject.toml").read_text()
    cli = (ROOT / "src/photo_curator/cli.py").read_text()

    assert not (ROOT / "src/photo_curator/app.py").exists()
    assert not (ROOT / "src/photo_curator/web").exists()
    assert "legacy-web" not in cli
    for dependency in ("fastapi", "jinja2", "python-multipart", "uvicorn"):
        assert f'"{dependency}"' not in pyproject


def test_native_bundle_compiles_public_photokit_source_helper() -> None:
    build = (ROOT / "packaging/macos/build_app.sh").read_text()
    helper = (ROOT / "src/photo_curator/photos/native/photo_curator_photokit.swift").read_text()
    publisher = (ROOT / "src/photo_curator/photos/native/photo_curator_publish.swift").read_text()

    assert "photo_curator_photokit.swift" in build
    assert "-framework Photos" in build
    assert "PhotoCuratorSource-Info.plist" in build
    assert "PHAssetCollection.fetchAssetCollections" in helper
    assert "let manager = PHImageManager.default()" in helper
    assert "manager.requestImage" in helper
    assert 'reviewRenderVersion = "review-v2-2048-q88"' in helper
    assert "maximumConcurrentRenders = 3" in helper
    assert "reviewRenderTimeoutSeconds = 120.0" in helper
    assert "options.isSynchronous = false" in helper
    assert "let workerCount = min(maximumConcurrentRenders, assets.count)" in helper
    assert "for _ in 0..<workerCount" in helper
    assert "let photoTotal = assets.reduce" in helper
    assert "DispatchSemaphore(value: maximumConcurrentRenders)" not in helper
    assert 'case "asset-metadata-jsonl"' in helper
    assert "targetSize: NSSize(width: 2048, height: 2048)" in helper
    assert "targetSize: NSSize(width: 2560, height: 2560)" not in helper
    assert "Photos.sqlite" not in helper
    assert "photo_curator_publish.swift" in build
    assert "duplicate_asset_identifiers" in publisher
    assert "PHAssetResourceManager.default().writeData" in publisher
    assert "PHAssetCreationRequest.forAsset()" in publisher
    assert "creation.addResource(with: .photo" in publisher
    assert "photokit-publish-duplicates-v2" in publisher
    assert "-framework QuickLookUI" in build
    assert '"$RESOURCES/native/photo-curator-photokit"' in build
    assert '--entitlements "$SCRIPT_DIR/Photos.entitlements"' in build


def test_build_and_notarization_never_use_keychain() -> None:
    makefile = (ROOT / "Makefile").read_text()
    build = (ROOT / "packaging/macos/build_app.sh").read_text()
    packaging_scripts = "\n".join(
        path.read_text() for path in (ROOT / "packaging" / "macos").glob("*.sh")
    )
    runtime_sources = "\n".join(
        path.read_text()
        for root in (ROOT / "src", ROOT / "packaging")
        for path in root.rglob("*")
        if path.suffix in {".py", ".swift", ".sh"}
    )
    build_surface = f"{makefile}\n{packaging_scripts}\n{runtime_sources}"

    assert "SIGN_IDENTITY ?= -" in makefile
    assert "local-signing-identity" not in makefile
    assert not (ROOT / "packaging/macos/local_signing_identity.sh").exists()
    assert "Photo Curator Local Development" not in build
    assert "/usr/bin/security import" not in build_surface
    assert "add-trusted-cert" not in build_surface
    assert "keychain" not in build_surface.lower()
    assert "secitem" not in build_surface.lower()
    assert "seckeychain" not in build_surface.lower()
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


def test_every_native_build_produces_and_installs_a_verified_dmg_without_privileges() -> None:
    makefile = (ROOT / "Makefile").read_text()
    build = (ROOT / "packaging/macos/build_app.sh").read_text()
    dmg_builder = (ROOT / "packaging/macos/build_dmg.sh").read_text()
    dmg_verifier = (ROOT / "packaging/macos/verify_dmg.sh").read_text()
    dmg_installer = (ROOT / "packaging/macos/install_dmg.sh").read_text()
    package_builder = (ROOT / "packaging/macos/build_installer.sh").read_text()
    package_verifier = (ROOT / "packaging/macos/verify_installer.sh").read_text()

    assert 'bash "$SCRIPT_DIR/build_dmg.sh"' in build
    assert "/usr/bin/hdiutil create" in dmg_builder
    assert 'ln -s /Applications "$STAGING/Applications"' in dmg_builder
    assert "/usr/bin/hdiutil attach" in dmg_verifier
    assert 'bash "$PROJECT_ROOT/packaging/macos/verify_app.sh"' in dmg_verifier
    assert "packaging/macos/install_dmg.sh" in makefile
    assert "/usr/bin/hdiutil attach" in dmg_installer
    assert "with administrator privileges" not in makefile
    assert "/usr/sbin/installer -pkg" not in makefile
    assert "sudo" not in dmg_installer
    assert "make pkg" in makefile
    assert "/usr/bin/pkgbuild" in package_builder
    assert '--component "$APP"' in package_builder
    assert '--install-location "/Applications"' in package_builder
    assert 'bash "$PROJECT_ROOT/packaging/macos/verify_installer.sh"' in package_builder
    assert "/usr/sbin/pkgutil --expand" in package_verifier
    assert "local.photo-curator.installer" in package_verifier
    assert "./PhotoCurator.app/Contents/MacOS/PhotoCurator" in package_verifier
    assert '/usr/bin/ditto "$(APP)" "$(INSTALLED_APP)"' not in makefile


def test_notarization_requires_developer_id_and_api_key_file() -> None:
    helper = (ROOT / "packaging/macos/notarize_app.sh").read_text()
    makefile = (ROOT / "Makefile").read_text()

    assert "NOTARY_KEY" in makefile
    assert "NOTARY_KEY_ID" in makefile
    assert "NOTARY_ISSUER_ID" in makefile
    assert "notarytool submit" in helper
    assert '--key "$API_KEY"' in helper
    assert '--key-id "$API_KEY_ID"' in helper
    assert '--issuer "$API_ISSUER_ID"' in helper
    assert "--keychain-profile" not in helper
    assert "Developer ID Application:" in helper
    assert 'notarytool submit "$DMG"' in helper
    assert 'stapler staple "$DMG"' in helper
    assert "--type open" in helper
    assert "stapler staple" in helper
    assert "stapler validate" in helper
    assert "spctl --assess" in helper
    assert "APPLE_ID" not in helper
    assert "PASSWORD" not in helper


def test_native_selection_localizes_positive_and_negative_reasons() -> None:
    models = (ROOT / "packaging/macos/PhotoCuratorModels.swift").read_text()

    assert '"strong_aesthetics": "Сильное первое впечатление"' in models
    assert '"similar_scene": "Похожая сцена уже представлена"' in models
    assert '"possible_blur": "Недостаточная резкость"' in models
    assert '"below_album_cutoff": "Уступает другим кадрам этого альбома"' in models
    assert '"weaker_duplicate": "Есть более удачный похожий кадр"' in models
