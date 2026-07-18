from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path


SESSION_SCHEMA_VERSION = 1


def _key(path: str) -> str:
    return os.path.normcase(os.path.abspath(path))


class ProjectSession:
    """Project-local session that persists portable relative page paths."""

    def __init__(self, project_root: Path):
        self.project_root = Path(project_root).resolve()
        self.path = self.project_root / "session.json"

    def _relative(self, path: str) -> str:
        resolved = Path(path).resolve()
        try:
            relative = resolved.relative_to(self.project_root)
        except ValueError as exc:
            raise ValueError(f"页面不在项目目录内：{path}") from exc
        return relative.as_posix()

    def _absolute(self, path: str) -> str:
        candidate = Path(path)
        if candidate.is_absolute():
            return str(candidate)
        resolved = (self.project_root / candidate).resolve()
        try:
            resolved.relative_to(self.project_root)
        except ValueError as exc:
            raise ValueError(f"非法的项目相对路径：{path}") from exc
        return str(resolved)

    def save(
        self,
        image_paths: list[str],
        current_index: int,
        output_dir: str,
        done_indices: set[int],
        draft_texts: dict[int, str],
    ) -> bool:
        try:
            pages = [self._relative(path) for path in image_paths]
            current_page = (
                pages[current_index] if 0 <= current_index < len(pages) else None
            )
            done_pages = [
                pages[index]
                for index in sorted(done_indices)
                if 0 <= index < len(pages)
            ]
            drafts = {
                pages[index]: text
                for index, text in draft_texts.items()
                if 0 <= index < len(pages) and isinstance(text, str)
            }
            data = {
                "schema_version": SESSION_SCHEMA_VERSION,
                "saved_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "pages": pages,
                "current_page": current_page,
                "done_pages": done_pages,
                "drafts": drafts,
            }
            temporary = self.path.with_suffix(".tmp")
            temporary.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            temporary.replace(self.path)
            return True
        except (OSError, TypeError, ValueError):
            return False

    def load(self) -> dict | None:
        if not self.path.exists():
            return None
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if data.get("schema_version") != SESSION_SCHEMA_VERSION:
                return None
            pages = data.get("pages", [])
            if not isinstance(pages, list) or not pages:
                return None
            absolute_pages = [self._absolute(path) for path in pages]
            current = data.get("current_page")
            done = data.get("done_pages", [])
            drafts = data.get("drafts", {})
            return {
                "schema_version": SESSION_SCHEMA_VERSION,
                "saved_at": data.get("saved_at", ""),
                "output_dir": str(self.project_root / "output"),
                "image_paths": absolute_pages,
                "current_path": self._absolute(current) if current else absolute_pages[0],
                "done_paths": {
                    self._absolute(path) for path in done if isinstance(path, str)
                },
                "draft_texts": {
                    self._absolute(path): text
                    for path, text in drafts.items()
                    if isinstance(path, str) and isinstance(text, str)
                },
            }
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return None

    def materialize(self, data: dict) -> tuple[list[str], int, set[int], dict[int, str]]:
        paths = [path for path in data.get("image_paths", []) if Path(path).is_file()]
        done = {_key(path) for path in data.get("done_paths", set())}
        drafts = {
            _key(path): text for path, text in data.get("draft_texts", {}).items()
        }
        current = _key(data.get("current_path") or "")
        current_index = next(
            (index for index, path in enumerate(paths) if _key(path) == current),
            0,
        )
        done_indices = {
            index for index, path in enumerate(paths) if _key(path) in done
        }
        draft_texts = {
            index: drafts[_key(path)]
            for index, path in enumerate(paths)
            if _key(path) in drafts
        }
        return paths, current_index, done_indices, draft_texts

    def clear(self) -> None:
        try:
            self.path.unlink(missing_ok=True)
        except OSError:
            pass

    def exists(self) -> bool:
        return self.path.exists()

    def saved_at(self) -> str:
        data = self.load()
        return str(data.get("saved_at", "")) if data else ""

