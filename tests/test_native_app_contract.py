from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP_SOURCES = (
    "PhotoCuratorApp.swift",
    "PhotoCuratorAppModel.swift",
    "PhotoCuratorSeriesModel.swift",
    "PhotoCuratorSelectionModel.swift",
    "PhotoCuratorHelpViews.swift",
    "PhotoCuratorRootView.swift",
    "PhotoCuratorGalleryViews.swift",
    "PhotoCuratorSettingsView.swift",
    "PhotoCuratorQualityWizardView.swift",
    "PhotoCuratorQualityModel.swift",
)


def native_app_source() -> str:
    return "\n".join((ROOT / "packaging/macos" / filename).read_text() for filename in APP_SOURCES)


def test_native_app_is_split_into_bounded_feature_modules() -> None:
    limits = {
        "PhotoCuratorApp.swift": 200,
        "PhotoCuratorAppModel.swift": 2_000,
        "PhotoCuratorSeriesModel.swift": 350,
        "PhotoCuratorSelectionModel.swift": 250,
        "PhotoCuratorHelpViews.swift": 600,
        "PhotoCuratorQualityWizardView.swift": 900,
        "PhotoCuratorQualityModel.swift": 300,
        "PhotoCuratorRootView.swift": 1_300,
        "PhotoCuratorGalleryViews.swift": 900,
    }
    makefile = (ROOT / "Makefile").read_text()
    build_script = (ROOT / "packaging/macos/build_app.sh").read_text()

    for filename, maximum_lines in limits.items():
        source = (ROOT / "packaging/macos" / filename).read_text()
        assert len(source.splitlines()) <= maximum_lines
        assert filename in makefile
        assert filename in build_script


