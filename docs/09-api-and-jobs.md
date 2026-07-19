# API и background jobs

## HTML routes

```text
GET /
GET /doctor
GET /projects/<id>
GET /projects/<id>/review
GET /projects/<id>/duplicates
GET /projects/<id>/publish
```

## JSON API

```text
GET    /api/status
GET    /api/albums
POST   /api/projects
GET    /api/projects/<id>
DELETE /api/projects/<id>
POST   /api/projects/<id>/pipeline/start
POST   /api/projects/<id>/pipeline/resume
POST   /api/projects/<id>/stages/<stage>/retry
POST   /api/projects/<id>/retry-missing
GET    /api/jobs/<job-id>
GET    /api/projects/<id>/assets
GET    /api/projects/<id>/assets/<asset-uuid>
PATCH  /api/projects/<id>/assets/<asset-uuid>/decision
PATCH  /api/projects/<id>/assets/batch-decision
PATCH  /api/projects/<id>/duplicate-groups/<group-id>/leader
POST   /api/projects/<id>/decisions/recalculate
POST   /api/projects/<id>/publish/dry-run
POST   /api/projects/<id>/publish/apply
POST   /api/projects/<id>/cache/clean
```

`DELETE /api/projects/<id>` удаляет project state и preview-cache. Для последнего проекта,
связанного с service-owned Shared snapshot, также удаляются snapshot и его copy-job.
Операция никогда не удаляет assets из Photos.

## Pagination

Assets endpoint использует:

```text
page
page_size
sort
order
filters
```

Default page size 60, max 200.

## Job model

In-process coordinator, один Uvicorn worker, bounded ThreadPoolExecutor.

```python
max_workers = min(4, os.cpu_count() or 2)
```

`/api/jobs/<id>` возвращает:

```json
{
  "status": "running",
  "stage": "previews",
  "processed_items": 718,
  "total_items": 1034,
  "warning_count": 4,
  "error_count": 1,
  "current_message": "Подготовка IMG_4219.HEIC"
}
```

UI polling — около одной секунды. WebSocket не нужен.

## Resume

При startup все `running` jobs → `interrupted`. Resume должен пропускать готовые cache/metrics и сохранять manual overrides.

## Cancellation

MVP может поддержать cooperative cancellation между batches. Не убивать произвольный worker thread. Cancelled stage сохраняет completed batches.

## Media routes

```text
GET /media/<project-id>/thumbnail/<asset-uuid>
GET /media/<project-id>/review/<asset-uuid>
```

Path строится server-side. Не принимать `path` от клиента.
