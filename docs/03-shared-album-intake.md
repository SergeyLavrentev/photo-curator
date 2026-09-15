# Общие альбомы: анализ и удаление публикаций

Основной native источник — прямой PhotoKit Shared Album. Каталог хранит идентификатор
альбома, `source_album_shared=true`, исходные UUID публикаций и `source_revision`.
Review renders сохраняются локально для анализа; это не импорт личных оригиналов в Photos.
Доступные через PhotoKit уменьшенные shared-версии могут отличаться от личного оригинала:
оценка резкости и разрешения относится к проверенной версии, а не к недоступному оригиналу.

## Удаление

Для прямого PhotoKit анализа план получает `scope=shared_album`. Подтверждение предупреждает:
публикации исчезнут у всех участников именно этого альбома, личные оригиналы и отдельно
сохранённые копии останутся. Восстановление из «Недавно удалённых» не обещается.

Native host проверяет текущий subtype, membership, source revision, cloud-shared source type
и `album.canPerform(.removeContent)`. При разрешении создаётся отдельный
`PHAssetCollectionChangeRequest` с `removeAssets`. `deleteAssets` для этого scope никогда
не вызывается. Нулевой change request, ошибка PhotoKit или отсутствующая capability — отказ,
а не успешное удаление. После callback проверяется membership того же альбома; только этот
shared-контекст обновляется в каталоге. Скрывать личный экземпляр по тому же UUID нельзя.

Владелец в Apple Photos может удалять любые публикации, участник — собственные. Эти правила
не доказывают доступ стороннему приложению: PhotoKit capability и callback остаются
обязательными. Поддержка запроса реализована, но успешное удаление общего альбома через API
на текущем Mac ещё не подтверждено. Если capability недоступна, удаление остаётся в Photos.

## Старые локальные копии

Legacy SharedCopyCoordinator создаёт service-owned working album с atomic manifest.
Такой источник остаётся доступен для анализа. Ни совпадение имени, ни сходство картинки не
используется для сопоставления с удаляемой публикацией. Для удаления нужен новый прямой
PhotoKit анализ; старые provenance не подменяются. Видео в legacy copy не копируются.

Не используются direct Photos.sqlite/file writes, private API, iCloud.com automation,
автоматизация разрушительных команд Photos или переключение System Photo Library.

Источники: [Shared Albums](https://support.apple.com/en-gb/108314),
[removeAssets](https://developer.apple.com/documentation/photos/phassetcollectionchangerequest/removeassets(_:)).

## Проверка 05.09.2026

302 теста, make lint, make app и make verify-app прошли. Подтверждение shared scope
визуально проверено в отдельном preview без проекта/worker; кнопка требует отметки
проверки. Основная сборка запущена. Доступ к Photos не предоставлялся, реальная
capability и успешный mutation Shared Album не подтверждены. Удаление пользовательских
assets не выполнялось, установленное приложение не заменено.
SHA-256 binary: `ec13d8f249745bbd57a4919e99f476015e7d4f13031cc7447d3d998a0674c020`. Evidence: `build/evidence/shared-albums-2026-09-05/result.json`.
