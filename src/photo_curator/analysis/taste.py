from __future__ import annotations

import base64
import math
from dataclasses import dataclass

import numpy as np

from photo_curator.db import repository

TASTE_PROFILE_SCHEMA_VERSION = 1
TASTE_MODEL_VERSION = "pairwise-linear-v3-independent-holdout"
MIN_CALIBRATION_PAIRS = 3


class TasteProfileError(ValueError):
    """Preference data cannot produce or apply a trustworthy local taste model."""


@dataclass(frozen=True, slots=True)
class TasteModel:
    feature_schema: str
    model_version: str
    weights: np.ndarray
    training_examples: int
    reliability: float

    def personal_delta(self, feature_signal: dict[str, object]) -> float:
        try:
            schema, vector, _ = feature_vector(feature_signal)
        except TasteProfileError:
            return 0.0
        if schema != self.feature_schema or vector.size != self.weights.size:
            return 0.0
        raw = float(np.dot(self.weights, _unit(vector)))
        return math.tanh(raw * 2.0) * 20.0 * self.reliability


def capture_preference(
    connection,
    *,
    project_id: str,
    left_uuid: str,
    right_uuid: str,
    preferred_uuid: str,
    split: str = "calibration",
    profile_id: str = "default",
) -> str:
    if preferred_uuid not in {left_uuid, right_uuid}:
        raise TasteProfileError("preferred_uuid должен быть одним из сравниваемых фото")
    signals = repository.analysis_signals_by_asset(connection, project_id)
    try:
        left = signals[left_uuid]["feature_print"]
        right = signals[right_uuid]["feature_print"]
    except KeyError as error:
        raise TasteProfileError("Для пары отсутствует native feature print") from error
    left_schema, _, left_base64 = feature_vector(left)
    right_schema, _, right_base64 = feature_vector(right)
    if left_schema != right_schema:
        raise TasteProfileError("Feature schema пары не совпадает")
    return repository.add_preference_example(
        connection,
        profile_id=profile_id,
        project_id=project_id,
        left_uuid=left_uuid,
        right_uuid=right_uuid,
        preferred_uuid=preferred_uuid,
        split=split,
        feature_schema=left_schema,
        left_feature_base64=left_base64,
        right_feature_base64=right_base64,
    )


def capture_preference_vectors(
    connection,
    *,
    left_uuid: str,
    right_uuid: str,
    preferred_uuid: str,
    left_feature_schema: str,
    left_feature_base64: str,
    right_feature_schema: str,
    right_feature_base64: str,
    split: str = "calibration",
    profile_id: str = "default",
) -> str:
    """Persist an explicit preference collected outside an analysis project."""
    if preferred_uuid not in {left_uuid, right_uuid}:
        raise TasteProfileError("preferred_uuid должен быть одним из сравниваемых фото")
    if left_feature_schema != right_feature_schema:
        raise TasteProfileError("Feature schema пары не совпадает")
    # Decode now so corrupt or incompatible vectors never enter the profile.
    for encoded in (left_feature_base64, right_feature_base64):
        try:
            vector = np.frombuffer(base64.b64decode(encoded, validate=True), dtype="<f4")
        except (ValueError, TypeError) as error:
            raise TasteProfileError("Preference feature data повреждены") from error
        if vector.size <= 0 or not np.all(np.isfinite(vector)):
            raise TasteProfileError("Preference feature data некорректны")
    return repository.add_preference_example(
        connection,
        profile_id=profile_id,
        project_id=None,
        left_uuid=left_uuid,
        right_uuid=right_uuid,
        preferred_uuid=preferred_uuid,
        split=split,
        feature_schema=left_feature_schema,
        left_feature_base64=left_feature_base64,
        right_feature_base64=right_feature_base64,
    )


