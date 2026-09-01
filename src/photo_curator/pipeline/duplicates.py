from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

from photo_curator.analysis.hashes import hamming_distance
from photo_curator.analysis.similarity import (
    histogram_similarity,
)
from photo_curator.analysis.taste import TasteProfileError, feature_vector

DUPLICATE_ENGINE_VERSION = "3-semantic-crop-series-picks"


@dataclass(slots=True)
class DuplicateMemberResult:
    asset_uuid: str
    is_leader: bool = False
    similarity: float = 0.0
    quality_score: float = 0.0
    resolution_ratio: float = 1.0
    evidence: dict[str, object] = field(default_factory=dict)


@dataclass(slots=True)
class DuplicateGroupResult:
    group_id: str
    kind: str
    confidence: float
    leader_uuid: str
    flags: list[str]
    members: list[DuplicateMemberResult]


class UnionFind:
    def __init__(self, values: list[str]) -> None:
        self.parent = {value: value for value in values}

    def find(self, value: str) -> str:
        while self.parent[value] != value:
            self.parent[value] = self.parent[self.parent[value]]
            value = self.parent[value]
        return value

    def union(self, left: str, right: str) -> None:
        left_root, right_root = self.find(left), self.find(right)
        if left_root != right_root:
            self.parent[right_root] = left_root