def test_native_app_uses_swiftui_jsonl_worker_without_browser_or_localhost() -> None:
    app = native_app_source()
    worker = (ROOT / "packaging/macos/NativeWorkerClient.swift").read_text()
    ipc = (ROOT / "packaging/macos/NativeIPC.swift").read_text()
    worker_dtos = (ROOT / "packaging/macos/PhotoCuratorWorkerDTOs.swift").read_text()
    models = (ROOT / "packaging/macos/PhotoCuratorModels.swift").read_text()

    assert "NavigationSplitView" in app
    assert "LazyVGrid" in app
    assert "worker.request(method:" in app
    assert '"native-worker", "--demo"' in worker
    assert "PHOTO_CURATOR_NATIVE_DEMO" in worker
    assert "PHOTO_CURATOR_PHOTOKIT_HELPER" in worker
    assert "PHOTO_CURATOR_VISION_HELPER" in worker
    assert "PHOTO_CURATOR_PUBLISH_HELPER" in worker
    assert "NativeWorkerResponseEnvelope: Decodable" in ipc
    assert "JSONDecoder().decode(NativeWorkerResponseEnvelope.self" in worker
    assert "func request<Params: Encodable, Response: Decodable>" in worker
    assert "GalleryPageParams(" in app
    assert 'case focusAssetUUID = "focus_asset_uuid"' in worker_dtos
    assert '"asset_details"' in app
    assert "AssetIDParams(projectID:" in app
    assert '"series"' in app
    assert "SeriesParams(projectID:" in app
    assert "photos: model.workspacePhotos" in app
    assert "model.seriesMembers(groupID:" in app
    assert "detailRequestGeneration == requestGeneration" in app
    assert "maxLogBytes: UInt64 = 5 * 1024 * 1024" in worker
    assert "native-worker.previous.log" in worker
    native_worker = (ROOT / "src/photo_curator/native_worker.py").read_text()
    native_payloads = (ROOT / "src/photo_curator/native_payloads.py").read_text()
    assert "PhotoKitProvider.from_environment" in native_worker
    assert "OSXPhotosProvider" not in native_worker
    assert "legacy_cli_enabled=False" in native_worker
    assert '"reasons": reasons' in native_payloads
    assert '"confidence": asset.get("confidence")' in native_payloads
    assert '"taste_round_prepare"' in app
    assert '"taste_round_submit"' in app
    assert 'fromStage: "decisions"' in app
    assert "Настроить вкус" in app
    assert "Остальные останутся нейтральными" in app
    assert "TasteGridCard" in app
    assert "tasteRejectedIDs" in app
    assert "rejectedUUIDs: rejected" in app
    assert "прежде чем запускать первый анализ" in app
    assert "QuickLookController.shared.show" in app
    assert '.keyboardShortcut("p", modifiers: [])' in app
    assert '.keyboardShortcut("u", modifiers: [])' in app
    assert '.keyboardShortcut("x", modifiers: [])' in app
    assert ".keyboardShortcut(.space, modifiers: [])" in app
    assert "undoLastDecision" in app
    assert '"resume_analysis"' in app
    assert '"cancel_analysis"' in app
    assert "Продолжить с прерванного этапа" in app
    assert "Повторить проблемный этап" in app
    assert 'model.project?.state == "interrupted" || model.project?.state == "error"' in app
    assert "Остановить и сохранить прогресс" in app
    assert "Удалить анализ и его локальный кэш" in app
    assert 'private let retainedProjectDefaultsKey = "retainedProjectID"' in app
    assert 'DisclosureGroup("Детали этапов", isExpanded: $analysisDetailsExpanded)' in app
    assert "Размер итогового Best‑альбома" in app
    assert "Исходный альбом анализируется целиком" in app
    assert "анализ можно запустить и без него" in app
    workflow_step = app.split("enum WorkflowStep", 1)[1].split("enum SelectionBucket", 1)[0]
    assert "case .album" in workflow_step
    assert "case .taste" not in workflow_step
    assert ".allowsHitTesting(false)" in app
    assert "struct PhotoCard: View, Equatable" in app
    assert ".equatable()" in app
    assert "optimistic.disposition = disposition" in app
    assert "updateCachedPhoto(optimistic)" in app
    assert ".onTapGesture(count: 2, perform: openDetails)" not in app
    assert (
        ".onTapGesture" not in (ROOT / "packaging/macos/PhotoCuratorGalleryViews.swift").read_text()
    )
    assert "Button(action: select)" in app
    assert ".accessibilityAddTraits(selected ? .isSelected : [])" in app
    assert "PhotoDetailView" in app
    assert "togglePhotoSelection" in app
    assert "setSelectedPhotosDecision" in app
    assert '"decisions_batch"' in app
    assert "Переместить в отклонённые" in app
    assert "Вернуть в Best" in app
    assert 'title: "Отбор фотографий"' not in app
    assert "Проверьте две готовые подборки" not in app
    assert "Выбрать видимые" not in app
    assert "Отметьте фотографии галочками" not in app
    assert "selectAllVisiblePhotos" not in app
    assert "Решить позже" not in app
    assert "finalReview" not in app
    assert "let selectionCardMinimumWidth: CGFloat = 96" in app
    assert "let selectionCardDefaultWidth: CGFloat = 128" in app
    assert "@State private var galleryCardWidth: CGFloat = selectionCardDefaultWidth" in app
    assert 'Label("Размер фото", systemImage: "rectangle.grid.3x2")' in app
    assert 'Image(systemName: "minus.magnifyingglass")' in app
    assert 'Image(systemName: "plus.magnifyingglass")' in app
    assert "Slider(" in app
    assert "let selectionCardMaximumWidth: CGFloat = 260" in app
    assert ".adaptive(" in app
    assert ".aspectRatio(4 / 3, contentMode: .fit)" in app
    assert "systemImage: bucket.symbol" in app
    assert '"binary_decisions"' in app
    assert "selection: selectionBucket.rawValue" in app
    assert "SelectionBucket.allCases" in app
    assert '("keep", "Добавить в Best", "flag.fill", Color.green)' in app
    assert '("reject", "Отклонить", "xmark", Color.red)' in app
    assert 'let active = value == "keep" ? selection == "pick" : selection == value' in app
    assert '.accessibilityValue(active ? "Выбрано" : "")' in app
    assert 'autoSelection = value["auto_selection"]?.stringValue' in models
    assert 'Text("Причины рекомендации")' in app
    assert '"Решение пользователя"' in app
    assert '"Рекомендация движка"' in app
    assert r"решение: \(decisionTitle)" in app
    assert "currentDecisionModelVersion = 6" in app
    assert "Codex Vision · экспериментальный" in app
    assert "API key не используется" in app
    assert '"codex_status"' in app
    assert "modelDisagreement" in models
    assert "Расхождение моделей" in app
    assert "analysisMode: analysisMode" in app
    assert "engineApple: engineApple" in app
    assert "engineNIMA: engineNIMA" in app
    assert "engineMobileCLIP: engineMobileCLIP" in app
    assert "engineMUSIQ: engineMUSIQ" in app
    assert 'Toggle("Apple Vision", isOn: $model.engineApple)' in app
    assert 'Toggle("NIMA", isOn: $model.engineNIMA)' in app
    assert 'Toggle("MobileCLIP S0", isOn: $model.engineMobileCLIP)' in app
    assert 'Toggle("MUSIQ", isOn: $model.engineMUSIQ)' in app
    assert "ScrollView" in (ROOT / "packaging/macos/PhotoCuratorSettingsView.swift").read_text()
    assert ".frame(minWidth: 720, idealWidth: 780" in app
    assert "Обновляем критерии отбора" in app
    assert 'Label("Почему?"' not in app
    assert 'Button("Открыть детали", action: openDetails)' in app
    assert 'Image(systemName: "info.circle.fill")' in app
    assert "Создать Best‑альбом" in app
    assert '"delete_project"' in app
    assert "Отменить новый анализ" in app
    assert "ForEach(model.projects)" in app
    assert "ToolbarItemGroup(placement: .primaryAction)" in app
    assert "TasteProfileEditorView" in app
    assert '"cleanup_abandoned_projects"' not in app
    assert '"taste_round_cancel"' in app
    assert "Сменить альбом" in app
    assert "UserDefaults.standard" in app
    assert '"taste_export"' in app
    assert '"quality_export"' in app
    assert '"quality_evaluate"' in app
    assert "photo-curator-labels.json" in app
    assert "photo-curator-swipe-scores.json" in app
    assert "Автоматические решения и найденные сервисом дубли не копируются" in app
    assert "Оценить заполненный набор…" in app
    assert '"quality_top_k"' in app
    assert '"quality_series"' in app
    assert '"quality_custom_series"' in app
    assert '"quality_status"' in app
    assert "Это лучший кадр серии" in app
    assert "Сохранить; текущий кадр — лидер" in app
    assert "Структура corpus готова" in app
    assert '"taste_status"' in app
    assert '"taste_reset"' in app
    assert "refreshDecisionsForTaste" in app
    assert "Статус обработки" in app
    assert "Codex Vision" in app
    assert "ProcessingStatRow" in app
    assert "tasteCalibrationExamples" in app
    assert "tasteHeldOutExamples" in app
    assert '"incompatible": "Нужна повторная настройка после обновления анализа"' in app
    assert "Удалить профиль и все сравнения" in app
    assert "PHOTO_CURATOR_DEVELOPER_TOOLS" in app
    assert "if model.qualityToolsEnabled" in app
    assert 'Toggle("Лаборатория качества", isOn: $model.qualityLabEnabled)' in app
    assert "showsQualityWizard = true" in app
    assert 'Button("Назад к настройкам", systemImage: "chevron.left")' in app
    assert "if model.isQualityBusy { ProgressView().controlSize(.small) }" in app
    assert "selectedAlbumIsTemporarilyUnavailable" in app
    assert "selectedAlbumIsAvailable" in app
    assert "model.albums + model.sharedAlbums" in app
    assert 'Section("Общие альбомы")' in app
    assert "qualityWizardActive = true" in app
    assert "!model.galleryShortcutsAllowed || model.photos.isEmpty" in app
    assert ".keyboardShortcut(.rightArrow, modifiers: [])" in app
    assert ".keyboardShortcut(.leftArrow, modifiers: [])" in app
    assert 'let codes = defectCodes.isEmpty ? ["other"]' in app
    assert "→ хорошее · ← плохое" in app
    assert "Сначала разрешите доступ к Photos" in app
    assert 'Text("Ранее выбранный альбом недоступен")' in app
    assert 'var tasteProfileApplies: Bool { tasteStatus == "ready" }' in app
    assert "Профиль вкуса активен:" in app
    assert '"photokit_acceptance_prepare"' in app
    assert '"photokit_acceptance_finalize"' in app
    assert "PhotoKitAcceptanceParams(projectID: project.id)" in app
    assert "Создать и удалить одноразовый Best‑альбом" in app
    assert "удаляет только созданный контейнер" in app
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
    app = native_app_source()

    assert "import Photos" in app
    assert "didCompleteOnboardingV1" in app
    assert "OnboardingView" in app
    assert "Продолжить и настроить вкус" in app
    assert "На следующем шаге macOS попросит доступ к Фото" in app
    assert "guard hasCompletedOnboarding" in app
    assert "requestPhotoLibraryAccess" in app
    assert "requestPhotoLibraryAccessFromUI" in app
    assert "PHPhotoLibrary.requestAuthorization(for: .readWrite)" in app
    assert "guard await requestPhotoLibraryAccess() else { return }" in app
    assert "photoAccessNeedsAction" in app
    assert "photoAccessCanRequest" in app
    assert "Разрешить доступ к Фото" in app
    assert "Открыть настройки доступа к Фото" in app
    assert "Privacy_Photos" in app
    assert "model.confirmCodexAnalysis()" in app


