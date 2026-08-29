# Интеграция с Apple Photos

## Provider contract

```python
class PhotosProvider(Protocol):
    def refresh_library(self) -> None: ...
    def get_current_library(self) -> PhotoLibrary: ...
    def list_regular_albums(self) -> list[PhotoAlbum]: ...
    def list_shared_albums(self) -> list[PhotoAlbum]: ...
    def list_assets(self, album_id: str) -> list[PhotoAsset]: ...
    def list_asset_metadata_with_progress(
        self, album_id: str, progress: Callable[[int, int], None]
    ) -> list[PhotoAsset]: ...
    def list_shared_assets(self, album_id: str) -> list[PhotoAsset]: ...
    def refresh_assets(self, asset_uuids: list[str]) -> list[PhotoAsset]: ...
    def asset_still_in_album(self, album_id: str, asset_uuid: str) -> bool: ...
```

## Native `PhotoKitProvider`

Установленное приложение использует bundled Swift helper и только публичный PhotoKit API:
читает regular/shared albums, получает локальные review-renders, повторно проверяет
membership перед публикацией и передаёт Python worker только JSON и пути внутри cache.
Публикация принятого набора также выполняется через отдельный PhotoKit helper.

Inventory не запрашивает пиксели: helper сначала передаёт UUID и метаданные альбома, поэтому
первый этап не блокируется на локальном render или загрузке из iCloud. На этапе previews
PhotoKit асинхронно подготавливает до трёх изображений одновременно и сразу выдаёт конечный
2048 px JPEG. Python переиспользует его как review-render без повторного JPEG-кодирования и
создаёт только thumbnail. Versioned thumbnail также хранится в общем cache и связывается
с project cache без повторного декодирования при следующем анализе.

Если iCloud не отдаёт полноразмерный render, helper может сохранить локальный opportunistic
preview только для отображения. Перед любым анализом pipeline fail-closed проверяет raster:
короткая сторона должна быть не меньше 256 px, длинная — не меньше 512 px. Более мелкий кадр
получает `cache_state=degraded`, остаётся видимым в галерее, но не передаётся в Apple Vision,
Core ML или Codex и не участвует в automatic Pick/Reject. Повторный Codex-анализ после
восстановления preview требует отдельного подтверждения в UI.

Render cache общий для проектов и альбомов. Его ключ включает asset UUID, PhotoKit
`modificationDate`, размеры и версию render-настроек: неизменённый кадр переиспользуется,
а отредактированный не получает устаревшую копию.

## Diagnostic `OSXPhotosProvider`

Используется только CLI `doctor` как read-only diagnostic adapter. Работает через публичные
Python API `osxphotos`; optional properties получать через capability checks и `getattr`.

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

Для нативного PhotoKit-проекта publisher:

1. получает local identifiers только из repository;
2. повторно валидирует membership и текущие решения;
3. сохраняет immutable dry-run;
4. присваивает dry-run уникальный reservation token в имени destination;
5. после явного подтверждения отдельно создаёт новый regular album публичным PhotoKit API,
   сохраняет его immutable local identifier и не переиспользует одноимённый album;
6. добавляет в зарезервированный album существующие `PHAsset` по local identifier без экспорта и создания
   новых library assets;
7. при retry обращается только к сохранённому identifier и fail-closed проверяет имя;
8. сохраняет число новых membership и audit результата.

Для legacy `OSXPhotosProvider` publisher:

1. получает UUIDs только из repository;
2. повторно валидирует их;
3. создаёт sorted one-UUID-per-line file;
4. вызывает документированный `osxphotos batch-edit` через argv list;
5. сначала выполняет dry-run;
6. сохраняет весь результат;
7. выполняет apply только после отдельного подтверждения.

Если `--add-to-album` или иной необходимый capability недоступен в текущей версии, publish отключается. Запрещены direct DB writes и UI scripting как fallback.

## Media policy

- Обычный `PHAssetMediaType.video` сохраняется только в immutable source snapshot для счётчика
  и provenance. Он не попадает в `assets`, preview/decode, Vision/Core ML/Codex, decisions,
  gallery или publish candidates.
- Live Photo считается фотографией: анализируется только текущий still render; motion resource
  не декодируется и не публикуется отдельно.
- Edited image анализируется по текущему PhotoKit render. `modificationDate`, adjustment state,
  media subtype и revision входят в snapshot/cache identity; изменение требует нового анализа.
- Animated image, burst coverage, hidden и iCloud-only остаются отдельными policy/acceptance
  пунктами roadmap; отсутствие такого evidence нельзя трактовать как готовность.

Для проекта из service-owned disk snapshot финальный `Best` использует тот же принцип
через отдельный нативный PhotoKit helper:

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

`photokit-acceptance --confirm-create-test-album` создаёт уникальный regular album, добавляет
один существующий `PHAsset`, проверяет отсутствие нового Photos asset, затем удаляет только
созданный альбом по его `localIdentifier` и подтверждает сохранность исходного asset. Cleanup
выполняется и при ошибке промежуточной membership-проверки; чужой альбом по имени не удаляется.
