# Product Requirements V2

## Product statement

Photo Curator превращает большой альбом Apple Photos в персональную подборку кадров,
которые этому пользователю хочется «свайпнуть вправо».

`Swipe Score` применим к портрету, пейзажу, семейному моменту, предмету или сцене. Он
оценивает силу фотографии и первого впечатления, а не ценность или красоту изображённого
человека и не обещает реакцию других людей.

## Primary job to be done

> Когда после поездки или съёмки у меня 2 000 похожих и неравноценных фотографий, я хочу
> быстро получить сильную, разнообразную подборку в моём вкусе, понять ключевые причины,
> поправить спорные решения и сохранить только подтверждённый результат в Photos.

## User journey

1. Выбрать поддерживаемый обычный альбом или создать service-owned disk snapshot общего.
2. При желании пройти короткую калибровку вкуса сравнением пар фотографий.
3. Запустить анализ и видеть честный общий прогресс с раскрываемыми деталями.
4. Получить галерею, упорядоченную по персональному Swipe Score и сериям.
5. Подкрутить ширину отбора, исправить спорные решения и увидеть обновлённый результат.
6. Проверить immutable dry-run и явно создать новый альбом Photos из принятого набора.

## Functional requirements

- Нативный SwiftUI/AppKit workflow без браузера и localhost в целевом релизе.
- PhotoKit для поддерживаемых источников и публикации; `osxphotos` — read-only adapter
  для недоступного публичным API enrichment и Shared Album intake.
- Versioned Swipe Score 0–100 с generic baseline, personal adjustment, confidence и
  краткими причинами.
- Apple Vision aesthetics, feature print, saliency и face signals как native baseline.
- Опциональные Core ML-модели допускаются только после измеримого uplift и проверки
  лицензии, размера, памяти, энергии и hardware execution.
- Поиск точных/визуальных дублей, формирование сцен и выбор лучшего кадра внутри серии.
- Технические дефекты как blockers, penalties и tie-breakers, но не главный критерий
  привлекательности.
- Локальный Personal Taste Profile, обучаемый на явных pairwise choices и подтверждённых
  правках; reset, export и полное удаление профиля.
- Разнообразие финального Top K, чтобы одна серия или один сюжет не заняли весь результат.
- Manual override, undo, keyboard navigation, Quick Look и state restoration.
- Safe publish: dry-run, source revalidation, explicit confirmation и новый Best-альбом.

## Non-functional requirements

- Полностью локальная обработка без cloud API и telemetry.
- Поддержка 5 000 фотографий с измеренными latency, peak memory и energy budgets.
- Core ML использует доступные CPU/GPU/Neural Engine через совместимые compute units.
- Один владелец mutable project state; Python worker не пишет состояние самостоятельно.
- Идемпотентные стадии, cancellation, resume и model/schema invalidation.
- Никаких автоматических destructive actions или direct Photos DB writes.
- Кодовая подпись обязательна; notarization — release gate распространения.

## Out of scope for V2

- Видеоанализ и оценка RAW-оригиналов.
- Облачный SaaS и синхронизация taste profile.
- Face identity/recognition.
- Автоматическое удаление или изменение исходного альбома.
- Обещание dating outcome или «объективной красоты».
- Несколько специализированных профилей до доказанного качества общего персонального.

## Success criteria

- Swipe Score статистически превосходит текущий technical-first baseline на held-out
  pairwise, best-in-series и Top-K evaluation.
- Personal Taste Profile улучшает согласие с тем же пользователем на отложенной выборке.
- Пользователь проходит Choose → Analyze → Review → Save в нативном приложении.
- Для каждого заметного решения доступны короткая причина, confidence и personal delta.
- Higher-resolution, missing, failed и ambiguous assets защищены от ложного исключения.
- Финальный Photos album в точности соответствует подтверждённому dry-run plan.