def test_native_progress_uses_fixed_stages_and_exposes_current_work() -> None:
    app = native_app_source()
    models = (ROOT / "packaging/macos/PhotoCuratorModels.swift").read_text()

    assert "private var stageOrder: [String]" in app
    codex_stages = (
        '["inventory", "previews", "metrics", "duplicates", "vision", '
        '"models", "codex", "scene_shadow", "decisions"]'
    )
    assert codex_stages in app
    assert '"scene_shadow": "Engine v3 строит эпизоды и сцены"' in app
    assert "Все этапы завершены" in app
    assert "completed / Double(stageOrder.count)" in app
    assert "Этап \\(stage) из \\(stageOrder.count)" in app
    assert "operationMessage" in app
    assert "Добавляем \\(publishPlan.itemCount) существующих фото в Best‑альбом" in app
    assert 'warnings = value["warnings"]' in models
    assert 'errors = value["errors"]' in models


def test_native_gallery_reports_and_repairs_missing_preview_files() -> None:
    app = native_app_source()
    models = (ROOT / "packaging/macos/PhotoCuratorModels.swift").read_text()
    pipeline = (ROOT / "packaging/macos/PhotoCuratorImagePipeline.swift").read_text()
    helper = (ROOT / "src/photo_curator/photos/native/photo_curator_photokit.swift").read_text()

    assert "unavailablePreviewFiles" in app
    assert "refreshUnavailablePreviewCount" in app
    assert 'analysisReady: $0.cacheState == "ready"' in app
    assert "guard preview.analysisReady, let path = preview.path else" in app
    assert 'cacheState = value["cache_state"]?.stringValue ?? "ready"' in models
    assert 'fromStage: "previews"' in app
    assert "Восстановить превью" in app
    assert 'codexConsentAction = "repair"' in app
    assert "private func performPreviewRepair()" in app
    repair = app[
        app.index("func repairUnavailablePreviews()") : app.index(
            "private func performPreviewRepair()"
        )
    ]
    assert "codexStatus?.ready" not in repair
    assert "refreshCodexStatus()" not in repair
    assert "codexConsentPending = true" in repair
    assert "photo.badge.exclamationmark" in pipeline
    assert "@Published var failed = false" in pipeline
    assert 'reviewRenderVersion = "review-v5-2048-q88-degraded-fallback"' in helper
    assert "reviewRenderIsAnalysisGrade(cached)" in helper
    assert "minimumAnalysisReviewShortEdge = 256" in helper
    assert "minimumAnalysisReviewLongEdge = 512" in helper
    assert "FileManager.default.removeItem(at: destination)" in helper
    assert "options.deliveryMode = .opportunistic" in helper
    assert "if isDegraded, let image" in helper
    assert "Используется локальный preview PhotoKit" in helper


