# Safety и целостность данных

## Неподлежащие компромиссу инварианты

1. Приложение не удаляет Photos assets.
2. Приложение не пишет напрямую в Photos SQLite.
3. Приложение не меняет originals.
4. Приложение не меняет Favorite, keywords, title, description, location и date.
5. Regular PhotoKit publish добавляет существующие assets в новый Best album без дублей;
   только Shared/service-owned disk snapshot импортирует отдельные Best-копии.
6. Реальному publish всегда предшествует отдельный dry-run.
7. Каждый publish использует новый уникальный album name с reservation token; нативный helper
   отдельно создаёт пустой destination album, сохраняет его PhotoKit identifier и при retry
   обращается только к этому identifier. Одноимённый существующий album не переиспользуется.
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
- destination Photos album identifier для нативного импорта.

Удаление локального анализа имеет отдельный fail-closed контур:

- UI подтверждает действие, а worker дополнительно требует `confirmed=true`;
- перед `DELETE ... ON CASCADE` SQLite backup API создаёт согласованную копию в
  `~/Library/Application Support/PhotoCurator/backups` и проверяет её через
  `PRAGMA integrity_check`;
- если backup не создан или не прошёл проверку, проект не удаляется;
- запрос и успешное завершение пишутся в `destructive-actions.jsonl` с project ID,
  временем, PID и путём backup;
- одновременно один каталог может обслуживать только один native worker; второй процесс
  завершается до открытия БД.
- перед project cascade все Quality Lab human labels, pairwise choices, Top-K и series-budget
  truth копируются в durable learning corpus без FK на project/assets; неполная feature/model
  provenance или несовместимая schema блокирует удаление и сохраняет failed migration audit;
- project cascade удаляет только working projection Quality Lab. Durable attempts, immutable
  feature snapshots и обученный локальный ranker сохраняются.

Полное удаление накопленного обучения — отдельное глобальное destructive action:

- worker требует `confirmed=true` и до изменения создаёт проверенный SQLite backup;
- удаляются training/held-out contexts, все attempts, copied feature snapshots и активный ranker;
- проекты, Apple Photos и project review artifacts этим действием не изменяются;
- request/completion записываются в `destructive-actions.jsonl` как `delete_learning_data`.

## Source drift

Перед publish повторно проверить:

- существование asset;
- membership в source album;
- library fingerprint;
- блокирующие warnings.

Для disk snapshot вместо Photos membership и library fingerprint проверяются безопасная
принадлежность пути persistent root, размер и mtime файла.

Удалённые после scan UUID исключить, но сохранить в audit.

## Cache safety

- Повторно получаемые PhotoKit renders и transient cache находятся только внутри
  `~/Library/Caches/PhotoCurator` и могут быть вытеснены macOS.
- Review/thumbnail artifacts готового проекта находятся внутри
  `~/Library/Application Support/PhotoCurator/project-artifacts`; они нужны для
  галереи и разметки и удаляются только вместе с проектом.
- Persistent local albums только внутри
  `~/Library/Application Support/PhotoCurator/local_albums` и содержат atomic manifest.
- Recursive delete только после resolved-path validation.
- Atomic writes для previews.
- Source files только read-only.
- Удаление проекта удаляет service-owned Shared snapshot только если других проектов,
  использующих этот snapshot, нет; Photos assets и исходный Shared Album не затрагиваются.

## Пользовательское подтверждение

Перед apply показать:

```text
Будет создан новый обычный альбом. Для обычного Photos-проекта в него
добавляются существующие assets; для Shared snapshot импортируются отдельные
локальные копии. Фотографии не будут удалены, исходные файлы и metadata
не будут изменены.
```

## Destructive instruction

После publish отдельно предупредить, что `Command+Delete` удаляет asset из всей личной медиатеки, не только из working album.
