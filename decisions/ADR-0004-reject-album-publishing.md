# ADR-0004: Временный Reject-альбом вместо keywords

- Status: Accepted

## Context

Keywords требуют сложного selective cleanup и могут затронуть пользовательские metadata. Пользователю нужен простой review в Photos.

## Decision

Каждый publish создаёт новый обычный временный альбом с final rejects. Он содержит ссылки на существующие assets, не новые файлы.

## Consequences

Плюсы:

- не меняются keywords;
- простой мобильный/desktop review;
- результат очевиден;
- dry-run/apply легко аудитировать.

Минусы:

- каждый publish создаёт новый album;
- старые review albums пользователь удаляет вручную;
- final deletion всё равно выполняется в Photos.