def find_duplicate_groups(
    assets: list[dict[str, object]],
    *,
    strict_phash_distance: int = 4,
    relaxed_phash_distance: int = 10,
    time_window_seconds: int = 120,
    candidate_pairs: list[tuple[dict[str, object], dict[str, object]]] | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> list[DuplicateGroupResult]:
    candidates = [asset for asset in assets if _eligible_duplicate_asset(asset)]
    union = UnionFind([str(asset["asset_uuid"]) for asset in candidates])
    pair_evidence: dict[frozenset[str], dict[str, object]] = {}
    preview_cache: dict[str, tuple[np.ndarray, np.ndarray] | None] = {}
    pairs = candidate_pairs if candidate_pairs is not None else list(_candidate_pairs(candidates))
    total = len(pairs)
    if progress:
        progress(0, total)
    for index, (left, right) in enumerate(pairs, start=1):
        evidence = _confirm_pair(
            left,
            right,
            strict_phash_distance=strict_phash_distance,
            relaxed_phash_distance=relaxed_phash_distance,
            time_window_seconds=time_window_seconds,
            preview_cache=preview_cache,
        )
        if evidence:
            left_uuid, right_uuid = str(left["asset_uuid"]), str(right["asset_uuid"])
            union.union(left_uuid, right_uuid)
            pair_evidence[frozenset((left_uuid, right_uuid))] = evidence
        if progress and (index % 100 == 0 or index == total):
            progress(index, total)

    by_root: dict[str, list[dict[str, object]]] = {}
    for asset in candidates:
        by_root.setdefault(union.find(str(asset["asset_uuid"])), []).append(asset)
    coherent_groups = []
    for members in by_root.values():
        coherent_groups.extend(_split_into_leader_coherent_groups(members, pair_evidence))
    groups = []
    for index, members in enumerate(coherent_groups, start=1):
        groups.append(_build_group(index, members, pair_evidence, relaxed_phash_distance))
    return groups


def _candidate_pairs(
    assets: list[dict[str, object]],
) -> Iterator[tuple[dict[str, object], dict[str, object]]]:
    by_uuid = {str(asset["asset_uuid"]): asset for asset in assets}
    candidate_ids: set[tuple[str, str]] = set()
    bands: dict[tuple[int, int], set[int]] = {}
    by_phash: dict[int, list[str]] = {}
    exact_renders: dict[str, list[str]] = {}
    bursts: dict[str, list[str]] = {}
    for asset in assets:
        uuid = str(asset["asset_uuid"])
        try:
            phash_value = int(str(asset["phash"]), 16)
        except ValueError:
            continue
        by_phash.setdefault(phash_value, []).append(uuid)
        for band, value in enumerate(_phash_bands(str(asset["phash"]))):
            bands.setdefault((band, value), set()).add(phash_value)
        if render_hash := asset.get("normalized_pixel_hash"):
            exact_renders.setdefault(str(render_hash), []).append(uuid)
        if _valid_burst_key(asset.get("burst_key")):
            bursts.setdefault(str(asset["burst_key"]), []).append(uuid)

    # With five bands and a full-image Hamming threshold of ten, at least one
    # band differs in at most two bits. Multi-probing every radius-2 neighbour
    # therefore cannot silently miss a qualifying pHash pair. Large identical
    # buckets stay linear by connecting their best representative to each member.
    phash_pairs: set[tuple[int, int]] = set()
    for phash_value in sorted(by_phash):
        for band, band_value in enumerate(_phash_bands(f"{phash_value:016x}")):
            width = _PHASH_BAND_WIDTHS[band]
            for neighbour in _hamming_neighbours(band_value, width, radius=2):
                for other_hash in bands.get((band, neighbour), ()):
                    if other_hash > phash_value and (phash_value ^ other_hash).bit_count() <= 10:
                        phash_pairs.add((phash_value, other_hash))
    representatives: dict[int, str] = {}
    for phash_value, bucket in by_phash.items():
        representative = max(bucket, key=lambda uuid: _leader_key(by_uuid[uuid]))
        representatives[phash_value] = representative
        for member in bucket:
            if member != representative:
                candidate_ids.add(tuple(sorted((representative, member))))
    for left_hash, right_hash in phash_pairs:
        left_rep = representatives[left_hash]
        right_rep = representatives[right_hash]
        for member in by_phash[left_hash]:
            candidate_ids.add(tuple(sorted((member, right_rep))))
        for member in by_phash[right_hash]:
            candidate_ids.add(tuple(sorted((left_rep, member))))
    for bucket in [*exact_renders.values(), *bursts.values()]:
        left = max(bucket, key=lambda uuid: _leader_key(by_uuid[uuid]))
        for right in sorted(uuid for uuid in bucket if uuid != left):
            candidate_ids.add(tuple(sorted((left, right))))

    # Visually related shots are normally close in capture time. Add every pair
    # inside the relaxed 120 second window without opening the full N^2 search.
    dated = sorted(
        (timestamp, str(asset["asset_uuid"]))
        for asset in assets
        if (timestamp := _timestamp(asset.get("taken_at"))) is not None
    )
    for index, (left_time, left_uuid) in enumerate(dated):
        right_index = index + 1
        while right_index < len(dated):
            right_time, right_uuid = dated[right_index]
            if (right_time - left_time).total_seconds() > 120:
                break
            candidate_ids.add(tuple(sorted((left_uuid, right_uuid))))
            right_index += 1
    for left, right in sorted(candidate_ids):
        yield by_uuid[left], by_uuid[right]


def _phash_bands(value: str) -> tuple[int, ...]:
    """Partition a 64-bit pHash into five disjoint candidate-reduction bands."""
    try:
        bits = f"{int(value, 16):064b}"
    except ValueError:
        return ()
    offset = 0
    result = []
    for width in _PHASH_BAND_WIDTHS:
        result.append(int(bits[offset : offset + width], 2))
        offset += width
    return tuple(result)


_PHASH_BAND_WIDTHS = (13, 13, 13, 13, 12)


def _hamming_neighbours(value: int, width: int, *, radius: int) -> Iterator[int]:
    yield value
    if radius < 1:
        return
    for left in range(width):
        yield value ^ (1 << left)
    if radius < 2:
        return
    for left in range(width):
        for right in range(left + 1, width):
            yield value ^ (1 << left) ^ (1 << right)


def _valid_burst_key(value: object) -> bool:
    return value not in (None, False, 0, "", "0")


def _timestamp(value: object) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def _split_into_leader_coherent_groups(
    members: list[dict[str, object]],
    evidence: dict[frozenset[str], dict[str, object]],
) -> list[list[dict[str, object]]]:
    """Prevent weak A-B-C chains from becoming one misleading photo series."""
    # Exact render equivalence is a stronger safety contract than scene similarity.
    # Extract those clusters first so a higher-quality scene leader cannot hide an
    # exact pair among non-leader members.
    exact_buckets: dict[str, list[dict[str, object]]] = {}
    for asset in members:
        render_hash = asset.get("normalized_pixel_hash")
        if render_hash:
            exact_buckets.setdefault(str(render_hash), []).append(asset)
    groups = [bucket for bucket in exact_buckets.values() if len(bucket) >= 2]
    exact_ids = {str(asset["asset_uuid"]) for bucket in groups for asset in bucket}
    remaining = sorted(
        (asset for asset in members if str(asset["asset_uuid"]) not in exact_ids),
        key=_leader_key,
        reverse=True,
    )
    while remaining:
        leader = remaining.pop(0)
        leader_uuid = str(leader["asset_uuid"])
        direct = [
            asset
            for asset in remaining
            if frozenset((leader_uuid, str(asset["asset_uuid"]))) in evidence
        ]
        if direct:
            groups.append([leader, *direct])
            direct_ids = {str(asset["asset_uuid"]) for asset in direct}
            remaining = [asset for asset in remaining if str(asset["asset_uuid"]) not in direct_ids]
    return groups


def _confirm_pair(
    left: dict[str, object],
    right: dict[str, object],
    *,
    strict_phash_distance: int,
    relaxed_phash_distance: int,
    time_window_seconds: int,
    feature_similarity: float | None = None,
    preview_cache: dict[str, tuple[np.ndarray, np.ndarray] | None] | None = None,
) -> dict[str, object] | None:
    render_hash = left.get("normalized_pixel_hash")
    exact = bool(render_hash and render_hash == right.get("normalized_pixel_hash"))
    distance = hamming_distance(str(left["phash"]), str(right["phash"]))
    dhash_distance = hamming_distance(str(left["dhash"]), str(right["dhash"]))
    aspect_left = _aspect_ratio(left)
    aspect_right = _aspect_ratio(right)
    aspect_delta = abs(aspect_left - aspect_right) / max(aspect_left, aspect_right, 0.001)
    time_delta = _time_delta(left.get("taken_at"), right.get("taken_at"))
    burst = bool(left.get("burst_key") and left.get("burst_key") == right.get("burst_key"))
    histogram = histogram_similarity(
        list(left.get("histogram") or []), list(right.get("histogram") or [])
    )
    cheap_strict = distance <= strict_phash_distance and aspect_delta <= 0.08
    cheap_relaxed = (
        distance <= relaxed_phash_distance
        and time_delta is not None
        and time_delta <= time_window_seconds
        and aspect_delta <= 0.08
        and histogram >= 0.84
    )
    semantic_candidate = (
        feature_similarity is not None
        and feature_similarity >= 0.985
        and time_delta is not None
        and time_delta <= min(15, time_window_seconds)
    )
    pixel_mae, exposure_crop_mae = (
        _preview_distances(left, right, preview_cache)
        if not exact and (cheap_strict or cheap_relaxed or burst or semantic_candidate)
        else (None, None)
    )
    strict = (
        cheap_strict
        and dhash_distance <= 12
        and histogram >= 0.72
        and (pixel_mae is None or pixel_mae <= 0.18)
        and time_delta is not None
        and time_delta <= time_window_seconds
    )
    relaxed = cheap_relaxed and dhash_distance <= 18 and (pixel_mae is None or pixel_mae <= 0.22)
    burst_confirmed = burst and dhash_distance <= 24 and histogram >= 0.60
    semantic_confirmed = semantic_candidate and (
        (exposure_crop_mae is not None and exposure_crop_mae <= 0.20)
        or (histogram >= 0.78 and dhash_distance <= 28)
    )
    if not (exact or strict or relaxed or burst_confirmed or semantic_confirmed):
        return None
    confidence = (
        1.0
        if exact
        else max(
            0.75,
            1.0 - distance / 16.0,
            min(0.99, float(feature_similarity or 0.0)),
        )
    )
    return {
        "exact": exact,
        "phash_distance": distance,
        "dhash_distance": dhash_distance,
        "normalized_pixel_mae": pixel_mae,
        "exposure_invariant_crop_mae": exposure_crop_mae,
        "feature_print_similarity": feature_similarity,
        "aspect_delta": aspect_delta,
        "time_delta": time_delta,
        "histogram_similarity": histogram,
        "confidence": confidence,
    }


def _preview_distances(
    left: dict[str, object],
    right: dict[str, object],
    cache: dict[str, tuple[np.ndarray, np.ndarray] | None] | None,
) -> tuple[float | None, float | None]:
    cache = cache if cache is not None else {}
    left_descriptor = _preview_descriptor(left, cache)
    right_descriptor = _preview_descriptor(right, cache)
    if left_descriptor is None or right_descriptor is None:
        return None, None
    raw_left, normalized_left = left_descriptor
    raw_right, normalized_right = right_descriptor
    return (
        float(np.mean(np.abs(raw_left.astype(np.int16) - raw_right.astype(np.int16))) / 255.0),
        float(min(1.0, np.mean(np.abs(normalized_left - normalized_right)) / 4.0)),
    )


def _preview_descriptor(
    asset: dict[str, object],
    cache: dict[str, tuple[np.ndarray, np.ndarray] | None],
) -> tuple[np.ndarray, np.ndarray] | None:
    preview_path = asset.get("thumbnail_path") or asset.get("review_path")
    key = str(asset.get("source_fingerprint") or preview_path or "")
    if key in cache:
        return cache[key]
    path = Path(str(preview_path or ""))
    if not path.is_file():
        cache[key] = None
        return None
    try:
        with Image.open(path) as image:
            rgb = image.convert("RGB")
            raw = np.asarray(rgb.resize((96, 96)), dtype=np.uint8)
            fitted = ImageOps.fit(rgb, (96, 96), method=Image.Resampling.LANCZOS)
            value = np.asarray(fitted, dtype=np.float32) / 255.0
        mean = np.mean(value, axis=(0, 1), keepdims=True)
        scale = np.std(value, axis=(0, 1), keepdims=True)
        normalized = np.clip((value - mean) / np.maximum(scale, 0.05), -4.0, 4.0).astype(np.float16)
        cache[key] = (raw, normalized)
    except (OSError, ValueError):
        cache[key] = None
    return cache[key]


def _build_group(
    index: int,
    assets: list[dict[str, object]],
    evidence: dict[frozenset[str], dict[str, object]],
    relaxed_distance: int,
    *,
    leader: dict[str, object] | None = None,
    group_id: str | None = None,
) -> DuplicateGroupResult:
    leader = leader or _choose_leader(assets)
    max_pixels = max(_pixels(asset) for asset in assets) or 1
    flags = []
    if _pixels(leader) < max_pixels * 0.75:
        flags.append("leader_lower_resolution")
    leader_render_hash = leader.get("normalized_pixel_hash")
    if leader_render_hash and all(
        asset.get("normalized_pixel_hash") == leader_render_hash for asset in assets
    ):
        group_kind = "exact"
    elif _valid_burst_key(leader.get("burst_key")) and all(
        asset.get("burst_key") == leader.get("burst_key") for asset in assets
    ):
        group_kind = "burst"
    else:
        group_kind = "scene"
    ambiguous = any(
        asset is not leader
        and frozenset((str(asset["asset_uuid"]), str(leader["asset_uuid"]))) not in evidence
        for asset in assets
    )
    if ambiguous:
        flags.append("ambiguous_duplicate")
        group_kind = "ambiguous"
    members = []
    confidences = []
    recommended = _recommended_series_picks(assets, leader, group_kind)
    for asset in sorted(assets, key=lambda value: str(value["asset_uuid"])):
        uuid = str(asset["asset_uuid"])
        leader_uuid = str(leader["asset_uuid"])
        pair = dict(evidence.get(frozenset((uuid, leader_uuid)), {}) if uuid != leader_uuid else {})
        pair["recommended_pick"] = uuid in recommended
        confidence = float(pair.get("confidence", 1.0))
        confidences.append(confidence)
        members.append(
            DuplicateMemberResult(
                asset_uuid=uuid,
                is_leader=uuid == leader_uuid,
                similarity=confidence,
                quality_score=_quality(asset),
                resolution_ratio=_pixels(asset) / max_pixels,
                evidence=pair,
            )
        )
    return DuplicateGroupResult(
        group_id=group_id or f"group-{index:04d}",
        kind=group_kind,
        confidence=min(confidences),
        leader_uuid=str(leader["asset_uuid"]),
        flags=flags,
        members=members,
    )


def rerank_duplicate_groups(
    groups: list[dict[str, object]],
    assets: list[dict[str, object]],
    signals: dict[str, dict[str, dict[str, object]]],
    *,
    personal_deltas: dict[str, float] | None = None,
    relaxed_phash_distance: int = 10,
    check_cancelled: Callable[[], None] | None = None,
) -> list[DuplicateGroupResult]:
    """Rebuild every member-to-leader row after all ranking signals are available."""
    if check_cancelled:
        check_cancelled()
    assets_by_uuid = {str(asset["asset_uuid"]): asset for asset in assets}
    personal_deltas = personal_deltas or {}
    preview_cache: dict[str, tuple[np.ndarray, np.ndarray] | None] = {}
    previous = {
        frozenset(str(member["asset_uuid"]) for member in group.get("members", [])): group
        for group in groups
    }
    discovered = _signal_aware_groups(
        assets,
        signals,
        relaxed_phash_distance,
        preview_cache,
        check_cancelled=check_cancelled,
    )
    groups = []
    for group in discovered:
        member_ids = frozenset(member.asset_uuid for member in group.members)
        old = previous.get(member_ids)
        groups.append(
            {
                "group_id": (
                    str(old["group_id"])
                    if old
                    else "series-"
                    + hashlib.sha256("\0".join(sorted(member_ids)).encode()).hexdigest()[:12]
                ),
                "kind": group.kind,
                "confidence": group.confidence,
                "leader_uuid": group.leader_uuid,
                "flags": sorted(set(group.flags).union(old.get("flags", []) if old else [])),
                "members": [
                    {
                        "asset_uuid": member.asset_uuid,
                        "is_leader": member.is_leader,
                        "evidence_json": json.dumps(member.evidence),
                    }
                    for member in group.members
                ],
            }
        )
    results = []
    for index, group in enumerate(groups, start=1):
        if check_cancelled:
            check_cancelled()
        members = [
            assets_by_uuid[str(member["asset_uuid"])]
            for member in group.get("members", [])
            if str(member.get("asset_uuid")) in assets_by_uuid
        ]
        if len(members) < 2:
            continue
        evidence: dict[frozenset[str], dict[str, object]] = {}
        pair_index = 0
        for left_index, left in enumerate(members):
            for right in members[left_index + 1 :]:
                pair_index += 1
                if check_cancelled and pair_index % 100 == 0:
                    check_cancelled()
                pair = _confirm_pair(
                    left,
                    right,
                    strict_phash_distance=4,
                    relaxed_phash_distance=relaxed_phash_distance,
                    time_window_seconds=120,
                    feature_similarity=_feature_similarity(
                        signals.get(str(left["asset_uuid"]), {}),
                        signals.get(str(right["asset_uuid"]), {}),
                    ),
                    preview_cache=preview_cache,
                )
                if pair:
                    evidence[frozenset((str(left["asset_uuid"]), str(right["asset_uuid"])))] = pair

        coherent = [
            candidate
            for candidate in members
            if all(
                other is candidate
                or frozenset((str(candidate["asset_uuid"]), str(other["asset_uuid"]))) in evidence
                for other in members
            )
        ]
        current_leader = next(
            (
                asset
                for asset in members
                if str(asset["asset_uuid"]) == str(group.get("leader_uuid") or "")
            ),
            members[0],
        )
        candidates = coherent or [current_leader]
        enhanced = []
        for candidate in candidates:
            asset_uuid = str(candidate["asset_uuid"])
            enhanced.append(
                {
                    **candidate,
                    "series_feature_vector": _feature_vector(signals.get(asset_uuid, {})),
                    "series_quality_score": _series_quality(
                        candidate,
                        signals.get(asset_uuid, {}),
                        personal_deltas.get(asset_uuid, 0.0),
                    ),
                }
            )
        leader = _choose_leader(enhanced)
        member_values = [
            {
                **member,
                "series_feature_vector": _feature_vector(
                    signals.get(str(member["asset_uuid"]), {})
                ),
                "series_quality_score": _series_quality(
                    member,
                    signals.get(str(member["asset_uuid"]), {}),
                    personal_deltas.get(str(member["asset_uuid"]), 0.0),
                ),
            }
            for member in members
        ]
        leader = next(
            member
            for member in member_values
            if str(member["asset_uuid"]) == str(leader["asset_uuid"])
        )
        rebuilt = _build_group(
            index,
            member_values,
            evidence,
            relaxed_phash_distance,
            leader=leader,
            group_id=str(group["group_id"]),
        )
        old_flags = group.get("flags")
        if not isinstance(old_flags, list):
            try:
                old_flags = json.loads(str(group.get("flags_json") or "[]"))
            except json.JSONDecodeError:
                old_flags = []
        rebuilt.flags = sorted(
            set(rebuilt.flags).union(
                str(flag)
                for flag in old_flags
                if flag not in {"leader_lower_resolution", "ambiguous_duplicate"}
            )
        )
        results.append(rebuilt)
    return results


def _signal_aware_groups(
    assets: list[dict[str, object]],
    signals: dict[str, dict[str, dict[str, object]]],
    relaxed_phash_distance: int,
    preview_cache: dict[str, tuple[np.ndarray, np.ndarray] | None],
    *,
    check_cancelled: Callable[[], None] | None = None,
) -> list[DuplicateGroupResult]:
    candidates = [asset for asset in assets if _eligible_duplicate_asset(asset)]
    union = UnionFind([str(asset["asset_uuid"]) for asset in candidates])
    evidence: dict[frozenset[str], dict[str, object]] = {}
    for index, (left, right) in enumerate(_candidate_pairs(candidates), start=1):
        if check_cancelled and index % 100 == 0:
            check_cancelled()
        left_uuid = str(left["asset_uuid"])
        right_uuid = str(right["asset_uuid"])
        pair = _confirm_pair(
            left,
            right,
            strict_phash_distance=4,
            relaxed_phash_distance=relaxed_phash_distance,
            time_window_seconds=120,
            feature_similarity=_feature_similarity(
                signals.get(left_uuid, {}), signals.get(right_uuid, {})
            ),
            preview_cache=preview_cache,
        )
        if pair:
            union.union(left_uuid, right_uuid)
            evidence[frozenset((left_uuid, right_uuid))] = pair
    by_root: dict[str, list[dict[str, object]]] = {}
    for asset in candidates:
        by_root.setdefault(union.find(str(asset["asset_uuid"])), []).append(asset)
    coherent = []
    for members in by_root.values():
        coherent.extend(_split_into_leader_coherent_groups(members, evidence))
    return [
        _build_group(index, members, evidence, relaxed_phash_distance)
        for index, members in enumerate(coherent, start=1)
    ]


def _eligible_duplicate_asset(asset: dict[str, object]) -> bool:
    return bool(
        asset.get("phash")
        and not asset.get("no_longer_exists")
        and not asset.get("is_missing")
        and asset.get("cache_state") == "ready"
    )


def _feature_vector(signals: dict[str, dict[str, object]]) -> np.ndarray | None:
    signal = signals.get("feature_print")
    if not signal or signal.get("status") != "ready":
        return None
    try:
        _, vector, _ = feature_vector(signal)
    except TasteProfileError:
        return None
    norm = float(np.linalg.norm(vector))
    return vector / norm if norm > 1e-8 else None


def _feature_similarity(
    left: dict[str, dict[str, object]], right: dict[str, dict[str, object]]
) -> float | None:
    left_vector = _feature_vector(left)
    right_vector = _feature_vector(right)
    if left_vector is None or right_vector is None or left_vector.size != right_vector.size:
        return None
    return round(float(np.dot(left_vector, right_vector)), 6)


def _recommended_series_picks(
    assets: list[dict[str, object]], leader: dict[str, object], group_kind: str
) -> set[str]:
    leader_uuid = str(leader["asset_uuid"])
    if group_kind == "exact":
        return {leader_uuid}
    ranked = sorted(assets, key=_leader_key, reverse=True)
    leader_quality = _quality(leader)
    selected: list[np.ndarray] = []
    result = {leader_uuid}
    leader_vector = leader.get("series_feature_vector")
    if isinstance(leader_vector, np.ndarray):
        selected.append(leader_vector)
    for asset in ranked:
        uuid = str(asset["asset_uuid"])
        if uuid == leader_uuid or len(result) >= 3 or _quality(asset) < leader_quality - 0.08:
            continue
        vector = asset.get("series_feature_vector")
        if not isinstance(vector, np.ndarray) or not selected:
            continue
        # A materially different pose/moment can survive the stack as an additional pick.
        if max(float(np.dot(vector, chosen)) for chosen in selected) < 0.995:
            result.add(uuid)
            selected.append(vector)
    return result


def _leader_key(asset: dict[str, object]) -> tuple[object, ...]:
    return (
        _quality(asset),
        bool(asset.get("favorite")),
        bool(asset.get("has_adjustments")),
        bool(asset.get("burst_default_pick")),
        _pixels(asset),
        str(asset.get("asset_uuid")),
    )


def _choose_leader(assets: list[dict[str, object]]) -> dict[str, object]:
    render_hashes = {asset.get("normalized_pixel_hash") for asset in assets}
    exact_group = len(render_hashes) == 1 and None not in render_hashes
    if exact_group:
        # A protected exact copy must remain the safety leader; otherwise the better
        # unprotected copy becomes leader and no duplicate is safely removable.
        return max(
            assets,
            key=lambda asset: (
                bool(asset.get("favorite")),
                bool(asset.get("has_adjustments")),
                _quality(asset),
                _pixels(asset),
                str(asset.get("asset_uuid")),
            ),
        )
    return max(assets, key=_leader_key)


def _quality(asset: dict[str, object]) -> float:
    if asset.get("series_quality_score") is not None:
        return float(asset["series_quality_score"])
    signals = [
        (asset.get("sharpness_percentile"), 0.45),
        (asset.get("contrast_percentile"), 0.20),
        (asset.get("apple_overall_percentile"), 0.10),
        (asset.get("technical_quality"), 0.25),
    ]
    available = [(float(value), weight) for value, weight in signals if value is not None]
    denominator = sum(weight for _, weight in available)
    return sum(value * weight for value, weight in available) / denominator if denominator else 0.0


def _series_quality(
    asset: dict[str, object],
    signals: dict[str, dict[str, object]],
    personal_delta: float,
) -> float:
    values: list[tuple[float, float]] = [(_quality(asset) * 100.0, 0.35)]
    score_fields = (
        ("aesthetics", "overall_score", 0.20, lambda value: (value + 1.0) * 50.0),
        ("nima_aesthetics", "aesthetic_score", 0.15, lambda value: value),
        ("mobileclip", "aesthetic_score", 0.15, lambda value: value),
        ("musiq_quality", "quality_score", 0.10, lambda value: value),
    )
    for signal_kind, score_field, weight, transform in score_fields:
        signal = signals.get(signal_kind)
        value = signal.get("value") if signal and signal.get("status") == "ready" else None
        raw = value.get(score_field) if isinstance(value, dict) else None
        if isinstance(raw, (int, float)):
            values.append((max(0.0, min(100.0, transform(float(raw)))), weight))
    faces = signals.get("faces")
    face_value = faces.get("value") if faces and faces.get("status") == "ready" else None
    if isinstance(face_value, dict) and int(face_value.get("face_count") or 0) > 0:
        capture = face_value.get("best_capture_quality")
        if isinstance(capture, (int, float)):
            values.append((max(0.0, min(100.0, float(capture) * 100.0)), 0.20))
    codex = signals.get("codex_vision")
    codex_value = codex.get("value") if codex and codex.get("status") == "ready" else None
    if isinstance(codex_value, dict):
        if isinstance(codex_value.get("series_rank"), int):
            rank = max(1, int(codex_value["series_rank"]))
            values.append((max(0.0, 105.0 - rank * 10.0), 0.25))
        if isinstance(codex_value.get("moment_score"), (int, float)):
            values.append((float(codex_value["moment_score"]), 0.10))
    denominator = sum(weight for _, weight in values)
    generic = sum(value * weight for value, weight in values) / denominator
    return max(0.0, min(100.0, generic + max(-8.0, min(8.0, personal_delta)))) / 100.0


def _pixels(asset: dict[str, object]) -> int:
    return int(asset.get("width") or 0) * int(asset.get("height") or 0)


def _aspect_ratio(asset: dict[str, object]) -> float:
    return float(asset.get("width") or 1) / float(asset.get("height") or 1)


def _time_delta(left: object, right: object) -> float | None:
    if not left or not right:
        return None
    try:
        return abs(
            (datetime.fromisoformat(str(left)) - datetime.fromisoformat(str(right))).total_seconds()
        )
    except ValueError:
        return None
