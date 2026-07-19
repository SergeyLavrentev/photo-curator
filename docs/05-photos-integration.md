# Интеграция с Apple Photos

## Provider contract

```python
class PhotosProvider(Protocol):
    def refresh_library(self) -> None: ...
    def get_current_library(self) -> PhotoLibrary: ...
    def list_regular_albums(self) -> list[PhotoAlbum]: ...
    def list_shared_albums(self) -> list[PhotoAlbum]: ...
    def list_assets(self, album_id: str) -> list[PhotoAsset]: ...
    def list_shared_assets(self, album_id: str) -> list[PhotoAsset]: ...
    def refresh_assets(self, asset_uuids: list[str]) -> list[PhotoAsset]: ...
    def asset_still_in_album(self, album_id: str, asset_uuid: str) -> bool: ...
```

## `OSXPhotosProvider`

Использует только публичные Python API `osxphotos`. Все optional properties получать через capability checks и `getattr`.

Не полагаться на:

- уникальность album name;
- наличие каждого score field;
- стабильность internal paths;
- стабильность Shared Album schemas и наличие полноразмерных originals;
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

Для обычного Photos-проекта publisher:

1. получает UUIDs только из repository;
2. повторно валидирует их;
3. создаёт sorted one-UUID-per-line file;
4. вызывает документированный `osxphotos batch-edit` через argv list;
5. сначала выполняет dry-run;
6. сохраняет весь результат;
7. выполняет apply только после отдельного подтверждения.

Если `--add-to-album` или иной необходимый capability недоступен в текущей версии, publish отключается. Запрещены direct DB writes и UI scripting как fallback.

Для проекта из service-owned disk snapshot финальный `Best` использует отдельный
нативный PhotoKit helper:

1. dry-run сохраняет неизменяемый список принятых UUID и не пишет в Photos;
2. apply доступен только после явного подтверждения;
3. перед apply повторно проверяются disposition, путь, размер и mtime каждого файла;
4. PhotoKit создаёт новый regular album и импортирует локальные image copies;
5. destination album identifier и результат импорта сохраняются в audit;
6. частично выполненный import можно безопасно повторить: уже добавленные имена файлов
   переиспользуются.

Для дискового проекта Reject не публикуется: это не финальный результат и его импорт
создавал бы лишние Photos assets. Direct DB writes, AppleScript и Photos UI не нужны.

## Shared copy contract

Shared intake отделён от анализа. Сервис сначала сохраняет план и требует отдельного
подтверждения, затем копирует только локально доступные photo renders в собственный
persistent-каталог и атомарно записывает manifest. `LocalAlbumsProvider` включает этот
snapshot в обычный album browser и pipeline. Видео и отсутствующие renders не подменяются
JPEG-превью; intake не использует Photos Library writes, AppleScript, PhotoKit writes или
UI scripting. PhotoKit используется позже только для отдельной подтверждённой публикации.

## Open in Photos

Допускается best-effort использование документированной команды `osxphotos show` либо `open -a Photos`. Ошибка открытия не влияет на project state.

## Capability gate

Проверить на отдельном тестовом asset/альбоме до полноценной разработки publisher.
