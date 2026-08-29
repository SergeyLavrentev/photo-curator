# Operations и troubleshooting

## Doctor

CLI `doctor` относится к explicit legacy workflow и проверяет `osxphotos`, Photos Library,
`sips`, writable directories и loopback port. Нативное приложение проверяет доступность
bundled PhotoKit/Vision helpers и разрешение Photos непосредственно при запуске.

## Logs

```text
~/Library/Logs/PhotoCurator/photo-curator.log
```

Логировать stage/job/project context, warnings, subprocess argv и return codes. Не логировать изображения, GPS, лица и полный metadata dump.

## Cache

```text
~/Library/Caches/PhotoCurator/photokit-renders/
~/Library/Application Support/PhotoCurator/project-artifacts/<project-id>/
```

Первый путь — повторно получаемый PhotoKit cache, который macOS может вытеснить.
Второй — materialized review/thumbnail artifacts проекта, необходимые галерее и
Quality Lab. UI обнаруживает их отсутствие и предлагает безопасное восстановление
через PhotoKit. Удаление проекта удаляет оба service-owned каталога проекта.

## Typical issues

### Photos Library не обнаружена

- Открыть Photos.app.
- Проверить нужную library.
- Запустить doctor.
- Не пытаться вручную указывать внутреннюю DB без отдельной поддержки.

### Альбом Shared и не выбирается

Дождаться локальных renders и создать дисковую копию через Shared Album intake.

### Missing previews

Открыть исходный альбом в Photos, дождаться загрузки, нажать `Восстановить изображения`.

### HEIC conversion failure

Проверить `/usr/bin/sips`, сохранить stderr, попробовать derivative fallback.

### `osxphotos` unavailable in CLI doctor

Не применять direct DB или UI scripting workaround; нативная публикация использует PhotoKit.

### Source drift

Обновить inventory либо продолжить только с существующими assets. Publish всегда revalidates UUIDs.

### Interrupted job

После restart выбрать `Продолжить` либо `Перезапустить этап`.

## Backup guidance

Приложение не должно само копировать всю `.photoslibrary`. Пользователю рекомендовать обычный Time Machine/backup и отдельный тестовый альбом перед первым publish.
