"""Archive the eligibility of identifiable legacy blind answers, retaining their evidence."""

from __future__ import annotations

import hashlib
import json


def _hash(*parts):
    return hashlib.sha256("\0".join(str(part) for part in parts).encode()).hexdigest()


def exclude_legacy_blind_answers(connection):
    for row in connection.execute(
        "SELECT s.*, p.album_id FROM blind_taste_sessions s JOIN projects p ON p.id=s.project_id"
    ).fetchall():
        plan = json.loads(row["pairs_json"])
        if plan.get("policy") == "same-scene-v2":
            continue
        album_hash = _hash("album", row["album_id"])
        for pair, answer in zip(plan["pairs"], json.loads(row["answers_json"]), strict=False):
            if answer not in {"left", "right"}:
                continue
            left, right = sorted(_hash("asset", album_hash, value) for value in pair)
            pair_id = _hash("learning-pair", row["round_id"], left, right)
            connection.execute(
                "INSERT OR IGNORE INTO learning_pair_exclusions(id, reason, evidence_json) "
                "VALUES (?, 'legacy_blind_scene_unverified', ?)",
                (pair_id, json.dumps({"session_id": row["id"], "pair": pair, "answer": answer})),
            )
    # Old coefficients already include these answers. Preserve the examples, but
    # require a fresh fit before personal scores can use the remaining evidence.
    connection.execute(
        "UPDATE taste_profiles SET weights_base64=NULL, training_examples=0, "
        "status=CASE WHEN status='paused' THEN 'paused' ELSE 'collecting' END "
        "WHERE id IN (SELECT profile_id FROM preference_examples "
        "WHERE id IN (SELECT id FROM learning_pair_exclusions))"
    )
