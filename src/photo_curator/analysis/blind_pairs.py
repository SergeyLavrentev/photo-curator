"""Conservative scene matching for taste questions, independent of quality predictions."""

from __future__ import annotations

import numpy as np

from photo_curator.analysis.taste import TasteProfileError, feature_vector
from photo_curator.pipeline.duplicates import (
    _confirm_pair,
    _timestamp,
)

PAIR_POLICY = "same-scene-v2"


def scene_pairs(assets, signals):
    """Return directly verified pairs; a shared genre or a transitive group is insufficient.

    At most 12 temporal neighbours per frame bound work on large bursts. Conservative
    omission is preferable to a question about unrelated places. No scores, leaders,
    favourites, dispositions or model-selected groups participate in sampling.
    """
    dated = []
    features = {}
    for asset in assets:
        if (
            asset.get("no_longer_exists")
            or asset.get("cache_state") != "ready"
            or asset.get("media_type") != "image"
        ):
            continue
        asset_id = str(asset["asset_uuid"])
        try:
            schema, vector, _ = feature_vector(signals.get(asset_id, {}).get("feature_print", {}))
        except TasteProfileError:
            continue
        norm = float(np.linalg.norm(vector))
        if norm <= 1e-8:
            continue
        features[asset_id] = (schema, vector / norm)
        timestamp = _timestamp(asset.get("taken_at"))
        if timestamp is not None:
            # Naive timestamps may be compared to one another, never to zoned ones.
            dated.append((timestamp.tzinfo is not None, timestamp, str(asset["asset_uuid"]), asset))
    dated.sort(key=lambda item: item[:3])
    previews = {}
    result = []
    for index, (zoned, timestamp, left_id, left) in enumerate(dated):
        for other_zoned, other_time, right_id, right in dated[index + 1 : index + 13]:
            if zoned != other_zoned or (other_time - timestamp).total_seconds() > 120:
                break
            if (
                not left.get("phash")
                or not right.get("phash")
                or not left.get("dhash")
                or not right.get("dhash")
                or (
                    left.get("normalized_pixel_hash")
                    and left["normalized_pixel_hash"] == right.get("normalized_pixel_hash")
                )
            ):
                continue
            left_schema, left_vector = features[left_id]
            right_schema, right_vector = features[right_id]
            if left_schema != right_schema:
                continue
            similarity = round(float(np.dot(left_vector, right_vector)), 6)
            if similarity < 0.96:
                continue
            evidence = _confirm_pair(
                left,
                right,
                strict_phash_distance=4,
                relaxed_phash_distance=10,
                time_window_seconds=120,
                feature_similarity=similarity,
                preview_cache=previews,
            )
            if evidence is None:
                continue
            # The duplicate engine can accept missing renders or a burst identifier.
            # A blind question requires positive visual evidence for this exact pair.
            pixel_error = evidence["normalized_pixel_mae"]
            if (
                pixel_error is None
                or pixel_error > 0.18
                or evidence["histogram_similarity"] < 0.80
                or evidence["aspect_delta"] > 0.08
                or evidence["phash_distance"] > 16
                or evidence["dhash_distance"] > 18
            ):
                continue
            result.append(((left_id, right_id), evidence))
    return result
