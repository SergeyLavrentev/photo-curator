from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime
from itertools import combinations
from pathlib import Path

from PIL import Image

from photo_curator.analysis.hashes import hamming_distance
from photo_curator.analysis.similarity import histogram_similarity, normalized_pixel_mae


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
) -> list[DuplicateGroupResult]:
    candidates = [asset for asset in assets if asset.get("phash")]
    union = UnionFind([str(asset["asset_uuid"]) for asset in candidates])
    pair_evidence: dict[frozenset[str], dict[str, object]] = {}
    for left, right in _candidate_pairs(candidates):
        evidence = _confirm_pair(
            left,
            right,
            strict_phash_distance=strict_phash_distance,
            relaxed_phash_distance=relaxed_phash_distance,
            time_window_seconds=time_window_seconds,
        )
        if evidence:
            left_uuid, right_uuid = str(left["asset_uuid"]), str(right["asset_uuid"])
            union.union(left_uuid, right_uuid)
            pair_evidence[frozenset((left_uuid, right_uuid))] = evidence

    by_root: dict[str, list[dict[str, object]]] = {}
    for asset in candidates:
        by_root.setdefault(union.find(str(asset["asset_uuid"])), []).append(asset)
    groups = []
    for index, members in enumerate(
        (group for group in by_root.values() if len(group) > 1), start=1
    ):
        groups.append(_build_group(index, members, pair_evidence, relaxed_phash_distance))
    return groups


def _candidate_pairs(
    assets: list[dict[str, object]],
) -> Iterator[tuple[dict[str, object], dict[str, object]]]:
    if len(assets) <= 3000:
        yield from combinations(assets, 2)
        return
    by_uuid = {str(asset["asset_uuid"]): asset for asset in assets}
    candidate_ids: set[tuple[str, str]] = set()
    bands: dict[tuple[int, str], list[str]] = {}
    bursts: dict[str, list[str]] = {}
    for asset in assets:
        uuid = str(asset["asset_uuid"])
        hash_value = str(asset["phash"])
        for band in range(4):
            bands.setdefault((band, hash_value[band * 4 : band * 4 + 4]), []).append(uuid)
        if asset.get("burst_key"):
            bursts.setdefault(str(asset["burst_key"]), []).append(uuid)
    for bucket in [*bands.values(), *bursts.values()]:
        for left, right in combinations(sorted(bucket), 2):
            candidate_ids.add((left, right))
    for left, right in sorted(candidate_ids):
        yield by_uuid[left], by_uuid[right]


def _confirm_pair(
    left: dict[str, object],
    right: dict[str, object],
    *,
    strict_phash_distance: int,
    relaxed_phash_distance: int,
    time_window_seconds: int,
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
    pixel_mae = (
        _preview_mae(left, right)
        if not exact and (cheap_strict or cheap_relaxed or burst)
        else None
    )
    strict = (
        cheap_strict
        and dhash_distance <= 12
        and histogram >= 0.72
        and (pixel_mae is None or pixel_mae <= 0.18)
    )
    relaxed = cheap_relaxed and dhash_distance <= 18 and (pixel_mae is None or pixel_mae <= 0.22)
    burst_confirmed = burst and dhash_distance <= 24 and histogram >= 0.60
    if not (exact or strict or relaxed or burst_confirmed):
        return None
    confidence = 1.0 if exact else max(0.75, 1.0 - distance / 16.0)
    return {
        "exact": exact,
        "phash_distance": distance,
        "dhash_distance": dhash_distance,
        "normalized_pixel_mae": pixel_mae,
        "aspect_delta": aspect_delta,
        "time_delta": time_delta,
        "histogram_similarity": histogram,
        "confidence": confidence,
    }


def _preview_mae(left: dict[str, object], right: dict[str, object]) -> float | None:
    left_path = Path(str(left.get("review_path") or ""))
    right_path = Path(str(right.get("review_path") or ""))
    if not left_path.is_file() or not right_path.is_file():
        return None
    try:
        with Image.open(left_path) as left_image, Image.open(right_path) as right_image:
            return normalized_pixel_mae(left_image, right_image)
    except (OSError, ValueError):
        return None


def _build_group(
    index: int,
    assets: list[dict[str, object]],
    evidence: dict[frozenset[str], dict[str, object]],
    relaxed_distance: int,
) -> DuplicateGroupResult:
    leader = max(assets, key=_leader_key)
    max_pixels = max(_pixels(asset) for asset in assets) or 1
    flags = []
    if _pixels(leader) < max_pixels * 0.75:
        flags.append("leader_lower_resolution")
    leader_render_hash = leader.get("normalized_pixel_hash")
    group_kind = (
        "exact"
        if leader_render_hash
        and all(asset.get("normalized_pixel_hash") == leader_render_hash for asset in assets)
        else "near"
    )
    ambiguous = any(
        hamming_distance(str(asset["phash"]), str(leader["phash"])) > relaxed_distance
        for asset in assets
    )
    if ambiguous:
        flags.append("ambiguous_duplicate")
        group_kind = "ambiguous"
    members = []
    confidences = []
    for asset in sorted(assets, key=lambda value: str(value["asset_uuid"])):
        uuid = str(asset["asset_uuid"])
        leader_uuid = str(leader["asset_uuid"])
        pair = evidence.get(frozenset((uuid, leader_uuid)), {}) if uuid != leader_uuid else {}
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
        group_id=f"group-{index:04d}",
        kind=group_kind,
        confidence=min(confidences),
        leader_uuid=str(leader["asset_uuid"]),
        flags=flags,
        members=members,
    )


def _leader_key(asset: dict[str, object]) -> tuple[object, ...]:
    return (
        bool(asset.get("favorite")),
        bool(asset.get("has_adjustments")),
        bool(asset.get("burst_default_pick")),
        _pixels(asset),
        _quality(asset),
        str(asset.get("asset_uuid")),
    )


def _quality(asset: dict[str, object]) -> float:
    signals = [
        (asset.get("sharpness_percentile"), 0.45),
        (asset.get("contrast_percentile"), 0.20),
        (asset.get("apple_overall_percentile"), 0.10),
        (asset.get("technical_quality"), 0.25),
    ]
    available = [(float(value), weight) for value, weight in signals if value is not None]
    denominator = sum(weight for _, weight in available)
    return sum(value * weight for value, weight in available) / denominator if denominator else 0.0


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
