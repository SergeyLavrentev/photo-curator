# Compatibility

## Target

```text
macOS 13+
Python 3.12
Apple Silicon и Intel
```

## Capability-first policy

Версия OS или библиотеки — только сигнал. Реальная поддержка определяется smoke tests:

1. current library readable;
2. regular albums listable;
3. assets/UUID/metadata readable;
4. local render available;
5. dry-run add-to-album supported;
6. optional test album creation supported.

## macOS 26 и новые Photos schemas

Показывать warning и выполнять обязательный read gate. Не предполагать полную совместимость только потому, что import `osxphotos` успешен.

## Shared Albums

Не поддерживаются напрямую независимо от OS. Это intentional product scope, а не только техническое ограничение.

## HEIC

Primary fallback — системный `sips`, поэтому отдельный HEIF runtime не обязателен. Фактическую конвертацию проверить в doctor/smoke test.

## Publish degradation

Если publish capability отсутствует:

- inventory/analysis/review остаются доступны;
- Publish page объясняет ограничение;
- UUID list может быть сохранён как audit artifact;
- direct DB write и UI automation запрещены.

## Dependency policy

Версии фиксируются в `uv.lock`. Upgrade выполняется осознанно и сопровождается повторным compatibility test.