def train_taste_profile(connection, profile_id: str = "default") -> dict[str, object]:
    repository.ensure_taste_profile(connection, profile_id)
    examples = repository.list_preference_examples(connection, profile_id)
    calibration = [example for example in examples if example["split"] == "calibration"]
    if len(calibration) < MIN_CALIBRATION_PAIRS:
        raise TasteProfileError(f"Нужно минимум {MIN_CALIBRATION_PAIRS} calibration comparisons")
    schemas = {str(example["feature_schema"]) for example in examples}
    if len(schemas) != 1:
        raise TasteProfileError("Preference examples используют несовместимые feature schemas")
    schema = schemas.pop()
    decoded = [_decode_example(example) for example in examples]
    dimension = decoded[0][0].size
    if any(left.size != dimension or right.size != dimension for left, right, _, _ in decoded):
        raise TasteProfileError("Feature dimensions не совпадают")

    weights = np.zeros(dimension, dtype=np.float32)
    learning_rate = 0.12
    regularization = 0.015
    calibration_vectors = [item for item in decoded if item[3] == "calibration"]
    for _ in range(160):
        for left, right, preferred_left, _ in calibration_vectors:
            difference = _unit(left) - _unit(right)
            if not preferred_left:
                difference = -difference
            logit = float(np.dot(weights, difference))
            probability = 1.0 / (1.0 + math.exp(-max(-20.0, min(20.0, logit))))
            gradient = (probability - 1.0) * difference + regularization * weights
            weights -= learning_rate * gradient.astype(np.float32)
    norm = float(np.linalg.norm(weights))
    if norm > 3.0:
        weights *= 3.0 / norm

    calibration_accuracy = _pairwise_accuracy(weights, calibration_vectors)
    held_out_vectors = [item for item in decoded if item[3] == "held_out"]
    held_out_accuracy = _pairwise_accuracy(weights, held_out_vectors) if held_out_vectors else None
    evidence: dict[str, object] = {
        "calibration_pairs": len(calibration_vectors),
        "calibration_accuracy": calibration_accuracy,
        "held_out_pairs": len(held_out_vectors),
        "held_out_accuracy": held_out_accuracy,
        "calibration_unique_assets": len(
            {str(value[key]) for value in calibration for key in ("left_uuid", "right_uuid")}
        ),
        "held_out_unique_assets": len(
            {
                str(value[key])
                for value in examples
                if value["split"] == "held_out"
                for key in ("left_uuid", "right_uuid")
            }
        ),
        "weights_l2": round(float(np.linalg.norm(weights)), 6),
    }
    repository.save_taste_model(
        connection,
        profile_id,
        feature_schema=schema,
        model_version=TASTE_MODEL_VERSION,
        weights_base64=_encode_weights(weights),
        dimension=dimension,
        training_examples=len(calibration_vectors),
        evidence=evidence,
    )
    return repository.get_taste_profile(connection, profile_id)


def load_taste_model(connection, profile_id: str = "default") -> TasteModel | None:
    try:
        profile = repository.get_taste_profile(connection, profile_id)
    except KeyError:
        return None
    if profile.get("status") == "paused":
        return None
    raw = profile.get("weights_base64")
    dimension = profile.get("dimension")
    schema = profile.get("feature_schema")
    version = profile.get("model_version")
    if not all((raw, dimension, schema, version)):
        return None
    if str(version) != TASTE_MODEL_VERSION:
        return None
    try:
        weights = np.frombuffer(base64.b64decode(str(raw), validate=True), dtype="<f4").copy()
    except (ValueError, TypeError) as error:
        raise TasteProfileError("Taste profile weights повреждены") from error
    if weights.size != int(dimension):
        raise TasteProfileError("Taste profile dimension не совпадает")
    return TasteModel(
        feature_schema=str(schema),
        model_version=str(version),
        weights=weights,
        training_examples=int(profile["training_examples"]),
        reliability=_taste_reliability(profile),
    )


def _taste_reliability(profile: dict[str, object]) -> float:
    """Shrink personal influence until preference quality is demonstrated."""
    evidence = profile.get("evidence")
    values = evidence if isinstance(evidence, dict) else {}
    held_out_pairs = int(values.get("held_out_pairs") or 0)
    held_out_accuracy = values.get("held_out_accuracy")
    calibration_pairs = int(values.get("calibration_pairs") or 0)
    calibration_accuracy = values.get("calibration_accuracy")
    if held_out_pairs >= 3 and isinstance(held_out_accuracy, (int, float)):
        accuracy = float(held_out_accuracy)
        independent_assets = int(values.get("held_out_unique_assets") or 0)
        evaluation_strength = min(1.0, independent_assets / 24.0)
    elif isinstance(calibration_accuracy, (int, float)):
        accuracy = float(calibration_accuracy)
        # In-sample accuracy is optimistic and therefore gets at most half trust.
        evaluation_strength = min(0.5, calibration_pairs / 60.0)
    else:
        return 0.0
    skill = max(0.0, min(1.0, (accuracy - 0.5) / 0.5))
    independent_training_assets = int(values.get("calibration_unique_assets") or 0)
    sample_strength = min(1.0, independent_training_assets / 24.0)
    return round(min(sample_strength, evaluation_strength) * skill, 3)


