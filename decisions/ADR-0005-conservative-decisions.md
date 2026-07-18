# ADR-0005: Консервативная политика automatic reject

- Status: Accepted

## Context

Смаз, композиция и эстетика субъективны. Ошибочный reject может скрыть эмоционально важный кадр.

## Decision

Automatic reject по умолчанию применяется только к уверенным duplicate losers. Technical defects и эстетические сомнения идут в review. Favorite, edited, missing, leaders и ambiguous groups защищены.

## Consequences

- Меньше false-positive rejects.
- Пользователю придётся просмотреть больше категории Review.
- Безопасность приоритетнее максимального автоматического сокращения.
