# Интеграция с Apple Photos

## Provider contract

```python
class PhotosProvider(Protocol):
    def get_current_library(self) -> PhotoLibrary: ...
    def list_regular_albums(self) -> list[PhotoAlbum]: ...
    def list_assets(self, album_id: str) -> list[PhotoAsset]: ...
    def refresh_assets(self, asset_uuids: list[str]) -> list[PhotoAsset]: ...
    def asset_still_in_album(self, album_id: str, asset_uuid: str) -> bool: ...
```

## `OSXPhotosProvider`

Использует только публичные Python API `osxphotos`. Все optional properties получать через capability checks и `getattr`.

Не полагаться на:

- уникальность album name;
- наличие каждого score field;
- стабильность internal paths;
- прямую поддержку Shared Albums;
- одинаковое поведение на каждой версии macOS.

## Identity

```text
Library: resolved path + DB metadata fingerprint
Album: stable id, иначе folder path + name + snapshot hash
Asset: library fingerprint + asset UUID
```

## Render resolver

Edited:

1. edited path;
2. largest derivative;
3. original с warning.

Unedited:

1. original;
2. largest derivative;
3. system render fallback.

## Publish contract

Publisher:

1. получает UUIDs только из repository;
2. повторно валидирует их;
3. создаёт sorted one-UUID-per-line file;
4. вызывает документированный `osxphotos batch-edit` через argv list;
5. сначала выполняет dry-run;
6. сохраняет весь результат;
7. выполняет apply только после отдельного подтверждения.

Если `--add-to-album` или иной необходимый capability недоступен в текущей версии, publish отключается. Запрещены direct DB writes и UI scripting как fallback.

## Open in Photos

Допускается best-effort использование документированной команды `osxphotos show` либо `open -a Photos`. Ошибка открытия не влияет на project state.

## Capability gate

Проверить на отдельном тестовом asset/альбоме до полноценной разработки publisher.
