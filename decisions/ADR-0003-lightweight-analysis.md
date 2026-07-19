# ADR-0003: Лёгкий анализ без тяжёлого ML runtime

- Status: Superseded by ADR-0007 for V2; retained as the implemented baseline

## Context

Нужен быстрый локальный MVP без сложной установки и долгих model downloads.

## Decision

Использовать Pillow, NumPy, perceptual hashes, technical metrics и optional Apple Photos scores. Не включать PyTorch, TensorFlow, CLIP, fastdup и OpenCV в обязательный MVP.

## Consequences

Плюсы:

- простой setup;
- малая runtime surface;
- объяснимые signals;
- хорошая скорость для 1000–5000 photos.

Минусы:

- нет closed-eyes и semantic composition;
- near-duplicate detection менее мощный, чем embeddings;
- эстетика используется ограниченно.

Future: optional native Vision helper.
