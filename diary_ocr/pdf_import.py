from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .image_import import unique_path


class PDFImportCancelled(Exception):
    pass


@dataclass(frozen=True)
class PDFInfo:
    page_count: int
    needs_password: bool


def _fitz():
    try:
        import fitz
    except ImportError as exc:
        raise RuntimeError("缺少 PyMuPDF，请重新安装 requirements.txt") from exc
    return fitz


def inspect_pdf(path: Path) -> PDFInfo:
    fitz = _fitz()
    try:
        document = fitz.open(str(path))
    except Exception as exc:
        raise ValueError(f"无法打开 PDF：{exc}") from exc
    try:
        return PDFInfo(document.page_count, bool(document.needs_pass))
    finally:
        document.close()


def validate_page_range(page_count: int, start_page: int, end_page: int) -> None:
    if page_count < 1:
        raise ValueError("PDF 没有可导入的页面")
    if start_page < 1 or end_page < start_page or end_page > page_count:
        raise ValueError(f"页码范围必须在 1–{page_count} 之间")


def import_pdf(
    project_root: Path,
    pdf_path: Path,
    start_page: int = 1,
    end_page: int | None = None,
    dpi: int = 300,
    progress: Callable[[int, int, Path], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> list[Path]:
    if not 72 <= int(dpi) <= 600:
        raise ValueError("PDF DPI 必须在 72–600 之间")
    fitz = _fitz()
    project_root = Path(project_root).resolve()
    pdf_path = Path(pdf_path).resolve()
    sources_dir = project_root / "sources"
    pages_dir = project_root / "pages"
    sources_dir.mkdir(parents=True, exist_ok=True)
    pages_dir.mkdir(parents=True, exist_ok=True)
    source_copy = unique_path(sources_dir, pdf_path.name)
    shutil.copy2(pdf_path, source_copy)
    generated: list[Path] = []
    try:
        document = fitz.open(str(source_copy))
        try:
            if document.needs_pass:
                raise ValueError("暂不支持加密 PDF")
            final_page = document.page_count if end_page is None else int(end_page)
            validate_page_range(document.page_count, int(start_page), final_page)
            total = final_page - int(start_page) + 1
            zoom = int(dpi) / 72.0
            matrix = fitz.Matrix(zoom, zoom)
            for completed, page_number in enumerate(
                range(int(start_page), final_page + 1), start=1
            ):
                if cancelled and cancelled():
                    raise PDFImportCancelled("PDF 导入已取消")
                page = document.load_page(page_number - 1)
                pixmap = page.get_pixmap(
                    matrix=matrix,
                    colorspace=fitz.csRGB,
                    alpha=False,
                )
                destination = unique_path(
                    pages_dir,
                    f"{source_copy.stem}_p{page_number:04d}.jpg",
                )
                pixmap.save(str(destination), jpg_quality=90)
                generated.append(destination)
                if progress:
                    progress(completed, total, destination)
                del pixmap
                del page
        finally:
            document.close()
    except Exception:
        for path in generated:
            path.unlink(missing_ok=True)
        source_copy.unlink(missing_ok=True)
        raise
    return generated

