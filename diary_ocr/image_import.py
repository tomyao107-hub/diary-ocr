from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


SUPPORTED_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".tif", ".webp"
}


def natural_key(path: Path) -> list:
    name_key = [
        int(part) if part.isdigit() else part.casefold()
        for part in re.split(r"(\d+)", path.name)
    ]
    return [*name_key, path.parent.as_posix().casefold()]


def unique_path(directory: Path, name: str) -> Path:
    candidate = directory / name
    if not candidate.exists():
        return candidate
    source = Path(name)
    counter = 2
    while True:
        candidate = directory / f"{source.stem}_{counter}{source.suffix.lower()}"
        if not candidate.exists():
            return candidate
        counter += 1


def _copy_atomic(source: Path, destination: Path) -> None:
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    shutil.copy2(source, temporary)
    temporary.replace(destination)


@dataclass(frozen=True)
class ImportedPage:
    source: Path
    page: Path


def discover_images(folder: Path) -> list[Path]:
    folder = Path(folder)
    discovered = []
    for root, directories, files in os.walk(folder, topdown=True):
        directories[:] = sorted(
            [
                name for name in directories
                if not name.startswith(".") and not (Path(root) / name).is_symlink()
            ],
            key=str.casefold,
        )
        for name in files:
            path = Path(root) / name
            if not name.startswith(".") and path.suffix.lower() in SUPPORTED_EXTENSIONS:
                discovered.append(path)
    return sorted(discovered, key=natural_key)


def import_images(project_root: Path, paths: Iterable[Path]) -> list[ImportedPage]:
    project_root = Path(project_root).resolve()
    sources_dir = project_root / "sources"
    pages_dir = project_root / "pages"
    sources_dir.mkdir(parents=True, exist_ok=True)
    pages_dir.mkdir(parents=True, exist_ok=True)
    imported = []
    for raw_path in paths:
        source = Path(raw_path).resolve()
        if not source.is_file() or source.suffix.lower() not in SUPPORTED_EXTENSIONS:
            continue
        source_copy = unique_path(sources_dir, source.name)
        page_copy = unique_path(pages_dir, source.name)
        try:
            _copy_atomic(source, source_copy)
            _copy_atomic(source, page_copy)
        except OSError:
            source_copy.unlink(missing_ok=True)
            page_copy.unlink(missing_ok=True)
            raise
        imported.append(ImportedPage(source_copy, page_copy))
    return imported


def import_folder(project_root: Path, folder: Path) -> list[ImportedPage]:
    return import_images(project_root, discover_images(folder))
