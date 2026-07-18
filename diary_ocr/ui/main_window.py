from __future__ import annotations

import traceback
from pathlib import Path

from PyQt6.QtCore import QThread, Qt, pyqtSignal
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QLabel,
    QMessageBox,
    QProgressDialog,
    QSpinBox,
)

from ..image_import import import_folder, import_images, natural_key
from ..legacy import module as legacy
from ..pdf_import import PDFImportCancelled, import_pdf, inspect_pdf
from ..project_store import Project, ProjectStore
from ..session import ProjectSession


class PDFImportWorker(QThread):
    progress = pyqtSignal(int, int, str)
    succeeded = pyqtSignal(list)
    failed = pyqtSignal(str)

    def __init__(
        self,
        project_root: Path,
        pdf_path: Path,
        start_page: int,
        end_page: int,
        dpi: int,
    ):
        super().__init__()
        self.project_root = project_root
        self.pdf_path = pdf_path
        self.start_page = start_page
        self.end_page = end_page
        self.dpi = dpi
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def run(self) -> None:
        try:
            pages = import_pdf(
                self.project_root,
                self.pdf_path,
                self.start_page,
                self.end_page,
                self.dpi,
                progress=lambda current, total, path: self.progress.emit(
                    current, total, str(path)
                ),
                cancelled=lambda: self._cancelled,
            )
            self.succeeded.emit([str(path) for path in pages])
        except PDFImportCancelled:
            self.failed.emit("PDF 导入已取消")
        except Exception as exc:
            self.failed.emit(f"{type(exc).__name__}: {exc}")


class PDFRangeDialog(QDialog):
    def __init__(self, page_count: int, dpi: int, parent=None):
        super().__init__(parent)
        self.setWindowTitle("导入 PDF")
        layout = QFormLayout(self)
        layout.addRow("总页数", QLabel(str(page_count)))
        self.start_page = QSpinBox()
        self.start_page.setRange(1, page_count)
        self.start_page.setValue(1)
        self.end_page = QSpinBox()
        self.end_page.setRange(1, page_count)
        self.end_page.setValue(page_count)
        self.dpi = QSpinBox()
        self.dpi.setRange(72, 600)
        self.dpi.setValue(max(72, min(600, int(dpi))))
        self.dpi.setSuffix(" DPI")
        layout.addRow("起始页", self.start_page)
        layout.addRow("结束页", self.end_page)
        layout.addRow("渲染精度", self.dpi)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._accept_if_valid)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

    def _accept_if_valid(self) -> None:
        if self.start_page.value() > self.end_page.value():
            QMessageBox.warning(self, "页码无效", "结束页不能小于起始页。")
            return
        self.accept()


