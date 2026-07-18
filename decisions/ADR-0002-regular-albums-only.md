# ADR-0002: Только обычные альбомы как прямой source

- Status: Accepted

## Context

Основной пользовательский материал может приходить из Shared Album, однако shared-assets имеют особую модель хранения и совместимости.

## Decision

Shared Album используется только для ручного импорта. Photo Curator работает с обычным альбомом личной Photos Library.

## Consequences

Плюсы:

- безопасный и единый source model;
- Shared Album остаётся нетронутым;
- упрощается inventory и publish;
- возможно сравнить полноразмерные originals с shared-копиями.

Минусы:

- дополнительный ручной intake;
- импортированная shared-версия может быть уменьшенной.
