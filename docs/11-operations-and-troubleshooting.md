# Operations и troubleshooting

## Doctor

Проверяет environment, `osxphotos`, Photos Library, `sips`, writable directories и loopback port.

## Logs

```text
~/Library/Logs/PhotoCurator/photo-curator.log
```

Логировать stage/job/project context, warnings, subprocess argv и return codes. Не логировать изображения, GPS, лица и полный metadata dump.

## Cache

```text
~/Library/Caches/PhotoCurator/<project-id>/
```

UI показывает размер и counts. Возможны очистка thumbnails, review previews, всего cache и удаление project state.

## Typical issues

### Photos Library не обнаружена

- Открыть Photos.app.
- Проверить нужную library.
- Запустить doctor.
- Не пытаться вручную указывать внутреннюю DB без отдельной поддержки.

### Альбом Shared и не выбирается

Импортировать элементы в личную медиатеку и создать обычный альбом.

### Missing previews

Открыть исходный альбом в Photos, дождаться загрузки, нажать `Повторить missing`.

### HEIC conversion failure

Проверить `/usr/bin/sips`, сохранить stderr, попробовать derivative fallback.

### `osxphotos` publish unavailable

Оставить проект в read-only review mode. Не применять direct DB или UI scripting workaround.

### Source drift

Обновить inventory либо продолжить только с существующими assets. Publish всегда revalidates UUIDs.

### Interrupted job

После restart выбрать `Продолжить` либо `Перезапустить этап`.

## Backup guidance

Приложение не должно само копировать всю `.photoslibrary`. Пользователю рекомендовать обычный Time Machine/backup и отдельный тестовый альбом перед первым publish.
