# Security

## Runtime secrets and Keychain

Нативное приложение не использует macOS Keychain. Пользовательские настройки хранятся
в `UserDefaults`, project/taste state — в локальной SQLite, review-renders — в cache.
Runtime не вызывает `security`, `SecItem*` или `SecKeychain*` и не создаёт сертификаты.

Release tooling также не сохраняет credentials в Keychain: notarization получает явно
указанный App Store Connect API `.p8` file, key ID и issuer ID. Сам `.p8` не входит в
репозиторий и должен иметь доступ только для владельца.

## Local IPC

Нативное приложение не поднимает HTTP server и не слушает localhost. SwiftUI общается
только с дочерним worker через versioned stdin/stdout JSONL, correlation ID и фиксированный
набор методов. Worker не принимает произвольные executable paths или shell commands.

## Media isolation

Worker возвращает только review/thumbnail paths внутри service-owned cache. Запросы клиента
адресуют проекты и фото через validated project ID и asset UUID.

## Subprocesses

```python
subprocess.run(argv, shell=False, check=False, ...)
```

- command as fixed executable;
- paths/names as separate argv;
- capture stdout/stderr;
- timeouts там, где уместно;
- не выполнять автоматически повторный destructive-ish apply.

## Filesystem safety

- atomic preview writes;
- recursive deletes только внутри cache root;
- symlink/resolved-path checks;
- originals read-only;
- source paths не отображать в UI.

## Privacy

- no telemetry;
- no cloud calls в стандартном локальном режиме;
- no analytics;
- no external CDN;
- не логировать GPS/person names/image data.

Optional Codex Vision является единственным явным cloud boundary. Он выключен по умолчанию,
показывает предупреждение перед созданием проекта и передаёт только service-owned review-копии
после подтверждения пользователя. Приложение не читает и не копирует Codex credentials:
официальный CLI самостоятельно использует существующую ChatGPT session. API-key auth для этого
режима считается неготовым состоянием. Команда запускается фиксированным argv с `shell=False`,
`--ephemeral`, `--ignore-user-config`, `--ignore-rules` и read-only sandbox.
