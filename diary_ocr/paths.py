from __future__ import annotations

import json
from pathlib import Path


CONFIG_PATH = Path.home() / ".diary_ocr_config.json"
DEFAULT_PROJECTS_ROOT = Path.home() / "DiaryOCRProjects"


def load_global_config() -> dict:
    defaults = {
        "projects_root": str(DEFAULT_PROJECTS_ROOT),
        "pdf_dpi": 300,
    }
    if CONFIG_PATH.exists():
        try:
            saved = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            if isinstance(saved, dict):
                defaults.update(saved)
        except (OSError, json.JSONDecodeError):
            pass
    return defaults


def save_global_config(config: dict) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = CONFIG_PATH.with_suffix(CONFIG_PATH.suffix + ".tmp")
    try:
        temporary.write_text(
            json.dumps(config, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(CONFIG_PATH)
    except Exception:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def projects_root(config: dict | None = None) -> Path:
    value = (config or load_global_config()).get(
        "projects_root", str(DEFAULT_PROJECTS_ROOT)
    )
    try:
        return Path(value).expanduser().resolve()
    except (OSError, RuntimeError):
        return DEFAULT_PROJECTS_ROOT.expanduser().resolve()

