# ADR-0001: Локальный web UI вместо PySide6/Electron

- Status: Accepted

## Context

Нужен визуальный pipeline и review gallery, но быстрый MVP не должен тянуть Qt, Electron или frontend build chain.

## Decision

Использовать FastAPI, Jinja2, vanilla JavaScript и CSS. Server доступен только через loopback.

## Consequences

Плюсы:

- минимальная упаковка;
- нет Node.js;
- простой progress polling;
- быстрый UI iteration;
- тестируемый HTTP API.

Минусы:

- browser window вместо native `.app`;
- нужен локальный server и CSRF/session protection.
