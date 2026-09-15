# Product Requirements V2

## Product statement

После поездки пользователь хочет быстро убрать неудачные кадры и повторы из большого
альбома. Продукт предлагает конкретные фотографии к удалению и объясняет причину.

## Primary job to be done

> Проанализируй тысячи фотографий, покажи неудачные снимки и повторы, дай проверить их
> и удалить выбранные прямо из интерфейса. Остальное просто оставь.

## User journey

1. Выбрать альбом и запустить анализ.
2. Получить ровно две категории: «Оставить» и «К удалению».
3. Просмотреть причины, сравнить кадры серии, исправить спорное решение или отменить его.
4. При желании уточнить вкус слепым сравнением из готового анализа.
5. Проверить точный список удаления и отдельно подтвердить удаление из медиатеки Photos.

Best, Alternative, Review и размер будущей подборки не входят в основной пользовательский
workflow. Исторические ranking/publish contracts сохраняются для совместимости evidence.
«К удалению» — предложение, не автоматическое destructive action. Низкий score уникального
кадра сам по себе не основание для этой рекомендации.

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
- Визуальная избыточность серии: сохранить представителя и отличающиеся моменты.
- Manual override, undo, keyboard navigation, Quick Look и state restoration.
- Удаление через публичный PhotoKit API в host app: точный immutable список,
  source/membership revalidation, проверенный backup каталога, отдельное подтверждение,
  одноразовый запрос без автоматического повтора и проверка фактического отсутствия assets.
- Отдельный optional Codex Vision mode: disabled by default, explicit privacy/usage warning,
  automatic official CLI discovery, ChatGPT-auth-only preflight and resumable batches.

## Non-functional requirements

- Полностью локальная обработка без cloud API и telemetry по умолчанию; Codex Vision —
  единственный явный opt-in cloud boundary по ADR-0008.
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
