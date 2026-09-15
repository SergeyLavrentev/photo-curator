# Safety и целостность данных

## Неподлежащие компромиссу инварианты

1. Анализ никогда не удаляет Photos assets. Удаление возможно только отдельным
   подтверждённым пользователем PhotoKit change request из native host app.
2. Приложение не пишет напрямую в Photos SQLite.
3. Приложение не редактирует original files напрямую. Подтверждённое удаление
   из всей медиатеки выполняет Photos; это не удаление только из текущего альбома.
4. Приложение не меняет Favorite, keywords, title, description, location и date.
5. Regular PhotoKit publish добавляет существующие assets в новый Best album без дублей;
   только Shared/service-owned disk snapshot импортирует отдельные Best-копии.
6. Реальному publish всегда предшествует отдельный dry-run.
7. Каждый publish использует новый уникальный album name с reservation token; нативный helper
   отдельно создаёт пустой destination album, сохраняет его PhotoKit identifier и при retry
   обращается только к этому identifier. Одноимённый существующий album не переиспользуется.
8. Новый основной workflow — «Оставить / К удалению». Best publish остаётся legacy API.

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
- исключение для legacy анализа без immutable album snapshot: вместо обучения создаётся
  проверенный архив полной БД и service-owned изображений в `legacy-learning-archives`.
  Архив не участвует в ротации обычных backups; SHA-256 и пути файлов записываются в manifest.
  При ошибке backup/copy/проверки проект остаётся. Недостающий snapshot не придумывается,
  архив не импортируется в обучение. UI сообщает путь архива и то, что он занимает место;
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


## Подтверждённое удаление фотографий (новый workflow 05.09.2026)

- Предложение culling хранится отдельно от исторических selection/disposition и не является
  разрешением удалять. Низкий aesthetic score не создаёт кандидата самостоятельно.
- Native UI показывает immutable список с количеством, именами и review-изображениями.
  Пользователь явно отмечает, что проверил список, и нажимает «Удалить N из медиатеки».
  В тексте подтверждения указаны все альбомы медиатеки и синхронизируемые iCloud устройства.
- План действует 15 минут. Before-begin сверяет категорию, manual generation, исходный revision,
  project/album identity; требует `confirmed=true` и создаёт проверенный SQLite backup.
- Явно выбранный UUID можно удалить из любой категории, в том числе Favorite/edited,
  после просмотра соответствующих отметок и подтверждения. Такой выбор не меняет decision.
  Запрос всей категории без списка UUID включает только текущие removal candidates.
  План фиксирует этот режим и оба manual decision поля; изменение решения после просмотра
  блокирует apply независимо от счётчика mutation generation.
- Native host повторно проверяет Photos permission, тип source album, точный membership,
  полный набор UUID, `canPerform(.delete)`, source revision и состояние Favorite/adjustments.
  Локальные копии без PhotoKit identity, stale source и неподдерживаемые assets блокируют операцию.
- Для scope=library после проверки выполняется `PHAssetChangeRequest.deleteAssets` внутри
  `PHPhotoLibrary.performChanges`. Для scope=shared_album применяется только
  `PHAssetCollectionChangeRequest.removeAssets` и проверяется `canPerform(.removeContent)`.
  Недоступность этого API не разрешает удалять личные оригиналы вместо публикаций.
  Scope фиксируется в immutable плане; изменение типа альбома блокирует begin.
  Сочетания Command+Delete не автоматизируются.
- План одноразовый. Отмена, ошибка или неизвестный исход не запускают автоматический retry.
  После успешного PhotoKit callback native host повторно читает UUID; в каталоге скрываются
  только реально отсутствующие assets. Для shared scope перечитывается membership исходного
  альбома, обновляются только его shared-проекты; личные и другие shared-контексты остаются.
  Review files, decisions и learning evidence сохраняются.
- Незавершённый после остановки процесса запрос требует проверки медиатеки и обновления
  анализа. SQLite backup восстанавливает каталог приложения, а не удалённые Photos assets.
- Реальное удаление пользовательских фото не входит в автоматическую acceptance-проверку.
  Для проверки используются синтетические данные и недеструктивные UI/preflight сценарии.

Публичный API: [Apple PHAssetChangeRequest](https://developer.apple.com/documentation/photos/phassetchangerequest).
