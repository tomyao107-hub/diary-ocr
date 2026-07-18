from __future__ import annotations

import json
import re
import shutil
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


PROJECT_SCHEMA_VERSION = 1
INDEX_SCHEMA_VERSION = 1


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _atomic_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(path)


def slugify(name: str) -> str:
    normalized = unicodedata.normalize("NFKC", name).strip()
    slug = re.sub(r"[^\w\u4e00-\u9fff-]+", "-", normalized, flags=re.UNICODE)
    slug = re.sub(r"[-_]{2,}", "-", slug).strip("-_. ")
    return slug[:80] or "project"


@dataclass(frozen=True)
class Project:
    id: str
    name: str
    path: Path
    created_at: str
    updated_at: str
    schema_version: int = PROJECT_SCHEMA_VERSION

    @property
    def sources_dir(self) -> Path:
        return self.path / "sources"

    @property
    def pages_dir(self) -> Path:
        return self.path / "pages"

    @property
    def output_dir(self) -> Path:
        return self.path / "output"

    @property
    def session_path(self) -> Path:
        return self.path / "session.json"

    @property
    def page_count(self) -> int:
        if not self.pages_dir.exists():
            return 0
        return sum(1 for item in self.pages_dir.iterdir() if item.is_file())


class ProjectStore:
    def __init__(self, root: Path):
        self.root = Path(root).expanduser().resolve()
        self.index_path = self.root / "projects_index.json"

    def _ensure_root(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)

    def _index(self) -> dict:
        if not self.index_path.exists():
            return {"schema_version": INDEX_SCHEMA_VERSION, "archived": []}
        try:
            data = json.loads(self.index_path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError
        except (OSError, json.JSONDecodeError, ValueError):
            return {"schema_version": INDEX_SCHEMA_VERSION, "archived": []}
        archived = data.get("archived", [])
        data["archived"] = archived if isinstance(archived, list) else []
        return data

    def _write_index(self, data: dict) -> None:
        self._ensure_root()
        data["schema_version"] = INDEX_SCHEMA_VERSION
        _atomic_json(self.index_path, data)

    @staticmethod
    def _from_path(path: Path) -> Project | None:
        metadata_path = path / "project.json"
        try:
            data = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(data, dict) or not data.get("id") or not data.get("name"):
            return None
        return Project(
            id=str(data["id"]),
            name=str(data["name"]),
            path=path.resolve(),
            created_at=str(data.get("created_at", "")),
            updated_at=str(data.get("updated_at", "")),
            schema_version=int(data.get("schema_version", 1)),
        )

    def list_projects(self, include_archived: bool = False) -> list[Project]:
        self._ensure_root()
        archived = set(self._index()["archived"])
        projects = []
        for child in self.root.iterdir():
            if not child.is_dir():
                continue
            project = self._from_path(child)
            if project and (include_archived or project.id not in archived):
                projects.append(project)
        return sorted(
            projects,
            key=lambda project: (project.updated_at, project.created_at),
            reverse=True,
        )

    def create(self, name: str) -> Project:
        clean_name = name.strip()
        if not clean_name:
            raise ValueError("项目名称不能为空")
        self._ensure_root()
        base = slugify(clean_name)
        project_path = self.root / base
        counter = 2
        while project_path.exists():
            project_path = self.root / f"{base}_{counter}"
            counter += 1
        project_path.mkdir()
        for folder in ("sources", "pages", "output"):
            (project_path / folder).mkdir()
        timestamp = _now()
        data = {
            "schema_version": PROJECT_SCHEMA_VERSION,
            "id": str(uuid.uuid4()),
            "name": clean_name,
            "created_at": timestamp,
            "updated_at": timestamp,
        }
        _atomic_json(project_path / "project.json", data)
        project = self._from_path(project_path)
        if project is None:
            raise OSError("无法创建项目元数据")
        return project

    def touch(self, project: Project) -> Project:
        metadata_path = project.path / "project.json"
        data = json.loads(metadata_path.read_text(encoding="utf-8"))
        data["updated_at"] = _now()
        data["schema_version"] = PROJECT_SCHEMA_VERSION
        _atomic_json(metadata_path, data)
        refreshed = self._from_path(project.path)
        if refreshed is None:
            raise OSError("项目元数据损坏")
        return refreshed

    def archive(self, project: Project) -> None:
        self._assert_managed(project.path)
        index = self._index()
        archived = set(index["archived"])
        archived.add(project.id)
        index["archived"] = sorted(archived)
        self._write_index(index)

    def delete(self, project: Project) -> None:
        resolved = self._assert_managed(project.path)
        if not (resolved / "project.json").is_file():
            raise ValueError("目标不是有效的 Diary OCR 项目")
        shutil.rmtree(resolved)
        index = self._index()
        index["archived"] = [
            item for item in index["archived"] if item != project.id
        ]
        self._write_index(index)

    def _assert_managed(self, path: Path) -> Path:
        resolved = Path(path).resolve()
        if resolved.parent != self.root or resolved == self.root:
            raise ValueError("项目路径不在当前项目总目录中")
        return resolved

