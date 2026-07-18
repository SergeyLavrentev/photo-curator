# Product Requirements

## Product statement

Photo Curator помогает пользователю безопасно очистить обычный альбом Apple Photos от дублей и явного брака, сохраняя полный контроль человека над финальным удалением.

## Primary persona

Технически грамотный владелец Mac и iPhone, использующий Apple Photos и iCloud Photos. Пользователь готов импортировать фотографии из Shared Album в личную медиатеку, но не хочет поддерживать постоянные JPEG-папки и стороннюю фотогалерею.

## Primary job to be done

> Когда после поездки в одном альбоме оказывается множество фотографий от нескольких участников, я хочу быстро увидеть дубли и слабые кадры, проверить рекомендации визуально и безопасно удалить только подтверждённый мусор, сохранив один исходный альбом с хорошими фотографиями.

## User journey

1. Пользователь вручную импортирует shared-фотографии в личную Photos Library.
2. Создаёт обычный рабочий альбом.
3. Запускает Photo Curator и выбирает этот альбом.
4. Наблюдает pipeline и видит предупреждения.
5. Просматривает `review`, duplicate groups и proposed rejects.
6. Исправляет решения и leaders.
7. Выполняет publish dry-run.
8. Создаёт временный Reject-альбом.
9. Ещё раз проверяет его в Photos.
10. Выполняет `Command+Delete` вручную.

## Functional requirements

- Regular album browser с folder path и количеством assets.
- Project state и resumable pipeline.
- Preview cache без permanent originals.
- Technical metrics и perceptual similarity.
- Duplicate groups с объяснимым leader selection.
- `keep/review/reject`, flags, confidence, structured reasons.
- Manual override, batch actions, keyboard shortcuts.
- Pipeline visualizer и summary cards.
- Capability-gated dry-run/apply временного Reject-альбома.
- Doctor, logging, cache cleanup и source drift validation.

## Non-functional requirements

- Полностью локальная обработка.
- Memory желательно менее 1 GB.
- Альбомы до 5000 фотографий.
- Отсутствие тяжёлого ML runtime.
- Работа без Node.js/Docker/Qt.
- Идемпотентные stages и resume.
- Никаких автоматических destructive actions.

## Out of scope

- Прямой анализ Shared Albums.
- Автоматическое удаление.
- Closed-eyes и semantic composition в MVP.
- Видео и RAW quality.
- Face recognition identities.
- Облачные API.
- Полноценный `.app` bundle и notarization.

## Success criteria

- Пользователь проходит весь workflow через GUI.
- Уверенные lower-quality duplicates выявляются корректно.
- Higher-resolution originals не проигрывают импортированным shared-копиям без явного ручного решения.
- Неоднозначные случаи направляются в review.
- Приложение не удаляет и не меняет metadata.
- Финальный Reject-альбом содержит только подтверждённые final rejects.
