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
    try:
        shutil.copy2(source, temporary)
        temporary.replace(destination)
    except Exception:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise


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


def import_images(
    project_root: Path,
    paths: Iterable[Path],
    *,
    skip_errors: bool = False,
    on_error=None,
) -> list[ImportedPage]:
    project_root = Path(project_root).resolve()
    sources_dir = project_root / "sources"
    pages_dir = project_root / "pages"
    sources_dir.mkdir(parents=True, exist_ok=True)
    pages_dir.mkdir(parents=True, exist_ok=True)
    imported = []
    for raw_path in paths:
        try:
            source = Path(raw_path).resolve()
        except (OSError, RuntimeError):
            continue
        if not source.is_file() or source.suffix.lower() not in SUPPORTED_EXTENSIONS:
            continue
        # Avoid re-importing a file that already lives under this project tree.
        try:
            source.relative_to(project_root)
            already_in_project = True
        except ValueError:
            already_in_project = False
        if already_in_project:
            # If the source is already a project page, just keep using it.
            if source.parent.resolve() == pages_dir.resolve():
                imported.append(ImportedPage(source, source))
                continue
        source_copy = unique_path(sources_dir, source.name)
        page_copy = unique_path(pages_dir, source.name)
        try:
            _copy_atomic(source, source_copy)
            _copy_atomic(source, page_copy)
        except OSError as exc:
            source_copy.unlink(missing_ok=True)
            page_copy.unlink(missing_ok=True)
            if on_error is not None:
                on_error(source, exc)
            if skip_errors:
                continue
            raise
        imported.append(ImportedPage(source_copy, page_copy))
    return imported


def import_folder(
    project_root: Path,
    folder: Path,
    *,
    on_error=None,
) -> list[ImportedPage]:
    # Folder imports tolerate individual unreadable files so one locked image
    # does not abort the whole batch.
    return import_images(
        project_root,
        discover_images(folder),
        skip_errors=True,
        on_error=on_error,
    )
