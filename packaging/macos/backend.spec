# -*- mode: python ; coding: utf-8 -*-

from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_submodules, copy_metadata
from pathlib import Path

project_root = Path(SPECPATH).parents[1]

photo_data = collect_data_files("photo_curator") + copy_metadata("photo-curator")
osxphotos_data, osxphotos_binaries, osxphotos_hidden = collect_all("osxphotos")
utitools_data = collect_data_files("utitools")
photoscript_data = collect_data_files("photoscript")
osxmetadata_data = collect_data_files("osxmetadata")

analysis = Analysis(
    [str(project_root / "src/photo_curator/__main__.py")],
    pathex=[str(project_root / "src")],
    binaries=osxphotos_binaries,
    datas=photo_data + osxphotos_data + utitools_data + photoscript_data + osxmetadata_data,
    hiddenimports=(
        collect_submodules("photo_curator")
        + osxphotos_hidden
        + collect_submodules("bitstring")
    ),
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest", "ruff"],
    noarchive=False,
)
pyz = PYZ(analysis.pure)

executable = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="photo-curator-backend",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
)

bundle = COLLECT(
    executable,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    name="photo-curator-backend",
)
