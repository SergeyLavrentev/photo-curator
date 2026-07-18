from __future__ import annotations

DEMO_STAGES = [
    {
        "number": 1,
        "name": "Проверка среды",
        "status": "done",
        "status_label": "Готово",
        "detail": "Demo provider активен, Photos не изменяется",
        "progress": 100,
    },
    {
        "number": 2,
        "name": "Инвентаризация",
        "status": "done",
        "status_label": "Готово",
        "detail": "1 034 фото · 7 видео пропущено",
        "progress": 100,
    },
    {
        "number": 3,
        "name": "Подготовка preview",
        "status": "done",
        "status_label": "Готово",
        "detail": "1 031 preview · 3 требуют внимания",
        "progress": 100,
    },
    {
        "number": 4,
        "name": "Анализ",
        "status": "done",
        "status_label": "Готово",
        "detail": "Метрики, hashes и 86 duplicate-групп",
        "progress": 100,
    },
    {
        "number": 5,
        "name": "Ручное ревью",
        "status": "active",
        "status_label": "Продолжить",
        "detail": "Проверено 642 из 1 034 · 39 фото ждут решения",
        "progress": 62,
    },
    {
        "number": 6,
        "name": "Публикация Reject-альбома",
        "status": "pending",
        "status_label": "Ожидает",
        "detail": "Станет доступно после ревью и dry-run",
        "progress": 0,
    },
]


DEMO_SUMMARY = [
    {"label": "Всего фото", "value": "1 034", "tone": "neutral"},
    {"label": "Оставить", "value": "808", "tone": "keep"},
    {"label": "На проверку", "value": "39", "tone": "review"},
    {"label": "Удалить", "value": "187", "tone": "reject"},
    {"label": "Группы дублей", "value": "86", "tone": "neutral"},
    {"label": "Favorite защищено", "value": "24", "tone": "keep"},
]
