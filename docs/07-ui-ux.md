# UI и UX

## Общий подход

Локальное desktop-first web-приложение на FastAPI/Jinja/vanilla JS. Основной workflow не требует терминала после запуска.

## Home

- current Photos Library;
- compatibility status;
- `osxphotos` version;
- existing projects;
- New Project;
- Doctor;
- Shared Album intake warning.

## New Project

- album selector с folder path;
- photo/video counts;
- project name;
- Start Analysis.

Shared collections показывать отдельно с инструкцией локального импорта, не как мёртвый disabled option.

## Pipeline visualizer

```text
[Проверка] → [Inventory] → [Preview] → [Analysis] → [Review] → [Publish]
```

Компактная строка этапа:

- pending/running/done/warning/error/interrupted;
- progress bar;
- processed/total;
- warnings/errors;
- current message;
- retry/details.

Не показывать speculative ETA.

## Ключевые счетчики

- Отобрано;
- Проверить;
- Исключено;
- Серии и дубликаты.

## Review gallery

- page size 60;
- lazy thumbnails;
- category/filter/sort;
- multi-select и range-select;
- batch Отобрано/Проверить/Исключено;
- large preview;
- оценка 0–100, русские reasons, breakdown и evidence;
- manual note;
- clear override;
- open in Photos.

## Shortcuts

```text
K keep
R review
X reject
C clear override
←/→ navigation
Space large preview
Esc close
```

Не обрабатывать shortcuts в form controls.

## Duplicate comparison

Members рядом, resolution, Favorite, edited, similarity, quality. Пользователь может сделать member лидером и изменить dispositions.

## Publish UX

Показать counts и блокирующие warnings. Dry-run и apply — отдельные действия. Apply требует checkbox-подтверждения.

## Accessibility basics

- semantic buttons/labels;
- visible focus;
- keyboard navigation;
- alt text на thumbnails;
- текстовые статусы, не только цвет;
- достаточный contrast;
- reduced-motion friendly progress.

## Language

MVP — русский. UI strings вынести централизованно.
