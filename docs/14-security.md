# Security

## Runtime secrets and Keychain

Нативное приложение не использует macOS Keychain. Пользовательские настройки хранятся
в `UserDefaults`, project/taste state — в локальной SQLite, review-renders — в cache.
Runtime не вызывает `security`, `SecItem*` или `SecKeychain*` и не создаёт сертификаты.

Release tooling также не сохраняет credentials в Keychain: notarization получает явно
указанный App Store Connect API `.p8` file, key ID и issuer ID. Сам `.p8` не входит в
репозиторий и должен иметь доступ только для владельца.

## Legacy web: loopback only

Этот раздел относится только к explicit `legacy-web`; нативное приложение не поднимает
HTTP server и не слушает localhost.

Server bind:

```text
127.0.0.1
```

Не использовать `0.0.0.0`.

## Session bootstrap

- cryptographically random startup token;
- initial token URL;
- SameSite=Strict cookie;
- redirect на URL без token.

## Mutating requests

- CSRF token;
- CORS disabled;
- Host allowlist `127.0.0.1`, `localhost`;
- methods/Content-Type validation.

## Media isolation

Client никогда не передаёт filesystem path. Server строит cache path из validated project ID и asset UUID.

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
- no cloud calls;
- no analytics;
- no external CDN;
- не логировать GPS/person names/image data.
