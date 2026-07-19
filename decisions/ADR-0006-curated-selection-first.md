# ADR-0006: Curated selection is the primary result

## Status

Accepted. Supersedes the product direction of ADR-0004 and ADR-0005 where they
make Reject the primary outcome. Their write-safety rules remain valid.

## Decision

The application produces an explainable curated gallery first:

`source → previews → technical metrics → exact/near series → local Vision →
score 0–100 → Selected / Review / Excluded → manual corrections → Best album`.

Internal `keep/review/reject` values remain for database compatibility. The UI
uses `Отобрано / Проверить / Исключено`. Missing, failed and ambiguous assets stay
in Review. Favorite, edited and series-leader frames are selected unless analysis
is unavailable.

Apple Vision is local and capability-gated. No cloud image API is used. Best and
optional Reject albums require independent dry-run and explicit apply. No code
path deletes a Photos asset or edits the Photos SQLite database.
