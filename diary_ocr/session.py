from __future__ import annotations

import json
import os
import shutil
from datetime import datetime
from pathlib import Path

from .ocr_task import PageTask, TaskStatus, recover_running_tasks


SESSION_SCHEMA_VERSION = 2
LEGACY_SESSION_SCHEMA_VERSION = 1


def _key(path: str) -> str:
    return os.path.normcase(os.path.abspath(path))


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


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

    def _backup(self) -> Path | None:
        if not self.path.exists():
            return None
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup = self.path.with_name(f"session.json.bak.{stamp}")
        try:
            shutil.copy2(self.path, backup)
            # Keep a stable latest backup name for operators.
            latest = self.path.with_suffix(self.path.suffix + ".bak")
            shutil.copy2(self.path, latest)
            return backup
        except OSError:
            return None

    def _normalize_pages(
        self,
        image_paths: list[str],
        page_tasks: dict[str, PageTask] | None,
        done_indices: set[int],
        output_dir: str,
    ) -> list[dict]:
        pages: list[dict] = []
        for index, path in enumerate(image_paths):
            relative = self._relative(path)
            key = _key(path)
            task = (page_tasks or {}).get(key)
            if task is None:
                status = (
                    TaskStatus.SUCCEEDED.value
                    if index in done_indices
                    else TaskStatus.PENDING.value
                )
                output = None
                if status == TaskStatus.SUCCEEDED.value:
                    # Best-effort relative output path; real path may use hash suffix.
                    stem = Path(path).stem
                    output = f"output/{stem}.md"
                task = PageTask(path=relative, status=status, output=output)
            else:
                task = PageTask(
                    path=relative,
                    status=task.status,
                    attempts=task.attempts,
                    last_error=task.last_error,
                    completed_at=task.completed_at,
                    output=task.output,
                )
            if task.status == TaskStatus.SUCCEEDED.value and not task.output:
                task.output = f"output/{Path(path).stem}.md"
            # Store output relative when possible.
            if task.output:
                try:
                    out_abs = Path(task.output)
                    if out_abs.is_absolute():
                        task.output = self._relative(str(out_abs))
                except ValueError:
                    pass
            pages.append(task.to_dict())
        return pages

    def save(
        self,
        image_paths: list[str],
        current_index: int,
        output_dir: str,
        done_indices: set[int],
        draft_texts: dict[int, str],
        page_tasks: dict[str, PageTask] | None = None,
    ) -> bool:
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        try:
            pages = self._normalize_pages(
                image_paths, page_tasks, done_indices, output_dir
            )
            page_paths = [item["path"] for item in pages]
            current_page = (
                page_paths[current_index]
                if 0 <= current_index < len(page_paths)
                else None
            )
            done_pages = [
                page_paths[index]
                for index in sorted(done_indices)
                if 0 <= index < len(page_paths)
            ]
            drafts = {
                page_paths[index]: text
                for index, text in draft_texts.items()
                if 0 <= index < len(page_paths) and isinstance(text, str)
            }
            data = {
                "schema_version": SESSION_SCHEMA_VERSION,
                "saved_at": _now(),
                "pages": pages,
                "current_page": current_page,
                "done_pages": done_pages,
                "drafts": drafts,
            }
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            temporary.replace(self.path)
            return True
        except (OSError, TypeError, ValueError):
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            return False

    def _migrate_v1(self, data: dict) -> dict:
        """Convert schema 1 string pages into schema 2 page objects."""
        self._backup()
        raw_pages = data.get("pages", [])
        if not isinstance(raw_pages, list):
            raw_pages = []
        done = data.get("done_pages", [])
        done_set = set(done) if isinstance(done, list) else set()
        output_dir = self.project_root / "output"
        pages: list[dict] = []
        for item in raw_pages:
            if not isinstance(item, str):
                continue
            status = (
                TaskStatus.SUCCEEDED.value
                if item in done_set
                else TaskStatus.PENDING.value
            )
            # Prefer filesystem evidence over done_pages when present.
            abs_path = None
            try:
                abs_path = self._absolute(item)
            except ValueError:
                abs_path = None
            if abs_path:
                stem = Path(abs_path).stem
                if (output_dir / f"{stem}.md").exists() or any(
                    output_dir.glob(f"{stem}__*.md")
                ):
                    status = TaskStatus.SUCCEEDED.value
            output = f"output/{Path(item).stem}.md" if status == TaskStatus.SUCCEEDED.value else None
            pages.append(
                PageTask(path=item, status=status, output=output).to_dict()
            )
        data = dict(data)
        data["schema_version"] = SESSION_SCHEMA_VERSION
        data["pages"] = pages
        # Persist migrated form immediately (idempotent).
        try:
            temporary = self.path.with_suffix(self.path.suffix + ".tmp")
            temporary.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            temporary.replace(self.path)
        except OSError:
            pass
        return data

    def _parse_pages(self, raw_pages: list) -> list[PageTask]:
        tasks: list[PageTask] = []
        for item in raw_pages:
            if isinstance(item, str):
                tasks.append(PageTask(path=item, status=TaskStatus.PENDING.value))
            elif isinstance(item, dict):
                tasks.append(PageTask.from_dict(item))
        return tasks

    def load(self) -> dict | None:
        if not self.path.exists():
            return None
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return None
            schema = int(data.get("schema_version", LEGACY_SESSION_SCHEMA_VERSION))
            if schema == LEGACY_SESSION_SCHEMA_VERSION:
                data = self._migrate_v1(data)
                schema = SESSION_SCHEMA_VERSION
            if schema != SESSION_SCHEMA_VERSION:
                # Future schema: refuse write-back elsewhere; load may still try.
                if schema > SESSION_SCHEMA_VERSION:
                    return None
            raw_pages = data.get("pages", [])
            if not isinstance(raw_pages, list) or not raw_pages:
                return None

            tasks = self._parse_pages(raw_pages)
            # Crash recovery: running → pending (idempotent).
            recover_running_tasks(tasks)

            absolute_pages: list[str] = []
            absolute_tasks: dict[str, PageTask] = {}
            for task in tasks:
                if not task.path:
                    continue
                try:
                    absolute = self._absolute(task.path)
                except ValueError:
                    continue
                absolute_pages.append(absolute)
                # Keep relative path in task for next save.
                absolute_tasks[_key(absolute)] = PageTask(
                    path=task.path,
                    status=task.status,
                    attempts=task.attempts,
                    last_error=task.last_error,
                    completed_at=task.completed_at,
                    output=task.output,
                )
            if not absolute_pages:
                return None

            def _safe_absolute(path: str | None) -> str | None:
                if not isinstance(path, str) or not path:
                    return None
                try:
                    return self._absolute(path)
                except ValueError:
                    return None

            current = _safe_absolute(data.get("current_page"))
            done = data.get("done_pages", [])
            drafts = data.get("drafts", {})
            done_paths = set()
            if isinstance(done, list):
                for path in done:
                    resolved = _safe_absolute(path) if isinstance(path, str) else None
                    if resolved:
                        done_paths.add(resolved)
            # Also treat succeeded tasks as done.
            for abs_path, task in absolute_tasks.items():
                if task.status == TaskStatus.SUCCEEDED.value:
                    done_paths.add(abs_path)
            draft_texts = {}
            if isinstance(drafts, dict):
                for path, text in drafts.items():
                    if not isinstance(path, str) or not isinstance(text, str):
                        continue
                    resolved = _safe_absolute(path)
                    if resolved:
                        draft_texts[resolved] = text
            return {
                "schema_version": SESSION_SCHEMA_VERSION,
                "saved_at": data.get("saved_at", ""),
                "output_dir": str(self.project_root / "output"),
                "image_paths": absolute_pages,
                "current_path": current or absolute_pages[0],
                "done_paths": done_paths,
                "draft_texts": draft_texts,
                "page_tasks": absolute_tasks,
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

    def materialize_tasks(self, data: dict) -> dict[str, PageTask]:
        """Return page tasks keyed by absolute canonical path, filtered to existing files."""
        raw: dict[str, PageTask] = data.get("page_tasks") or {}
        paths = [path for path in data.get("image_paths", []) if Path(path).is_file()]
        result: dict[str, PageTask] = {}
        for path in paths:
            key = _key(path)
            task = raw.get(key)
            if task is None:
                result[key] = PageTask(
                    path=self._relative(path),
                    status=(
                        TaskStatus.SUCCEEDED.value
                        if key in {_key(p) for p in data.get("done_paths", set())}
                        else TaskStatus.PENDING.value
                    ),
                )
            else:
                result[key] = task
        return result

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
