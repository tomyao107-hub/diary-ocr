from __future__ import annotations

import atexit
import sys

from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QApplication

from . import APP_NAME, __version__
from .legacy import module as legacy
from .paths import load_global_config, projects_root, resolve_paddleocr_json_exe, save_global_config
from .project_store import Project, ProjectStore
from .ui.main_window import ProjectEditor
from .ui.project_home import ProjectHome


class ApplicationController:
    def __init__(self):
        self.config = load_global_config()
        self.store = ProjectStore(projects_root(self.config))
        self.home = ProjectHome(self.store, self.config)
        self.editor: ProjectEditor | None = None
        self.home.open_requested.connect(self.open_project)
        self.home.root_changed.connect(self.change_root)

    def show(self) -> None:
        self.home.show()

    def open_project(self, project: Project) -> None:
        if self.editor is not None:
            # Avoid stacking multiple editors if a signal fires twice.
            return
        try:
            dpi = int(self.config.get("pdf_dpi", 300))
        except (TypeError, ValueError):
            dpi = 300
        dpi = max(72, min(600, dpi))
        self.editor = ProjectEditor(project, self.store, dpi)
        self.editor.back_requested.connect(self.return_home)
        self.home.hide()
        self.editor.show()

    def return_home(self) -> None:
        if self.editor:
            try:
                dpi = int(self.editor.pdf_dpi)
            except (TypeError, ValueError):
                dpi = int(self.config.get("pdf_dpi", 300) or 300)
            # Reload from disk so API settings saved inside the editor are not
            # overwritten by a stale in-memory home config.
            disk = load_global_config()
            disk["pdf_dpi"] = max(72, min(600, dpi))
            disk["projects_root"] = self.config.get(
                "projects_root", disk.get("projects_root")
            )
            self.config = disk
            self.home.config = self.config
            try:
                save_global_config(self.config)
            except OSError:
                pass
            self.editor.deleteLater()
            self.editor = None
        self.home.refresh()
        self.home.show()
        self.home.raise_()
        self.home.activateWindow()

    def change_root(self, new_root: str) -> None:
        if new_root:
            self.config["projects_root"] = str(new_root)
        self.store = ProjectStore(projects_root(self.config))
        self.home.store = self.store
        self.home.refresh()


def main() -> int:
    if "--version" in sys.argv:
        print(__version__)
        return 0
    if "--check-environment" in sys.argv:
        import fitz
        import PIL
        import openai
        from PyQt6.QtCore import PYQT_VERSION_STR

        print(f"Diary OCR: {__version__}")
        print(f"Python: {sys.executable}")
        print(f"PyQt6: {PYQT_VERSION_STR}")
        print(f"Pillow: {PIL.__version__}")
        print(f"OpenAI: {openai.__version__}")
        print(f"PyMuPDF: {fitz.VersionBind}")
        paddle_exe = resolve_paddleocr_json_exe()
        print(f"PaddleOCR-json: {paddle_exe or 'not found'}")
        try:
            import paddleocr  # noqa: F401

            print("In-process paddleocr: available")
        except ImportError:
            print("In-process paddleocr: not installed")
        return 0
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(__version__)
    app.setOrganizationName("DiaryOCR")
    app.setStyleSheet(legacy.APP_STYLE)
    font = QFont("Microsoft YaHei", 11)
    app.setFont(font)

    def _cleanup_engines() -> None:
        try:
            from .engines.paddle_json import shutdown_shared_paddle_json_engine

            shutdown_shared_paddle_json_engine()
        except Exception:
            pass

    atexit.register(_cleanup_engines)
    app.aboutToQuit.connect(_cleanup_engines)

    controller = ApplicationController()
    # Lightweight startup probe (no model load).
    cfg = controller.config
    paddle_exe = resolve_paddleocr_json_exe(
        explicit_path=str(cfg.get("paddleocr_json_path") or "") or None,
        engines_dir=str(cfg.get("engines_dir") or "") or None,
    )
    if paddle_exe:
        print(f"[Diary OCR] 本地 Paddle OCR 就绪：{paddle_exe}")
    else:
        print(
            "[Diary OCR] 未检测到 PaddleOCR-json；"
            "便携包请检查 engines/，源码可用进程内 PP-OCR。"
        )
    controller.show()
    return app.exec()
