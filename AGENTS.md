# AGENTS.md

## Обязательное чтение

Перед работой прочитать `README.md`, `docs/04-safety-and-data-integrity.md` и
`docs/10-testing-and-quality.md`. Для roadmap/acceptance claims также читать
`docs/16-execution-roadmap.md` и актуальный versioned acceptance evidence.

## Неприкосновенная граница Photos

- Original Apple Photos library, assets, metadata, albums и `Photos.sqlite`
  никогда не редактировать и не удалять напрямую.
- Product output — предложение, immutable dry run и новый album после явного
  подтверждения. Source album и source assets должны сохраниться.
- Реальный PhotoKit acceptance разрешён только как отдельная явно согласованная
  проверка с уникальным disposable output album, source revalidation и cleanup,
  который удаляет только созданный test container.
- `Command+Delete` и другие действия, способные удалить asset из всей library,
  не автоматизировать.

## Quality и model boundaries

- Перед commit запускать `make lint` и релевантный `make test`; для packaging
  changes дополнительно `make app` и `make verify-app`.
- Tests, synthetic demo и packaged smoke не заменяют друг друга. Real Photos,
  visual performance и human-labelled quality остаются отдельными acceptance
  gates.
- Некалиброванные aesthetic/semantic models остаются advisory и не должны
  самостоятельно определять delete safety или финальный Pick. Оценивать модели
  на album-separated human-labelled evidence без prediction-conditioned выборки.
- Любое recursive cleanup ограничивать service-owned resolved paths и сохранять
  forensic/audit evidence при partial recovery.

## Installed app и release

- Build/change/commit не разрешают `make install`, `make uninstall`, publish,
  notarization или создание release. Эти действия требуют явной задачи.
- После разрешённой установки проверять exact app version/build, bundle/signature,
  native worker startup и затронутый пользовательский path.
- Не называть preview production-ready, пока обязательные human, Photos и
  performance gates не имеют свежего evidence.

## Evidence и Git

- Различать `implemented`, `tests-passed`, `app-built`, `installed`,
  `runtime-verified`, `human-accepted` и `released`.
- Проверять branch/status/diff, сохранять unrelated work и stage только scoped
  files. Один логический этап — один commit.
- Local commit не разрешает push или внешнюю публикацию.