def compatible_taste_model(
    connection,
    signals_by_asset: dict[str, dict[str, dict[str, object]]],
    profile_id: str = "default",
) -> TasteModel | None:
    """Return a model only when every current Vision feature uses its trained schema."""
    try:
        profile = repository.get_taste_profile(connection, profile_id)
    except KeyError:
        return None
    if profile.get("status") == "paused":
        return None
    if profile.get("model_version") not in {None, TASTE_MODEL_VERSION}:
        try:
            profile = train_taste_profile(connection, profile_id)
        except TasteProfileError as error:
            repository.mark_taste_profile_stale(
                connection,
                reason="model_version_mismatch",
                detail=str(error),
                profile_id=profile_id,
            )
            return None
    model = load_taste_model(connection, profile_id)
    if model is None:
        return None
    observed: set[str] = set()
    for signals in signals_by_asset.values():
        feature = signals.get("feature_print")
        if not feature:
            continue
        try:
            schema, _, _ = feature_vector(feature)
        except TasteProfileError:
            continue
        observed.add(schema)
    if not observed:
        return None
    compatible = observed == {model.feature_schema}
    repository.set_taste_profile_compatibility(
        connection,
        compatible=compatible,
        observed_feature_schemas=sorted(observed),
        profile_id=profile_id,
    )
    return model if compatible else None


def feature_vector(signal: dict[str, object]) -> tuple[str, np.ndarray, str]:
    if signal.get("status") != "ready":
        raise TasteProfileError("Feature print не готов")
    value = signal.get("value")
    if not isinstance(value, dict):
        raise TasteProfileError("Feature print payload отсутствует")
    element_type = value.get("element_type")
    element_count = value.get("element_count")
    data_base64 = value.get("data_base64")
    revision = value.get("revision")
    if element_type != 1 or not isinstance(element_count, int) or element_count <= 0:
        raise TasteProfileError("Поддерживается только Vision Float32 feature print")
    if not isinstance(data_base64, str) or not isinstance(revision, int):
        raise TasteProfileError("Feature print provenance неполон")
    try:
        raw = base64.b64decode(data_base64, validate=True)
    except ValueError as error:
        raise TasteProfileError("Feature print base64 повреждён") from error
    vector = np.frombuffer(raw, dtype="<f4").copy()
    if vector.size != element_count or not np.all(np.isfinite(vector)):
        raise TasteProfileError("Feature print dimension/data некорректны")
    engine = signal.get("engine_name")
    version = signal.get("engine_version")
    request_revision = signal.get("request_revision")
    schema = (
        f"{engine}@{version}/request-{request_revision}/feature-{revision}/float32-{element_count}"
    )
    return schema, vector, data_base64


def _decode_example(
    example: dict[str, object],
) -> tuple[np.ndarray, np.ndarray, bool, str]:
    try:
        left = np.frombuffer(
            base64.b64decode(str(example["left_feature_base64"]), validate=True),
            dtype="<f4",
        ).copy()
        right = np.frombuffer(
            base64.b64decode(str(example["right_feature_base64"]), validate=True),
            dtype="<f4",
        ).copy()
    except (ValueError, TypeError) as error:
        raise TasteProfileError("Preference feature data повреждены") from error
    if not np.all(np.isfinite(left)) or not np.all(np.isfinite(right)):
        raise TasteProfileError("Preference feature data содержат non-finite values")
    return (
        left,
        right,
        str(example["preferred_uuid"]) == str(example["left_uuid"]),
        str(example["split"]),
    )


def _pairwise_accuracy(
    weights: np.ndarray, examples: list[tuple[np.ndarray, np.ndarray, bool, str]]
) -> float:
    if not examples:
        return 0.0
    correct = 0
    for left, right, preferred_left, _ in examples:
        predicted_left = float(np.dot(weights, _unit(left) - _unit(right))) > 0
        correct += int(predicted_left == preferred_left)
    return correct / len(examples)


def _unit(vector: np.ndarray) -> np.ndarray:
    norm = float(np.linalg.norm(vector))
    return vector / norm if norm > 1e-8 else vector


def _encode_weights(weights: np.ndarray) -> str:
    return base64.b64encode(weights.astype("<f4", copy=False).tobytes()).decode("ascii")