def test_native_workflow_keeps_publish_behind_dry_run_and_confirmation() -> None:
    app = native_app_source()

    assert app.index('"publish_dry_run"') < app.index('"publish_apply"')
    assert "confirmed: true" in app
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
    app = native_app_source()
    helper = (ROOT / "src/photo_curator/photos/native/photo_curator_photokit.swift").read_text()
    publisher = (ROOT / "src/photo_curator/photos/native/photo_curator_publish.swift").read_text()

    assert "photo_curator_photokit.swift" in build
    assert "-framework Photos" in build
    assert "PhotoCuratorSource-Info.plist" not in build
    assert "PhotoCuratorPublish-Info.plist" not in build
    assert "PHAssetCollection.fetchAssetCollections" in helper
    assert "let manager = PHImageManager.default()" in helper
    assert "manager.requestImage" in helper
    assert "let stdoutLock = NSLock()" in helper
    assert "stdoutLock.lock()" in helper
    assert 'reviewRenderVersion = "review-v5-2048-q88-degraded-fallback"' in helper
    assert "maximumConcurrentRenders = 3" in helper
    assert "let timeout = allowNetwork ? 120.0 : 12.0" in helper
    assert "options.isSynchronous = false" in helper
    assert "options.isNetworkAccessAllowed = allowNetwork" in helper
    assert "Фото доступно только в iCloud" in helper
    assert "let workerCount = min(maximumConcurrentRenders, assets.count)" in helper
    assert "for _ in 0..<workerCount" in helper
    assert "let photoTotal = assets.reduce" in helper
    assert "DispatchSemaphore(value: maximumConcurrentRenders)" not in helper
    assert "while group.wait(timeout: .now() + 0.05) == .timedOut" in helper
    assert "RunLoop.current.run(" in helper
    assert 'case "asset-metadata-jsonl"' in helper
    assert "photokit-source-media-v3" in helper
    assert 'case "repair-assets-by-id"' in helper
    assert "allowNetwork: true" in helper
    assert "media_type" in helper
    assert "media_subtypes" in helper
    assert "modification_timestamp" in helper
    assert "source_revision" in helper
    assert "albumAssets(album).filter { $0.mediaType == .image }" in helper
    assert "targetSize: NSSize(width: 2048, height: 2048)" in helper
    assert "targetSize: NSSize(width: 2560, height: 2560)" not in helper
    assert "Photos.sqlite" not in helper
    assert "photo_curator_publish.swift" in build
    assert "duplicate_asset_identifiers" in publisher
    assert "PHAssetResourceManager.default().writeData" in publisher
    assert "PHAssetCreationRequest.forAsset()" in publisher
    assert "creation.addResource(with: .photo" in publisher
    assert "photokit-publish-reserved-album-v7" in publisher
    assert "destination_album_identifier" in publisher
    assert "reserve_album" in publisher
    assert "?? createAlbum" not in publisher
    assert "func performPhotoLibraryChanges" in publisher
    assert "PHPhotoLibrary.shared().performChanges(changes)" in publisher
    assert "performChangesAndWait" not in publisher
    assert "RunLoop.current.run(" in publisher
    assert "deletePhotoCuratorAcceptanceAlbumInHostApplication" in publisher
    assert "#if !GALLERY_BENCHMARK\nimport PhotoCuratorPublishHelper" in app
    assert "PhotoKit cleanup недоступен в benchmark harness" in app
    assert '"photokit_acceptance_prepare"' in app
    assert '"photokit_acceptance_finalize"' in app
    assert "pendingPhotoKitAcceptanceV1" in app
    assert "Завершить cleanup одноразового альбома" in app
    assert '"--delete-album"' in publisher
    assert "public func runPhotoCuratorSourceHelper" in helper
    assert "public func runPhotoCuratorPublishHelper" in publisher
    assert '"--photo-curator-source-helper"' in app
    assert '"--photo-curator-publish-helper"' in app
    assert 'firstIndex(of: "--photo-curator-source-helper")' in app
    assert 'firstIndex(of: "--photo-curator-publish-helper")' in app
    assert "-framework QuickLookUI" in build
    assert '"$RESOURCES/native/photo-curator-photokit"' in build
    assert "photo-curator-photokit-wrapper.sh" in build
    assert "PhotoCuratorSourceHelper.swiftmodule" in build
    assert "PhotoCuratorPublishHelper.swiftmodule" in build
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
    assert 'require_identifier "$SOURCE" "local.photo-curator.source-helper"' not in verifier
    assert 'require_identifier "$PUBLISH" "local.photo-curator.publish-helper"' not in verifier
    assert "PhotoKit source launcher does not exec the main TCC identity" in verifier
    assert "PhotoKit publish launcher does not exec the main TCC identity" in verifier
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
    assert 'hdiutil detach "$mount_point" -force -quiet' in dmg_verifier
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
    app = native_app_source()
    native_payloads = (ROOT / "src/photo_curator/native_payloads.py").read_text()
    worker_dtos = (ROOT / "packaging/macos/PhotoCuratorWorkerDTOs.swift").read_text()

    assert '"strong_aesthetics": "Сильное первое впечатление"' in models
    assert '"similar_scene": "Похожая сцена уже представлена"' in models
    assert '"possible_blur": "Недостаточная резкость"' in models
    assert '"poor_face_capture": "Лицо снято неразборчиво"' in models
    assert '"no_confirmed_defect": "Явных дефектов не найдено"' in models
    assert '"below_album_cutoff": "Уступает другим кадрам этого альбома"' in models
    assert '"weaker_duplicate": "Есть более удачный похожий кадр"' in models
    assert "duplicate_leader_uuid" in native_payloads
    assert '"duplicate_leader": asset.get("duplicate_leader")' in native_payloads
    assert "technical_defect_codes" in native_payloads
    assert "DecisionReasonsView" in app
    assert "RelatedPhotoPopover" in app
    assert "Это точная копия другого кадра" in models
    assert "Открыть другой экземпляр в «Хороших»" in app
    assert "AlbumReferencesPopover" in app
    assert 'case focusAssetUUID = "focus_asset_uuid"' in worker_dtos


