"""Project backup / restore and diagnostic packs (v1.4)."""

from __future__ import annotations

import json
import re
import shutil
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

ARCHIVE_SCHEMA_VERSION = 1
SENSITIVE_CONFIG_KEYS = frozenset({"api_key", "apiKey", "secret", "token"})


def _now_stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _is_within(root: Path, candidate: Path) -> bool:
    try:
        candidate.resolve().relative_to(root.resolve())
        return True
    except (ValueError, OSError):
        return False


def create_project_backup(project_path: Path, destination: Path | None = None) -> Path:
    project_path = Path(project_path).resolve()
    if not (project_path / "project.json").is_file():
        raise ValueError("目标不是有效的 Diary OCR 项目")
    if destination is None:
        destination = project_path.parent / f"{project_path.name}_backup_{_now_stamp()}.zip"
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)

    manifest = {
        "archive_schema_version": ARCHIVE_SCHEMA_VERSION,
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "project_name": project_path.name,
        "includes": ["project.json", "session.json", "sources", "pages", "output"],
        "excludes": ["API keys", "global config"],
    }
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(
                "archive_manifest.json",
                json.dumps(manifest, ensure_ascii=False, indent=2),
            )
            for path in project_path.rglob("*"):
                if not path.is_file():
                    continue
                # Skip temp files
                if path.suffix in {".tmp", ".pyc"} or path.name == ".DS_Store":
                    continue
                arcname = Path(project_path.name) / path.relative_to(project_path)
                zf.write(path, arcname.as_posix())
        temporary.replace(destination)
        return destination
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def restore_project_backup(
    archive_path: Path,
    projects_root: Path,
    *,
    target_name: str | None = None,
) -> Path:
    archive_path = Path(archive_path).resolve()
    projects_root = Path(projects_root).resolve()
    projects_root.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(archive_path, "r") as zf:
        names = zf.namelist()
        # Safety: reject absolute paths and parent traversal.
        for name in names:
            normalized = name.replace("\\", "/")
            if normalized.startswith("/") or ".." in normalized.split("/"):
                raise ValueError(f"归档包含非法路径：{name}")

        manifest_raw = None
        if "archive_manifest.json" in names:
            manifest_raw = json.loads(zf.read("archive_manifest.json").decode("utf-8"))

        # Detect project root prefix inside archive.
        project_entries = [
            n for n in names if n.replace("\\", "/").endswith("project.json")
        ]
        if not project_entries:
            raise ValueError("归档中未找到 project.json")
        project_json_name = project_entries[0].replace("\\", "/")
        prefix = project_json_name[: -len("project.json")].rstrip("/")
        folder_name = target_name or Path(prefix).name or "restored_project"
        folder_name = re.sub(r"[^\w\u4e00-\u9fff-]+", "-", folder_name).strip("-") or "restored"
        target = projects_root / folder_name
        counter = 2
        while target.exists():
            target = projects_root / f"{folder_name}_{counter}"
            counter += 1
        if not _is_within(projects_root, target) or target == projects_root:
            raise ValueError("恢复路径必须位于项目总目录内")

        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            zf.extractall(tmp_root)
            source_root = tmp_root / prefix if prefix else tmp_root
            if not (source_root / "project.json").is_file():
                # flat extract
                candidates = list(tmp_root.rglob("project.json"))
                if not candidates:
                    raise ValueError("解压后未找到 project.json")
                source_root = candidates[0].parent
            shutil.copytree(source_root, target)

    # Touch updated_at
    meta_path = target / "project.json"
    try:
        data = json.loads(meta_path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            data["updated_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
            meta_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
    except (OSError, json.JSONDecodeError):
        pass
    return target


def create_diagnostic_pack(
    *,
    app_version: str,
    config: dict,
    project_path: Path | None = None,
    log_lines: list[str] | None = None,
    destination: Path | None = None,
) -> Path:
    """Create a redacted diagnostic zip (no API keys, no OCR body, no images)."""
    if destination is None:
        base = Path.home() / "DiaryOCRDiagnostics"
        base.mkdir(parents=True, exist_ok=True)
        destination = base / f"diag_{_now_stamp()}.zip"
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)

    redacted = {
        key: ("***REDACTED***" if key in SENSITIVE_CONFIG_KEYS else value)
        for key, value in (config or {}).items()
    }
    # Never include long prompts that might contain private diary text samples? Keep prompts.
    info = {
        "app_version": app_version,
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "config": redacted,
    }
    if project_path is not None:
        project_path = Path(project_path)
        info["project"] = {
            "path_name": project_path.name,
            "has_session": (project_path / "session.json").exists(),
            "page_files": 0,
            "output_files": 0,
        }
        pages = project_path / "pages"
        output = project_path / "output"
        if pages.exists():
            info["project"]["page_files"] = sum(1 for p in pages.iterdir() if p.is_file())
        if output.exists():
            info["project"]["output_files"] = sum(1 for p in output.iterdir() if p.is_file())
        # Include project.json only (no content dumps)
        try:
            info["project"]["metadata"] = json.loads(
                (project_path / "project.json").read_text(encoding="utf-8")
            )
        except (OSError, json.JSONDecodeError):
            info["project"]["metadata"] = None

    # Redact potential secrets in log lines.
    safe_logs = []
    for line in log_lines or []:
        safe = re.sub(r"(api[_-]?key\s*[:=]\s*)\S+", r"\1***", line, flags=re.I)
        safe_logs.append(safe)

    temporary = destination.with_suffix(destination.suffix + ".tmp")
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(
                "diagnostic.json",
                json.dumps(info, ensure_ascii=False, indent=2),
            )
            zf.writestr("logs.txt", "\n".join(safe_logs))
        temporary.replace(destination)
        return destination
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
