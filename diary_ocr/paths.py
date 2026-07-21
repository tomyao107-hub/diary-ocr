from __future__ import annotations

import json
import os
import sys
from pathlib import Path


CONFIG_PATH = Path.home() / ".diary_ocr_config.json"
DEFAULT_PROJECTS_ROOT = Path.home() / "DiaryOCRProjects"

# Candidate executable names used by PaddleOCR-json releases / forks.
PADDLEOCR_JSON_EXE_NAMES = (
    "PaddleOCR-json.exe",
    "PaddleOCR_json.exe",
    "PaddleOCR-json",
    "PaddleOCR_json",
)


def application_dir() -> Path:
    """
    Directory that holds the application root for portable assets.

    - Frozen (PyInstaller): directory of DiaryOCR.exe
    - Source: repository root (parent of diary_ocr package)
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def default_engines_dir() -> Path:
    return application_dir() / "engines"


def load_global_config() -> dict:
    defaults = {
        "projects_root": str(DEFAULT_PROJECTS_ROOT),
        "pdf_dpi": 300,
        # OOBE defaults: local Paddle first; existing user files still override.
        "ocr_mode": "local",
        "privacy_local_only": False,
        "local_device": "cpu",
        "ppocr_version": "PP-OCRv5",
        "ppocr_model_size": "mobile",
        "paddleocr_json_path": "",
        "engines_dir": "",
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


def _looks_like_exe(path: Path) -> bool:
    try:
        return path.is_file()
    except OSError:
        return False


def _search_exe_in_dir(directory: Path) -> Path | None:
    if not directory.is_dir():
        return None
    for name in PADDLEOCR_JSON_EXE_NAMES:
        candidate = directory / name
        if _looks_like_exe(candidate):
            return candidate.resolve()
    # Nested single folder (common after unzip)
    try:
        children = [p for p in directory.iterdir() if p.is_dir()]
    except OSError:
        return None
    for child in children:
        for name in PADDLEOCR_JSON_EXE_NAMES:
            candidate = child / name
            if _looks_like_exe(candidate):
                return candidate.resolve()
    return None


def resolve_paddleocr_json_exe(
    *,
    explicit_path: str | None = None,
    engines_dir: str | None = None,
) -> Path | None:
    """
    Locate PaddleOCR-json executable.

    Search order:
      1. explicit config path
      2. DIARY_OCR_PADDLE_JSON env
      3. engines_dir / PaddleOCR-json (or engines_dir itself)
      4. application_dir/engines/PaddleOCR-json
      5. PATH
    """
    candidates: list[Path] = []

    if explicit_path:
        candidates.append(Path(explicit_path).expanduser())
    env_path = os.environ.get("DIARY_OCR_PADDLE_JSON", "").strip()
    if env_path:
        candidates.append(Path(env_path).expanduser())

    for raw in candidates:
        try:
            path = raw.resolve()
        except (OSError, RuntimeError):
            continue
        if _looks_like_exe(path):
            return path
        found = _search_exe_in_dir(path)
        if found:
            return found

    engine_roots: list[Path] = []
    if engines_dir:
        try:
            engine_roots.append(Path(engines_dir).expanduser().resolve())
        except (OSError, RuntimeError):
            pass
    engine_roots.append(default_engines_dir())

    for root in engine_roots:
        found = _search_exe_in_dir(root / "PaddleOCR-json")
        if found:
            return found
        found = _search_exe_in_dir(root)
        if found:
            return found

    # PATH lookup
    for name in PADDLEOCR_JSON_EXE_NAMES:
        which = shutil_which(name)
        if which:
            return Path(which).resolve()
    return None


def shutil_which(name: str) -> str | None:
    from shutil import which

    return which(name)
