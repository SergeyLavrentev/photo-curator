SHELL := /bin/bash
.DEFAULT_GOAL := help

APP := $(CURDIR)/build/macos/PhotoCurator.app
DMG := $(CURDIR)/build/macos/PhotoCurator.dmg
PKG := $(CURDIR)/build/macos/PhotoCurator.pkg
INSTALL_DIR ?= /Applications
INSTALLED_APP := $(INSTALL_DIR)/PhotoCurator.app
SIGN_IDENTITY ?= -
INSTALLER_SIGN_IDENTITY ?=
NOTARY_KEY ?=
NOTARY_KEY_ID ?=
NOTARY_ISSUER_ID ?=

.PHONY: help sync test lint vision-helper coreml-helper local-model-helper gallery-benchmark app build pkg install run stop verify-app verify-dmg verify-pkg notarize uninstall clean

help:
	@echo "Photo Curator"
	@echo "  make sync        установить Python-зависимости"
	@echo "  make test        запустить тесты"
	@echo "  make vision-helper собрать нативный Apple Vision benchmark"
	@echo "  make coreml-helper собрать optional Core ML benchmark"
	@echo "  make local-model-helper собрать движок NIMA, MobileCLIP и MUSIQ"
	@echo "  make gallery-benchmark измерить SwiftUI-галерею на 2k/5k карточек"
	@echo "  make app         собрать .app и стандартный PhotoCurator.dmg"
	@echo "  make install     установить из DMG без прав администратора и запустить"
	@echo "  make pkg         собрать optional admin/corporate .pkg"
	@echo "  make notarize    нотариально заверить release DMG"
	@echo "  make stop        завершить установленное приложение"
	@echo "  make uninstall   переместить установленное приложение в Корзину"
	@echo "  make clean       удалить build-артефакты"

sync:
	uv sync

test:
	uv run pytest

lint:
	uv run ruff format --check .
	uv run ruff check .

vision-helper:
	mkdir -p "$(CURDIR)/build/native"
	xcrun swiftc -swift-version 5 -O \
	  -target "$$(uname -m)-apple-macosx13.0" \
	  -framework Vision -framework CoreVideo \
	  src/photo_curator/analysis/native/photo_curator_vision.swift \
	  -o "$(CURDIR)/build/native/photo-curator-vision"

coreml-helper:
	mkdir -p "$(CURDIR)/build/native"
	xcrun swiftc -swift-version 5 -O \
	  -target "$$(uname -m)-apple-macosx13.0" \
	  -framework Vision -framework CoreML -framework AppKit \
	  src/photo_curator/analysis/native/photo_curator_coreml.swift \
	  -o "$(CURDIR)/build/native/photo-curator-coreml"

local-model-helper:
	mkdir -p "$(CURDIR)/build/native"
	xcrun swiftc -swift-version 5 -O \
	  -target "$$(uname -m)-apple-macosx13.0" \
	  -framework Vision -framework CoreML -framework AppKit \
	  src/photo_curator/analysis/native/photo_curator_local_models.swift \
	  -o "$(CURDIR)/build/native/photo-curator-local-models"

gallery-benchmark:
	mkdir -p "$(CURDIR)/build/evidence"
	xcrun swiftc -swift-version 5 -parse-as-library -O -whole-module-optimization \
	  -D GALLERY_BENCHMARK \
	  -target "$$(uname -m)-apple-macosx13.0" \
	  -framework SwiftUI -framework AppKit -framework Photos -framework QuickLookUI \
	  packaging/macos/NativeIPC.swift \
	  packaging/macos/NativeWorkerClient.swift \
	  packaging/macos/PhotoCuratorModels.swift \
	  packaging/macos/PhotoCuratorWorkerDTOs.swift \
	  packaging/macos/PhotoCuratorImagePipeline.swift \
	  packaging/macos/PhotoCuratorSettingsView.swift \
	  packaging/macos/PhotoCuratorQualityWizardView.swift \
	  packaging/macos/PhotoCuratorQualityModel.swift \
	  packaging/macos/PhotoCuratorAppModel.swift \
	  packaging/macos/PhotoCuratorSeriesModel.swift \
	  packaging/macos/PhotoCuratorHelpViews.swift \
	  packaging/macos/PhotoCuratorRootView.swift \
	  packaging/macos/PhotoCuratorGalleryViews.swift \
	  packaging/macos/PhotoCuratorApp.swift \
	  packaging/macos/GalleryBenchmark.swift \
	  -o "$(CURDIR)/build/evidence/gallery-benchmark"
	"$(CURDIR)/build/evidence/gallery-benchmark" \
	  > "$(CURDIR)/build/evidence/gallery-benchmark.json"
	@echo "$(CURDIR)/build/evidence/gallery-benchmark.json"

app build:
	SIGN_IDENTITY="$(SIGN_IDENTITY)" packaging/macos/build_app.sh

pkg: app
	INSTALLER_SIGN_IDENTITY="$(INSTALLER_SIGN_IDENTITY)" \
	packaging/macos/build_installer.sh "$(APP)" "$(PKG)"

verify-app:
	bash packaging/macos/verify_app.sh "$(APP)"

verify-dmg:
	bash packaging/macos/verify_dmg.sh "$(DMG)"

verify-pkg:
	bash packaging/macos/verify_installer.sh "$(PKG)"

notarize:
	bash packaging/macos/notarize_app.sh "$(APP)" "$(DMG)" "$(NOTARY_KEY)" "$(NOTARY_KEY_ID)" "$(NOTARY_ISSUER_ID)"

install: app
	packaging/macos/install_dmg.sh "$(DMG)" "$(INSTALL_DIR)"

run: app
	/usr/bin/open "$(APP)"

stop:
	@/usr/bin/osascript -e 'tell application id "local.photo-curator.app" to quit' >/dev/null 2>&1 || true

uninstall: stop
	@stamp="$$(date +%Y%m%d-%H%M%S)"; \
	if [[ -e "$(INSTALLED_APP)" ]]; then \
	  mkdir -p "$(HOME)/.Trash"; \
	  mv "$(INSTALLED_APP)" "$(HOME)/.Trash/PhotoCurator-$$stamp.app"; \
	  echo "Приложение перемещено в Корзину"; \
	else \
	  echo "Photo Curator не установлен"; \
	fi

clean:
	/bin/rm -rf "$(CURDIR)/build"
