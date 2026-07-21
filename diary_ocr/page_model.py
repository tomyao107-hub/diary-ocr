"""Page metadata model with dedup and preprocessing hooks (v1.2)."""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterable


PAGE_CATALOG_SCHEMA = 1


@dataclass
class Preprocessing:
    crop: list[int] | None = None
    perspective: list[list[float]] | None = None
    grayscale: bool = False
    contrast: float = 1.0
    rotation: int = 0

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict | None) -> "Preprocessing":
        data = data or {}
        return cls(
            crop=data.get("crop"),
            perspective=data.get("perspective"),
            grayscale=bool(data.get("grayscale", False)),
            contrast=float(data.get("contrast", 1.0) or 1.0),
            rotation=int(data.get("rotation", 0) or 0),
        )


@dataclass
class PageMeta:
    id: str
    path: str  # relative pages/...
    source: str | None = None  # relative sources/...
    sha256: str | None = None
    rotation: int = 0
    preprocessing: Preprocessing = field(default_factory=Preprocessing)
    width: int | None = None
    height: int | None = None
    imported_at: str = ""

    def to_dict(self) -> dict:
        data = asdict(self)
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "PageMeta":
        prep = Preprocessing.from_dict(data.get("preprocessing"))
        return cls(
            id=str(data.get("id") or str(uuid.uuid4())),
            path=str(data.get("path") or ""),
            source=(str(data["source"]) if data.get("source") else None),
            sha256=(str(data["sha256"]) if data.get("sha256") else None),
            rotation=int(data.get("rotation", 0) or 0),
            preprocessing=prep,
            width=data.get("width"),
            height=data.get("height"),
            imported_at=str(data.get("imported_at") or ""),
        )


def file_sha256(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        while True:
            chunk = handle.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def load_page_catalog(project_root: Path) -> dict:
    path = Path(project_root) / "pages_meta.json"
    if not path.exists():
        return {"schema_version": PAGE_CATALOG_SCHEMA, "pages": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError
        pages = data.get("pages", [])
        data["pages"] = pages if isinstance(pages, list) else []
        data["schema_version"] = int(data.get("schema_version", PAGE_CATALOG_SCHEMA))
        return data
    except (OSError, json.JSONDecodeError, ValueError):
        return {"schema_version": PAGE_CATALOG_SCHEMA, "pages": []}


def save_page_catalog(project_root: Path, catalog: dict) -> None:
    path = Path(project_root) / "pages_meta.json"
    catalog = dict(catalog)
    catalog["schema_version"] = PAGE_CATALOG_SCHEMA
    catalog["updated_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
    temporary = path.with_suffix(path.suffix + ".tmp")
    try:
        temporary.write_text(
            json.dumps(catalog, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def find_duplicate_sha(catalog: dict, sha256: str) -> PageMeta | None:
    for item in catalog.get("pages", []):
        if isinstance(item, dict) and item.get("sha256") == sha256:
            return PageMeta.from_dict(item)
    return None


def register_page(
    project_root: Path,
    page_rel: str,
    source_rel: str | None,
    sha256: str | None,
    *,
    rotation: int = 0,
    width: int | None = None,
    height: int | None = None,
) -> PageMeta:
    catalog = load_page_catalog(project_root)
    meta = PageMeta(
        id=str(uuid.uuid4()),
        path=page_rel,
        source=source_rel,
        sha256=sha256,
        rotation=rotation,
        width=width,
        height=height,
        imported_at=datetime.now().astimezone().isoformat(timespec="seconds"),
    )
    pages = list(catalog.get("pages", []))
    pages.append(meta.to_dict())
    catalog["pages"] = pages
    save_page_catalog(project_root, catalog)
    return meta


@dataclass
class ImportReport:
    succeeded: int = 0
    duplicates: int = 0
    skipped: int = 0
    unsupported: int = 0
    failed: int = 0
    messages: list[str] = field(default_factory=list)

    def format(self) -> str:
        return (
            f"导入报告：成功 {self.succeeded}，重复 {self.duplicates}，"
            f"跳过 {self.skipped}，不支持 {self.unsupported}，失败 {self.failed}"
        )
