# Product Requirements

## Product statement

Photo Curator превращает большой обычный альбом Apple Photos в объяснимую подборку лучших кадров, сохраняя полный контроль человека.

## Primary persona

Технически грамотный владелец Mac и iPhone, использующий Apple Photos и iCloud Photos.
Пользователь хочет поручить приложению создание рабочей копии Shared Album и не хочет
поддерживать постоянные JPEG-папки или вручную собирать альбом в Photos.

## Primary job to be done

> Когда после поездки в одном альбоме оказывается множество фотографий от нескольких участников, я хочу быстро увидеть дубли и слабые кадры, проверить рекомендации визуально и безопасно удалить только подтверждённый мусор, сохранив один исходный альбом с хорошими фотографиями.

## User journey

1. Пользователь выбирает Shared Album и объём копии: первые N, отмеченные или все фото.
2. Проверяет план и подтверждает создание нового обычного Photos album.
3. Выбирает созданный working album для анализа.
4. Наблюдает pipeline и видит предупреждения.
5. Просматривает `Selected / Review / Excluded`, оценки и серии.
6. Исправляет решения и leaders.
7. Выполняет publish dry-run.
8. Создаёт новый Best-альбом.
9. При необходимости отдельно создаёт Reject-альбом.

## Functional requirements

- Regular album browser с folder path и количеством assets.
- Capability-gated частичная/полная фотокопия Shared Album с планом, подтверждением,
  повторным использованием дублей и явным пропуском видео.
- Project state и resumable pipeline.
- Preview cache без permanent originals.
- Technical metrics и perceptual similarity.
- Duplicate groups с объяснимым leader selection.
- `Selected/Review/Excluded`, оценка 0–100, flags, confidence и structured reasons.
- Manual override, batch actions, keyboard shortcuts.
- Компактный строковый pipeline и галерея результата.
- Capability-gated dry-run/apply Best- и опционального Reject-альбома.
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
- Распознавание личности и semantic composition.
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
- Финальный Best-альбом содержит только текущие final selected.
