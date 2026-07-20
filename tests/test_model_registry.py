from __future__ import annotations

from pathlib import Path

import pytest

from photo_curator.analysis.model_registry import (
    ModelRegistryError,
    approve_model,
    list_models,
    model_sha256,
    register_model,
    revalidate_model,
)
from photo_curator.db.connection import database_connection
from photo_curator.db.migrations import migrate


def make_model(path: Path) -> Path:
    path.mkdir()
    (path / "Manifest.json").write_text('{"model": 1}', encoding="utf-8")
    weights = path / "weights"
    weights.mkdir()
    (weights / "weight.bin").write_bytes(b"stable-weights")
    return path


def test_registry_fingerprints_packages_deterministically(tmp_path: Path) -> None:
    first = make_model(tmp_path / "first.mlpackage")
    second = make_model(tmp_path / "second.mlpackage")

    assert model_sha256(first) == model_sha256(second)
    (second / "weights/weight.bin").write_bytes(b"changed")
    assert model_sha256(first) != model_sha256(second)


def test_restricted_model_cannot_be_approved(tmp_path: Path) -> None:
    model_path = make_model(tmp_path / "research.mlpackage")
    with database_connection(tmp_path / "db.sqlite3") as connection:
        migrate(connection)
        model = register_model(
            connection,
            name="Research model",
            version="1",
            model_path=model_path,
            license_id="research-only",
            source_url="https://example.invalid/model",
            commercial_use_allowed=False,
        )

        with pytest.raises(ModelRegistryError, match="does not allow commercial"):
            approve_model(connection, str(model["id"]), compatibility={"compatible": True})

        assert list_models(connection)[0]["status"] == "candidate"


def test_approval_requires_compatibility_and_immutable_checksum(tmp_path: Path) -> None:
    model_path = make_model(tmp_path / "licensed.mlmodelc")
    with database_connection(tmp_path / "db.sqlite3") as connection:
        migrate(connection)
        model = register_model(
            connection,
            name="Licensed model",
            version="2026-07",
            model_path=model_path,
            license_id="Apache-2.0",
            source_url=None,
            commercial_use_allowed=True,
        )
        model_id = str(model["id"])

        with pytest.raises(ModelRegistryError, match="compatibility"):
            approve_model(connection, model_id, compatibility={"compatible": False})

        approved = approve_model(
            connection,
            model_id,
            compatibility={"compatible": True, "macos": "26.5"},
        )
        assert approved["status"] == "approved"
        assert approved["compute_policy"] == "all"

        (model_path / "weights/weight.bin").write_bytes(b"tampered")
        assert revalidate_model(connection, model_id) is False
        assert list_models(connection)[0]["status"] == "invalid"
