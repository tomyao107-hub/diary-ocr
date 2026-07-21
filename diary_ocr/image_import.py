from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .image_preprocess import apply_exif_orientation, heic_to_jpeg_bytes
from .page_model import (
    ImportReport,
    file_sha256,
    find_duplicate_sha,
    load_page_catalog,
    register_page,
)


SUPPORTED_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".tif", ".webp",
    ".heic", ".heif",
}

HEIC_EXTENSIONS = {".heic", ".heif"}


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


def _write_bytes_atomic(data: bytes, destination: Path) -> None:
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    try:
        temporary.write_bytes(data)
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
    sha256: str | None = None
    duplicate_of: str | None = None


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


def _materialize_working_page(source_copy: Path, page_copy: Path) -> None:
    """Copy source into pages/, converting HEIC when needed; apply EXIF orientation for raster."""
    suffix = source_copy.suffix.lower()
    if suffix in HEIC_EXTENSIONS:
        jpeg_bytes = heic_to_jpeg_bytes(source_copy)
        if jpeg_bytes is None:
            raise OSError(
                "无法解码 HEIC/HEIF。请安装 pillow-heif：pip install pillow-heif"
            )
        # Force JPEG page path.
        if page_copy.suffix.lower() not in {".jpg", ".jpeg"}:
            page_copy = page_copy.with_suffix(".jpg")
        _write_bytes_atomic(jpeg_bytes, page_copy)
        return page_copy
    # Standard formats: copy then normalize orientation in-place for working page.
    _copy_atomic(source_copy, page_copy)
    try:
        from PIL import Image

        with Image.open(page_copy) as image:
            fixed = apply_exif_orientation(image)
            if fixed is not image:
                if fixed.mode not in ("RGB", "L"):
                    fixed = fixed.convert("RGB")
                temporary = page_copy.with_suffix(page_copy.suffix + ".tmp")
                if page_copy.suffix.lower() in {".jpg", ".jpeg"}:
                    fixed.save(temporary, quality=92, optimize=True)
                else:
                    fixed.save(temporary)
                temporary.replace(page_copy)
    except Exception:
        # Keep the raw copy if orientation fix fails.
        pass
    return page_copy


def import_images(
    project_root: Path,
    paths: Iterable[Path],
    *,
    skip_errors: bool = False,
    on_error=None,
    skip_duplicates: bool = True,
    report: ImportReport | None = None,
) -> list[ImportedPage]:
    project_root = Path(project_root).resolve()
    sources_dir = project_root / "sources"
    pages_dir = project_root / "pages"
    sources_dir.mkdir(parents=True, exist_ok=True)
    pages_dir.mkdir(parents=True, exist_ok=True)
    imported: list[ImportedPage] = []
    report = report if report is not None else ImportReport()
    catalog = load_page_catalog(project_root)

    for raw_path in paths:
        try:
            source = Path(raw_path).resolve()
        except (OSError, RuntimeError):
            report.skipped += 1
            report.messages.append(f"跳过不可解析路径：{raw_path}")
            continue
        if not source.is_file():
            report.skipped += 1
            continue
        suffix = source.suffix.lower()
        if suffix not in SUPPORTED_EXTENSIONS:
            report.unsupported += 1
            report.messages.append(f"不支持的格式：{source.name}")
            continue

        # Avoid re-importing a file that already lives under this project tree.
        try:
            source.relative_to(project_root)
            already_in_project = True
        except ValueError:
            already_in_project = False
        if already_in_project:
            if source.parent.resolve() == pages_dir.resolve():
                imported.append(ImportedPage(source, source))
                report.succeeded += 1
                continue

        try:
            digest = file_sha256(source)
        except OSError as exc:
            report.failed += 1
            report.messages.append(f"{source.name}: {exc}")
            if on_error is not None:
                on_error(source, exc)
            if skip_errors:
                continue
            raise

        duplicate = find_duplicate_sha(catalog, digest)
        if duplicate is not None and skip_duplicates:
            report.duplicates += 1
            report.messages.append(
                f"重复跳过：{source.name}（与 {duplicate.path} 内容相同，SHA-256）"
            )
            # Do not re-queue the existing page; caller already has it if needed.
            continue

        source_name = source.name
        page_name = source.name
        if suffix in HEIC_EXTENSIONS:
            page_name = f"{source.stem}.jpg"

        source_copy = unique_path(sources_dir, source_name)
        page_copy = unique_path(pages_dir, page_name)
        try:
            _copy_atomic(source, source_copy)
            page_copy = _materialize_working_page(source_copy, page_copy)
        except OSError as exc:
            source_copy.unlink(missing_ok=True)
            page_copy.unlink(missing_ok=True)
            report.failed += 1
            report.messages.append(f"{source.name}: {exc}")
            if on_error is not None:
                on_error(source, exc)
            if skip_errors:
                continue
            raise

        rel_page = page_copy.relative_to(project_root).as_posix()
        rel_source = source_copy.relative_to(project_root).as_posix()
        width = height = None
        try:
            from PIL import Image

            with Image.open(page_copy) as image:
                width, height = image.size
        except Exception:
            pass
        register_page(
            project_root,
            rel_page,
            rel_source,
            digest,
            width=width,
            height=height,
        )
        # Refresh catalog reference for subsequent duplicates in same batch.
        catalog = load_page_catalog(project_root)
        imported.append(
            ImportedPage(source_copy, page_copy, sha256=digest)
        )
        report.succeeded += 1
    return imported


def import_folder(
    project_root: Path,
    folder: Path,
    *,
    on_error=None,
    skip_duplicates: bool = True,
    report: ImportReport | None = None,
) -> list[ImportedPage]:
    # Folder imports tolerate individual unreadable files so one locked image
    # does not abort the whole batch.
    return import_images(
        project_root,
        discover_images(folder),
        skip_errors=True,
        on_error=on_error,
        skip_duplicates=skip_duplicates,
        report=report,
    )
