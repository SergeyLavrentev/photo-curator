from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter

from photo_curator.photos.provider import PhotoAlbum, PhotoAsset, PhotoLibrary


class FakePhotosProvider:
    ALBUM_ID = "demo-regular-album"

    def __init__(self, fixture_root: Path) -> None:
        self.fixture_root = fixture_root.resolve()
        self.fixture_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._assets = self._ensure_fixtures()

    def refresh_library(self) -> None:
        return None

    def get_current_library(self) -> PhotoLibrary:
        return PhotoLibrary(
            library_path="Synthetic Demo Library",
            database_path=None,
            database_version="demo-1",
            photos_app_version="demo",
            fingerprint=hashlib.sha256(b"photo-curator-demo-library-v1").hexdigest(),
        )

    def list_regular_albums(self) -> list[PhotoAlbum]:
        return [
            PhotoAlbum(
                id=self.ALBUM_ID,
                name="Черногория",
                folder_path="Путешествия/2026",
                photo_count=len(self._assets),
                video_count=2,
            )
        ]

    def list_shared_albums(self) -> list[PhotoAlbum]:
        return [
            PhotoAlbum(
                id="demo-shared-album",
                name="Семейный Shared Album",
                is_shared=True,
                photo_count=4,
            )
        ]

    def list_assets(self, album_id: str) -> list[PhotoAsset]:
        if album_id != self.ALBUM_ID:
            raise KeyError(album_id)
        return list(self._assets)

    def list_shared_assets(self, album_id: str) -> list[PhotoAsset]:
        if album_id != "demo-shared-album":
            raise KeyError(album_id)
        return list(self._assets[:4])

    def refresh_assets(self, asset_uuids: list[str]) -> list[PhotoAsset]:
        wanted = set(asset_uuids)
        return [asset for asset in self._assets if asset.uuid in wanted]

    def asset_still_in_album(self, album_id: str, asset_uuid: str) -> bool:
        return album_id == self.ALBUM_ID and any(a.uuid == asset_uuid for a in self._assets)

    def _ensure_fixtures(self) -> list[PhotoAsset]:
        base = _base_image((1200, 800))
        variants: list[tuple[str, Image.Image, dict[str, object]]] = [
            ("demo-001", base, {"favorite": True}),
            ("demo-002", base.copy(), {}),
            ("demo-003", base.resize((600, 400)), {}),
            ("demo-004", base.filter(ImageFilter.GaussianBlur(8)), {}),
            ("demo-005", ImageEnhance.Brightness(base).enhance(0.18), {}),
            ("demo-006", ImageEnhance.Brightness(base).enhance(2.2), {}),
            ("demo-007", ImageEnhance.Contrast(base).enhance(0.12), {}),
            ("demo-008", _shifted_image(base, 8), {}),
            ("demo-009", _landscape((1200, 800), (62, 104, 151)), {"has_adjustments": True}),
            (
                "demo-010",
                _landscape((1200, 800), (130, 84, 52)),
                {"apple_scores": {"overall": 0.85}},
            ),
            ("demo-011", _landscape((900, 1200), (56, 124, 91)), {}),
            ("demo-012", _landscape((1200, 800), (116, 64, 101)), {}),
        ]
        assets = []
        for index, (uuid, image, flags) in enumerate(variants, start=1):
            path = self.fixture_root / f"{uuid}.jpg"
            if not path.exists():
                image.convert("RGB").save(path, "JPEG", quality=92)
            width, height = image.size
            assets.append(
                PhotoAsset(
                    uuid=uuid,
                    original_filename=path.name,
                    current_filename=path.name,
                    taken_at=f"2026-06-12T10:{index:02d}:00+03:00",
                    width=width,
                    height=height,
                    original_width=width,
                    original_height=height,
                    favorite=bool(flags.get("favorite")),
                    has_adjustments=bool(flags.get("has_adjustments")),
                    source_path=path,
                    apple_scores=flags.get("apple_scores"),
                )
            )
        return assets


def _base_image(size: tuple[int, int]) -> Image.Image:
    image = _landscape(size, (49, 103, 137))
    draw = ImageDraw.Draw(image)
    draw.ellipse((330, 170, 760, 600), fill=(235, 174, 78), outline=(255, 230, 174), width=8)
    draw.rectangle((60, 560, 1140, 800), fill=(35, 91, 65))
    draw.line((0, 620, 1200, 400), fill=(248, 244, 220), width=13)
    return image


def _landscape(size: tuple[int, int], color: tuple[int, int, int]) -> Image.Image:
    width, height = size
    gradient = np.zeros((height, width, 3), dtype=np.uint8)
    for channel, value in enumerate(color):
        gradient[:, :, channel] = np.linspace(value // 2, min(255, value + 70), height)[:, None]
    return Image.fromarray(gradient, mode="RGB")


def _shifted_image(image: Image.Image, pixels: int) -> Image.Image:
    shifted = Image.new("RGB", image.size, (0, 0, 0))
    shifted.paste(image.crop((0, 0, image.width - pixels, image.height)), (pixels, 0))
    return shifted
