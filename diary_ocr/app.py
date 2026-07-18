from __future__ import annotations

import sys

from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QApplication

from . import APP_NAME, __version__
from .legacy import module as legacy
from .paths import load_global_config, projects_root, save_global_config
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
        self.editor = ProjectEditor(
            project,
            self.store,
            int(self.config.get("pdf_dpi", 300)),
        )
        self.editor.back_requested.connect(self.return_home)
        self.home.hide()
        self.editor.show()

    def return_home(self) -> None:
        if self.editor:
            self.config["pdf_dpi"] = self.editor.pdf_dpi
            save_global_config(self.config)
            self.editor.deleteLater()
            self.editor = None
        self.home.refresh()
        self.home.show()
        self.home.raise_()
        self.home.activateWindow()

    def change_root(self, new_root: str) -> None:
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
        return 0
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(__version__)
    app.setOrganizationName("DiaryOCR")
    app.setStyleSheet(legacy.APP_STYLE)
    font = QFont("Microsoft YaHei", 11)
    app.setFont(font)
    controller = ApplicationController()
    controller.show()
    return app.exec()
