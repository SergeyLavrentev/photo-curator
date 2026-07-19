# Safety и целостность данных

## Неподлежащие компромиссу инварианты

1. Приложение не удаляет Photos assets.
2. Приложение не пишет напрямую в Photos SQLite.
3. Приложение не меняет originals.
4. Приложение не меняет Favorite, keywords, title, description, location и date.
5. Shared intake пишет только service-owned disk snapshot; отдельный publish может
   добавить подтверждённые existing assets в новый regular Photos album.
6. Реальному publish всегда предшествует отдельный dry-run.
7. Каждый publish использует новый уникальный album name.
8. Финальное удаление выполняет пользователь в Photos.app.

## Защищённые категории

Следующие assets не получают automatic reject:

- Favorite;
- edited;
- duplicate leader;
- missing preview;
- analysis error;
- member ambiguous duplicate group;
- asset в resolution inversion.

## Auditability

Сохранять:

- auto/final/manual dispositions;
- reasons и evidence;
- job state;
- dry-run stdout/stderr/return code;
- apply stdout/stderr/return code;
- UUID file path;
- publish album name и timestamp.

## Source drift

Перед publish повторно проверить:

- существование asset;
- membership в source album;
- library fingerprint;
- блокирующие warnings.

Удалённые после scan UUID исключить, но сохранить в audit.

## Cache safety

- Cache только внутри `~/Library/Caches/PhotoCurator`.
- Persistent local albums только внутри
  `~/Library/Application Support/PhotoCurator/local_albums` и содержат atomic manifest.
- Recursive delete только после resolved-path validation.
- Atomic writes для previews.
- Source files только read-only.

## Пользовательское подтверждение

Перед apply показать:

```text
Будет создан новый обычный альбом и в него будут добавлены ссылки
на существующие фотографии. Фотографии не будут удалены, originals
и metadata не будут изменены.
```

## Destructive instruction

После publish отдельно предупредить, что `Command+Delete` удаляет asset из всей личной медиатеки, не только из working album.