def test_new_analysis_clears_stale_job_progress() -> None:
    app_model = (ROOT / "packaging/macos/PhotoCuratorAppModel.swift").read_text()
    create_and_analyze = app_model.split("func createAndAnalyze()", 1)[1].split(
        "func requestAnalysis()", 1
    )[0]

    assert "errorMessage = nil\n        jobs = []\n        photos = []" in create_and_analyze


def test_taste_profile_does_not_flicker_as_unconfigured_during_bootstrap() -> None:
    app_model = (ROOT / "packaging/macos/PhotoCuratorAppModel.swift").read_text()
    root_view = (ROOT / "packaging/macos/PhotoCuratorRootView.swift").read_text()

    assert '@Published var tasteStatus = "loading"' in app_model
    assert 'var tasteProfileLoading: Bool { tasteStatus == "loading" }' in app_model
    assert 'tasteStatus = "unavailable"' in app_model
    assert "else if model.tasteProfileLoading" in root_view
    assert 'Label("Проверяем профиль вкуса…"' in root_view


def test_thumbnail_decode_work_is_cancelled_and_repair_invalidates_cache() -> None:
    pipeline = (ROOT / "packaging/macos/PhotoCuratorImagePipeline.swift").read_text()
    app_model = (ROOT / "packaging/macos/PhotoCuratorAppModel.swift").read_text()
    series_model = (ROOT / "packaging/macos/PhotoCuratorSeriesModel.swift").read_text()

    assert "withTaskCancellationHandler" in pipeline
    assert "task.cancel()" in pipeline
    assert "cancelWaiter" in pipeline
    assert "continuation.resume(returning: false)" in pipeline
    assert "private static var inFlight" in pipeline
    assert "existing.priority.rawValue >= priority.rawValue" in pipeline
    assert "private static let thumbnailCache" in pipeline
    assert "private static let reviewCache" in pipeline
    assert "ThumbnailRequestIdentity" in pipeline
    assert "let maxPixelSize: Int" in pipeline
    assert "let cacheEpoch: Int" in pipeline
    assert "image = nil\n            currentRequestKey = requestKey" in pipeline
    assert "priority: TaskPriority = .userInitiated" in pipeline
    assert "priority: .utility" in pipeline
    assert "guard !Task.isCancelled else" in pipeline
    assert "static func invalidateAll()" in pipeline
    assert "ThumbnailLoader.invalidateAll()" in app_model
    assert "candidates[$0].reviewPath ?? candidates[$0].thumbnailPath" in series_model
    assert "func selectPhoto(photoID: String)" in series_model