class ProjectEditor(legacy.MainWindow):
    back_requested = pyqtSignal()

    def __init__(self, project: Project, store: ProjectStore, pdf_dpi: int = 300):
        self.project = project
        self.store = store
        self.pdf_dpi = pdf_dpi
        self._pdf_worker: PDFImportWorker | None = None
        self._pdf_progress: QProgressDialog | None = None
        super().__init__()
        self._session = ProjectSession(project.path)
        self._config["output_dir"] = str(project.output_dir)
        self.setWindowTitle(f"{project.name} — Diary OCR 1.0")
        self._install_project_actions()
        self._restore_project()

    def _offer_restore_session(self):
        # Project sessions are restored automatically when a project is opened.
        return

    def _install_project_actions(self) -> None:
        toolbar = self.findChildren(legacy.QToolBar)[0]
        self._act_back = QAction("← 返回项目列表", self)
        self._act_back.setShortcut("Alt+Left")
        self._act_pdf = QAction("📕 导入 PDF", self)
        toolbar.insertAction(toolbar.actions()[0], self._act_back)
        toolbar.insertSeparator(toolbar.actions()[1])
        toolbar.insertAction(self._act_settings, self._act_pdf)
        self._act_back.triggered.connect(self.close)
        self._act_pdf.triggered.connect(self._open_pdf)
        self._act_restore_session.setText("↻ 重新载入项目进度")
        self._act_clear_session.setText("🗑 清除项目进度")

    def _restore_project(self) -> None:
        data = self._session.load()
        if data:
            paths, current, done, drafts = self._session.materialize(data)
            if paths:
                self._load_images(paths, current, done, drafts)
                return
        pages = sorted(
            [
                str(path)
                for path in self.project.pages_dir.iterdir()
                if path.is_file() and path.suffix.lower() in self.SUPPORTED_EXTS
            ],
            key=self._natural_key,
        )
        if pages:
            self._load_images(pages)

    def _append_pages(self, pages: list[str]) -> None:
        if not pages:
            return
        if self._current_index >= 0:
            self._ocr_texts[self._current_index] = self._text_editor.toPlainText()
        existing = list(self._image_paths)
        new_pages = [path for path in pages if path not in existing]
        if not new_pages:
            return
        start_index = self._current_index if existing else 0
        self._load_images(
            existing + new_pages,
            start_index=max(0, start_index),
            restore_done=set(self._done_indices),
            restore_texts=dict(self._ocr_texts),
        )
        self.project = self.store.touch(self.project)
        self._log(f"✅ 已导入 {len(new_pages)} 页到项目")

    def _open_files(self):
        if self._batch_ui_running:
            return
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "选择日记图片",
            "",
            "图片文件 (*.jpg *.jpeg *.png *.bmp *.tiff *.tif *.webp)",
        )
        if not paths:
            return
        try:
            imported = import_images(
                self.project.path,
                [Path(path) for path in sorted(paths, key=self._natural_key)],
            )
            self._append_pages([str(item.page) for item in imported])
        except OSError as exc:
            self._log(f"❌ 图片导入失败：{exc}")
            QMessageBox.critical(self, "导入失败", str(exc))

    def _open_folder(self):
        if self._batch_ui_running:
            return
        folder = QFileDialog.getExistingDirectory(self, "选择要递归导入的文件夹")
        if not folder:
            return
        try:
            imported = import_folder(self.project.path, Path(folder))
            if not imported:
                QMessageBox.information(
                    self, "没有图片", "文件夹及其子目录中没有支持的图片。"
                )
                return
            self._append_pages([str(item.page) for item in imported])
        except OSError as exc:
            self._log(f"❌ 文件夹导入失败：{exc}")
            QMessageBox.critical(self, "导入失败", str(exc))

    def _open_pdf(self):
        if self._batch_ui_running or (
            self._pdf_worker and self._pdf_worker.isRunning()
        ):
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "选择 PDF", "", "PDF 文件 (*.pdf)"
        )
        if not path:
            return
        try:
            info = inspect_pdf(Path(path))
            if info.needs_password:
                raise ValueError("暂不支持加密 PDF")
        except Exception as exc:
            QMessageBox.critical(self, "无法打开 PDF", str(exc))
            return
        dialog = PDFRangeDialog(info.page_count, self.pdf_dpi, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        total = dialog.end_page.value() - dialog.start_page.value() + 1
        self.pdf_dpi = dialog.dpi.value()
        self._pdf_progress = QProgressDialog(
            "正在逐页渲染 PDF…", "取消", 0, total, self
        )
        self._pdf_progress.setWindowTitle("导入 PDF")
        self._pdf_progress.setWindowModality(Qt.WindowModality.WindowModal)
        self._pdf_progress.setMinimumDuration(0)
        self._pdf_worker = PDFImportWorker(
            self.project.path,
            Path(path),
            dialog.start_page.value(),
            dialog.end_page.value(),
            dialog.dpi.value(),
        )
        self._pdf_progress.canceled.connect(self._pdf_worker.cancel)
        self._pdf_worker.progress.connect(self._on_pdf_progress)
        self._pdf_worker.succeeded.connect(self._on_pdf_succeeded)
        self._pdf_worker.failed.connect(self._on_pdf_failed)
        self._pdf_worker.finished.connect(self._on_pdf_finished)
        self._pdf_worker.start()

    def _on_pdf_progress(self, current: int, total: int, path: str) -> None:
        if self._pdf_progress:
            self._pdf_progress.setMaximum(total)
            self._pdf_progress.setValue(current)
            self._pdf_progress.setLabelText(f"正在渲染 {Path(path).name}")

    def _on_pdf_succeeded(self, pages: list[str]) -> None:
        self._append_pages(pages)
        self._log(f"✅ PDF 导入完成，共 {len(pages)} 页")

    def _on_pdf_failed(self, message: str) -> None:
        self._log(f"⚠️ {message}")
        if message != "PDF 导入已取消":
            QMessageBox.critical(self, "PDF 导入失败", message)

    def _on_pdf_finished(self) -> None:
        if self._pdf_progress:
            self._pdf_progress.close()
            self._pdf_progress.deleteLater()
            self._pdf_progress = None
        if self._pdf_worker:
            self._pdf_worker.deleteLater()
            self._pdf_worker = None

    def _open_settings(self):
        super()._open_settings()
        self._config["output_dir"] = str(self.project.output_dir)
        self._save_config()

    def closeEvent(self, event):
        if self._pdf_worker and self._pdf_worker.isRunning():
            self._pdf_worker.cancel()
            QMessageBox.information(
                self, "正在停止导入", "已请求停止 PDF 导入，请稍后再返回。"
            )
            event.ignore()
            return
        super().closeEvent(event)
        if event.isAccepted():
            try:
                self.project = self.store.touch(self.project)
            except OSError:
                self._log(traceback.format_exc())
            event.ignore()
            self.hide()
            self.back_requested.emit()

