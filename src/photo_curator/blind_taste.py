"""Short prediction-independent preference sessions over completed local analyses.

Only explicit winners enter the existing durable Quality Lab corpus. Skips carry no
preference. Answers train ranking without creating manual gallery decisions or deletion consent.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict, deque
from pathlib import Path

from photo_curator.analysis.blind_pairs import PAIR_POLICY, scene_pairs
from photo_curator.analysis.taste import feature_vector
from photo_curator.db import repository
from photo_curator.learning import learning_status, migrate_project_learning, start_new_round
from photo_curator.pipeline.duplicates import UnionFind


def _key(seed: str, value: str) -> str:
    return hashlib.sha256(f"blind-taste-v1|{seed}|{value}".encode()).hexdigest()


def prepare_session(connection, project_id: str) -> dict[str, object]:
    project = repository.get_project(connection, project_id)
    if project["state"] != "ready":
        raise ValueError("Сначала завершите локальный анализ альбома")
    if repository.latest_album_snapshot(connection, project_id) is None:
        raise ValueError(
            "В старом анализе нет снимка состава альбома. Создайте новый анализ для обучения; "
            "прежние ответы сохранятся."
        )
    active = learning_status(connection, project_id)["active_round"]
    if active is None:
        result = start_new_round(connection, project_id, "training")
        if result["status"] == "failed":
            raise ValueError(str(result["error"]))
        active = learning_status(connection, project_id)["active_round"]
    if active["split"] != "training":
        raise ValueError(
            "Этот альбом закреплён за независимой проверкой. Для обучения выберите другой альбом."
        )
    previous = connection.execute(
        "SELECT * FROM blind_taste_sessions WHERE project_id=? AND round_id=? "
        "ORDER BY rowid DESC LIMIT 1",
        (project_id, active["id"]),
    ).fetchone()
    if (
        previous
        and json.loads(previous["pairs_json"]).get("policy") == PAIR_POLICY
        and len(json.loads(previous["answers_json"]))
        < len(json.loads(previous["pairs_json"])["pairs"])
    ):
        return session_payload(connection, str(previous["id"]))
    assets = repository.list_assets(connection, project_id)
    signals = repository.analysis_signals_by_asset(connection, project_id)
    candidates = []
    schema = None
    for asset in assets:
        if (
            asset.get("no_longer_exists")
            or asset.get("media_type") != "image"
            or asset.get("cache_state") != "ready"
        ):
            continue
        if not asset.get("review_path") or not Path(str(asset["review_path"])).is_file():
            continue
        try:
            current_schema, _, _ = feature_vector(
                signals.get(str(asset["asset_uuid"]), {}).get("feature_print", {})
            )
        except ValueError:
            continue
        if schema is not None and schema != current_schema:
            continue
        schema = current_schema
        candidates.append(asset)
    seen = {
        frozenset((str(p["left_uuid"]), str(p["right_uuid"])))
        for p in repository.list_quality_preference_examples(connection, project_id)
    }
    # Skip previously presented pairs too. A skip never becomes a negative example.
    for row in connection.execute(
        "SELECT pairs_json FROM blind_taste_sessions WHERE project_id=?", (project_id,)
    ):
        seen.update(frozenset(p) for p in json.loads(row[0])["pairs"])
    attempt = connection.execute(
        "SELECT COUNT(*) FROM blind_taste_sessions WHERE project_id=?", (project_id,)
    ).fetchone()[0]
    seed = f"{project['album_id']}|{attempt}"
    verified = scene_pairs(candidates, signals)
    # Components balance coverage between series; only directly verified edges may
    # become questions. A-B and B-C do not authorize an unverified A-C comparison.
    components = UnionFind([str(asset["asset_uuid"]) for asset in candidates])
    for (left, right), _ in verified:
        components.union(left, right)
    pools = defaultdict(list)
    for pair, evidence in verified:
        if frozenset(pair) not in seen:
            pools[components.find(pair[0])].append((pair, evidence))
    queue = deque(
        deque(sorted(pool, key=lambda item: _key(seed, "|".join(item[0]))))
        for _, pool in sorted(pools.items(), key=lambda item: _key(seed, item[0]))
    )
    pairs, pair_evidence = [], []
    used: set[str] = set()
    while queue and len(pairs) < 12:
        pool = queue.popleft()
        while pool:
            pair, evidence = pool.popleft()
            if set(pair) & used:
                continue
            pairs.append(list(pair if int(_key(seed, pair[0])[-1], 16) % 2 else pair[::-1]))
            pair_evidence.append(evidence)
            used.update(pair)
            break
        if pool:
            queue.append(pool)
    if not pairs:
        raise ValueError(
            "Новых пар одной сцены не найдено. Для сравнения нужны разные кадры одной серии, "
            "снятые рядом по времени. Другие сюжеты и точные копии не сравниваются."
        )
    plan = {
        "policy": PAIR_POLICY,
        "pair_evidence": pair_evidence,
        "pairs": pairs,
        "snapshot_id": repository.latest_album_snapshot(connection, project_id)["id"],
        "features": {
            asset_id: _key(
                "feature", json.dumps(signals[asset_id]["feature_print"], sort_keys=True)
            )
            for asset_id in used
        },
    }
    session_id = repository.new_id()
    connection.execute(
        "INSERT INTO blind_taste_sessions(id, project_id, round_id, pairs_json, "
        "answers_json, created_at) VALUES (?, ?, ?, ?, '[]', ?)",
        (session_id, project_id, active["id"], json.dumps(plan), repository.utc_now()),
    )
    return session_payload(connection, session_id)


def session_payload(connection, session_id: str) -> dict[str, object]:
    row = connection.execute(
        "SELECT * FROM blind_taste_sessions WHERE id=?", (session_id,)
    ).fetchone()
    if row is None:
        raise ValueError("Сравнение больше недоступно")
    plan = json.loads(row["pairs_json"])
    if plan.get("policy") != PAIR_POLICY:
        raise ValueError("Правила сравнения обновлены. Откройте новый набор пар одной сцены.")
    pairs, answers = plan["pairs"], json.loads(row["answers_json"])
    active = learning_status(connection, str(row["project_id"]))["active_round"]
    if active is None or active["id"] != row["round_id"]:
        raise ValueError("Раунд изменился. Откройте новый набор сравнений.")
    project = repository.get_project(connection, str(row["project_id"]))
    snapshot = repository.latest_album_snapshot(connection, str(row["project_id"]))
    if project["state"] != "ready" or snapshot is None or snapshot["id"] != plan["snapshot_id"]:
        raise ValueError("Анализ изменился. Закройте сравнение и выберите новый готовый анализ.")
    pair = None
    if len(answers) < len(pairs):
        ids = pairs[len(answers)]
        assets = repository.assets_by_uuid(connection, str(row["project_id"]), set(ids))
        if set(assets) != set(ids):
            raise ValueError("Фотографии изменились. Повторите анализ.")

        for asset_id in ids:
            signal = next(
                (
                    s
                    for s in repository.list_analysis_signals(
                        connection, str(row["project_id"]), asset_id
                    )
                    if s["signal_kind"] == "feature_print"
                ),
                {},
            )
            if _key("feature", json.dumps(signal, sort_keys=True)) != plan["features"][asset_id]:
                raise ValueError("Признаки движка изменились. Начните обучение на новом анализе.")
            asset = assets[asset_id]
            if (
                asset.get("no_longer_exists")
                or asset.get("cache_state") != "ready"
                or not Path(str(asset["review_path"])).is_file()
            ):
                raise ValueError("Фото недоступно. Восстановите изображения перед сравнением.")

        def photo(asset):
            return {"id": asset["asset_uuid"], "review_path": asset["review_path"]}

        pair = {"left": photo(assets[ids[0]]), "right": photo(assets[ids[1]])}
    return {
        "id": session_id,
        "project_id": row["project_id"],
        "album_name": project["album_name"],
        "completed": len(answers),
        "total": len(pairs),
        "pair": pair,
        "choices": sum(answer in {"left", "right"} for answer in answers),
    }


def answer_session(connection, session_id: str, index: int, choice: str) -> dict[str, object]:
    if choice not in {"left", "right", "skip", "different_scene"}:
        raise ValueError("Выберите левое, правое фото или пропустите пару")
    state = session_payload(connection, session_id)
    row = connection.execute(
        "SELECT * FROM blind_taste_sessions WHERE id=?", (session_id,)
    ).fetchone()
    answers = json.loads(row["answers_json"])
    # Retries are idempotent, stale/double clicks cannot answer the next pair.
    if index < len(answers) and index >= 0 and answers[index] == choice:
        return state
    if isinstance(index, bool) or index != len(answers) or state["pair"] is None:
        raise ValueError("Эта пара уже изменилась; дождитесь следующей фотографии")
    if choice in {"left", "right"}:
        pair = state["pair"]
        repository.add_quality_preference_example(
            connection,
            project_id=str(row["project_id"]),
            left_uuid=pair["left"]["id"],
            right_uuid=pair["right"]["id"],
            preferred_uuid=pair[choice]["id"],
            split="training",
        )
        result = migrate_project_learning(connection, str(row["project_id"]))
        if result["status"] == "failed":
            raise ValueError(str(result["error"]))
    answers.append(choice)
    connection.execute(
        "UPDATE blind_taste_sessions SET answers_json=? WHERE id=?",
        (json.dumps(answers), session_id),
    )
    return session_payload(connection, session_id)
