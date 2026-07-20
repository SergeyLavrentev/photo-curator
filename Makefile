SHELL := /bin/bash
.DEFAULT_GOAL := help

APP := $(CURDIR)/build/macos/PhotoCurator.app
INSTALL_DIR ?= /Applications
INSTALLED_APP := $(INSTALL_DIR)/PhotoCurator.app
SIGN_IDENTITY ?= $(shell bash packaging/macos/local_signing_identity.sh --print)

.PHONY: help sync test lint vision-helper local-signing-identity app build install run stop verify-app uninstall clean

help:
	@echo "Photo Curator"
	@echo "  make sync        установить Python-зависимости"
	@echo "  make test        запустить тесты"
	@echo "  make vision-helper собрать нативный Apple Vision benchmark"
	@echo "  make local-signing-identity создать стабильную локальную подпись"
	@echo "  make app         собрать и локально подписать .app"
	@echo "  make install     установить в $(INSTALL_DIR) и запустить"
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
	node --check src/photo_curator/web/static/app.js

vision-helper:
	mkdir -p "$(CURDIR)/build/native"
	xcrun swiftc -swift-version 5 -O \
	  -target "$$(uname -m)-apple-macosx13.0" \
	  -framework Vision -framework CoreVideo \
	  src/photo_curator/analysis/native/photo_curator_vision.swift \
	  -o "$(CURDIR)/build/native/photo-curator-vision"

local-signing-identity:
	bash packaging/macos/local_signing_identity.sh --create

app build:
	SIGN_IDENTITY="$(SIGN_IDENTITY)" packaging/macos/build_app.sh

verify-app:
	bash packaging/macos/verify_app.sh "$(APP)"

install: app
	mkdir -p "$(INSTALL_DIR)"
	@/usr/bin/osascript -e 'tell application id "local.photo-curator.app" to quit' >/dev/null 2>&1 || true
	@for attempt in 1 2 3 4 5; do pgrep -x PhotoCurator >/dev/null || break; sleep 1; done
	@if [[ -e "$(INSTALLED_APP)" ]]; then \
	  backup="$(INSTALLED_APP).previous"; \
	  /bin/rm -rf "$$backup"; \
	  mv "$(INSTALLED_APP)" "$$backup"; \
	  /usr/bin/ditto "$(APP)" "$(INSTALLED_APP)"; \
	  /bin/rm -rf "$$backup"; \
	else \
	  /usr/bin/ditto "$(APP)" "$(INSTALLED_APP)"; \
	fi
	/usr/bin/codesign --verify --deep --strict --verbose=2 "$(INSTALLED_APP)"
	/usr/bin/open "$(INSTALLED_APP)"

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