def test_gallery_paging_keeps_navigation_and_mutations_in_the_current_page_context() -> None:
    app_model = (ROOT / "packaging/macos/PhotoCuratorAppModel.swift").read_text()
    series_model = (ROOT / "packaging/macos/PhotoCuratorSeriesModel.swift").read_text()
    root_view = (ROOT / "packaging/macos/PhotoCuratorRootView.swift").read_text()

    assert "func loadMorePhotosIfNeeded(currentPhotoID: String)" in app_model
    assert "loadMorePhotos(selectFirstNewPhoto: true)" in series_model
    assert "ScrollViewReader { galleryProxy in" in root_view
    assert "galleryProxy.scrollTo(photoID, anchor: .center)" in root_view
    assert "model.loadMorePhotosIfNeeded(currentPhotoID: photo.id)" in root_view
    assert "let mutationBucket = selectionBucket" in app_model
    assert "let mutationGalleryGeneration = galleryRequestGeneration" in app_model
    assert "galleryRequestGeneration == mutationGalleryGeneration" in app_model


def test_workspace_supports_alternatives_six_frame_survey_and_focus_safe_shortcuts() -> None:
    app = (ROOT / "packaging/macos/PhotoCuratorApp.swift").read_text()
    app_model = (ROOT / "packaging/macos/PhotoCuratorAppModel.swift").read_text()
    gallery = (ROOT / "packaging/macos/PhotoCuratorGalleryViews.swift").read_text()
    root = (ROOT / "packaging/macos/PhotoCuratorRootView.swift").read_text()

    assert 'model.setSelection(photoID: photoID, selection: "alternative")' in root
    assert '("alternative", "Оставить как альтернативу"' in gallery
    assert "displayed.count > 4 ? 3" in gallery
    assert "responder is NSTextView || responder is NSTextField" in app_model
    assert "performGalleryShortcut" in app
