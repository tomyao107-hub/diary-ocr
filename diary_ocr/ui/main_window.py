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

from .. import __version__
from ..backup import create_diagnostic_pack, create_project_backup
from ..export_book import (
    ExportProfile,
    build_export_items,
    export_docx,
    export_markdown,
    export_pdf,
    load_export_profile,
    save_export_profile,
)
from ..image_import import import_folder, import_images, natural_key
from ..legacy import module as legacy
from ..ocr_task import PageTask, TaskStatus
from ..page_model import ImportReport
from ..pdf_import import PDFImportCancelled, import_pdf, inspect_pdf
from ..project_store import Project, ProjectStore
from ..session import ProjectSession


def _canonical(path: str) -> str:
    return legacy._canonical_path(path)


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
        if page_count < 1:
            raise ValueError("PDF 没有可导入的页面")
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
        self._page_tasks: dict[str, PageTask] = {}
        super().__init__()
        # Keep the user's global output_dir; projects always write under output/.
        self._global_output_dir = self._config.get(
            "output_dir", str(Path.home() / "diary_ocr_output")
        )
        self._session = ProjectSession(project.path)
        self._config["output_dir"] = str(project.output_dir)
        self._ocr_mode = str(self._config.get("ocr_mode", "cloud"))
        self._privacy_local_only = bool(self._config.get("privacy_local_only", False))
        self.setWindowTitle(f"{project.name} — Diary OCR {__version__}")
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
        self._act_export = QAction("📚 成册导出", self)
        self._act_backup = QAction("📦 备份项目", self)
        toolbar.insertAction(toolbar.actions()[0], self._act_back)
        toolbar.insertSeparator(toolbar.actions()[1])
        toolbar.insertAction(self._act_settings, self._act_pdf)
        toolbar.insertAction(self._act_settings, self._act_export)
        toolbar.insertAction(self._act_settings, self._act_backup)
        self._act_back.triggered.connect(self.close)
        self._act_pdf.triggered.connect(self._open_pdf)
        self._act_export.triggered.connect(self._export_book)
        self._act_backup.triggered.connect(self._backup_project)
        self._act_restore_session.setText("↻ 重新载入项目进度")
        self._act_clear_session.setText("🗑 清除项目进度")

    def _restore_project(self) -> None:
        data = self._session.load()
        if data:
            paths, current, done, drafts = self._session.materialize(data)
            self._page_tasks = self._session.materialize_tasks(data)
            if paths:
                self._load_images(paths, current, done, drafts)
                self._refresh_status_colors()
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
            self._refresh_status_colors()

    def _refresh_status_colors(self) -> None:
        for index, path in enumerate(self._image_paths):
            key = _canonical(path)
            task = self._page_tasks.get(key)
            if task:
                self._mark_item_status(index, task.status)
            elif index in self._done_indices:
                self._mark_item_status(index, TaskStatus.SUCCEEDED.value)

    def _append_pages(self, pages: list[str]) -> None:
        if not pages:
            return
        if self._current_index >= 0:
            self._ocr_texts[self._current_index] = self._text_editor.toPlainText()
        existing = list(self._image_paths)
        existing_keys = {_canonical(path) for path in existing}
        new_pages = [
            path for path in pages if _canonical(path) not in existing_keys
        ]
        if not new_pages:
            self._log("ℹ️ 所选图片均已在当前队列中，未重复导入")
            return
        # Seed pending tasks for new pages.
        for path in new_pages:
            key = _canonical(path)
            if key not in self._page_tasks:
                self._page_tasks[key] = PageTask(
                    path=path, status=TaskStatus.PENDING.value
                )
        start_index = self._current_index if existing else 0
        self._load_images(
            existing + new_pages,
            start_index=max(0, start_index),
            restore_done=set(self._done_indices),
            restore_texts=dict(self._ocr_texts),
        )
        self._refresh_status_colors()
        try:
            self.project = self.store.touch(self.project)
        except OSError as exc:
            self._log(f"⚠️ 更新项目时间失败：{exc}")
        self._log(f"✅ 已导入 {len(new_pages)} 页到项目")

    def _open_files(self):
        if self._batch_ui_running or (
            self._pdf_worker and self._pdf_worker.isRunning()
        ):
            return
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "选择日记图片",
            "",
            "图片文件 (*.jpg *.jpeg *.png *.bmp *.tiff *.tif *.webp *.heic *.heif)",
        )
        if not paths:
            return
        errors: list[str] = []
        report = ImportReport()

        def _on_error(source: Path, exc: OSError) -> None:
            errors.append(f"{source.name}: {exc}")

        try:
            imported = import_images(
                self.project.path,
                [Path(path) for path in sorted(paths, key=self._natural_key)],
                skip_errors=True,
                on_error=_on_error,
                report=report,
            )
            if not imported and errors:
                raise OSError("；".join(errors[:5]))
            if not imported:
                QMessageBox.information(
                    self,
                    "没有图片",
                    report.format() if report.messages else "没有可导入的图片文件。",
                )
                return
            self._append_pages([str(item.page) for item in imported])
            self._log(report.format())
            if report.messages:
                for message in report.messages[:8]:
                    self._log(f"  · {message}")
            if errors:
                self._log(f"⚠️ 部分图片导入失败：{'；'.join(errors[:5])}")
        except OSError as exc:
            self._log(f"❌ 图片导入失败：{exc}")
            QMessageBox.critical(self, "导入失败", str(exc))

    def _open_folder(self):
        if self._batch_ui_running or (
            self._pdf_worker and self._pdf_worker.isRunning()
        ):
            return
        folder = QFileDialog.getExistingDirectory(self, "选择要递归导入的文件夹")
        if not folder:
            return
        errors: list[str] = []
        report = ImportReport()

        def _on_error(source: Path, exc: OSError) -> None:
            errors.append(f"{source.name}: {exc}")

        try:
            imported = import_folder(
                self.project.path,
                Path(folder),
                on_error=_on_error,
                report=report,
            )
            if not imported:
                QMessageBox.information(
                    self,
                    "没有图片",
                    report.format()
                    if report.messages
                    else "文件夹及其子目录中没有支持的图片。",
                )
                return
            self._append_pages([str(item.page) for item in imported])
            self._log(report.format())
            if report.messages:
                for message in report.messages[:8]:
                    self._log(f"  · {message}")
            if errors:
                self._log(f"⚠️ 部分图片跳过：{'；'.join(errors[:5])}")
        except OSError as exc:
            self._log(f"❌ 文件夹导入失败：{exc}")
            QMessageBox.critical(self, "导入失败", str(exc))

    def _open_pdf(self):
        if self._batch_ui_running or (
            self._pdf_worker and self._pdf_worker.isRunning()
        ):
            return
        if self._ocr_worker and self._ocr_worker.isRunning():
            QMessageBox.information(self, "请稍候", "当前识别完成后再导入 PDF。")
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
            if info.page_count < 1:
                raise ValueError("PDF 没有可导入的页面")
        except Exception as exc:
            QMessageBox.critical(self, "无法打开 PDF", str(exc))
            return
        try:
            dialog = PDFRangeDialog(info.page_count, self.pdf_dpi, self)
        except ValueError as exc:
            QMessageBox.critical(self, "无法打开 PDF", str(exc))
            return
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
        self._pdf_progress.setAutoClose(False)
        self._pdf_progress.setAutoReset(False)
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
        self._set_import_busy(True)
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
        self._set_import_busy(False)

    def _set_import_busy(self, busy: bool) -> None:
        """Disable queue-mutating actions while PDF import is running."""
        if hasattr(self, "_act_pdf"):
            self._act_pdf.setEnabled(not busy and not self._batch_ui_running)
        if hasattr(self, "_act_export"):
            self._act_export.setEnabled(not busy and not self._batch_ui_running)
        if hasattr(self, "_act_backup"):
            self._act_backup.setEnabled(not busy and not self._batch_ui_running)
        self._act_open_files.setEnabled(not busy and not self._batch_ui_running)
        self._act_open_folder.setEnabled(not busy and not self._batch_ui_running)
        self._act_batch_ocr.setEnabled(
            not busy and bool(self._image_paths) and not self._batch_ui_running
        )
        self._update_buttons()

    def _export_book(self) -> None:
        if not self._image_paths:
            QMessageBox.information(self, "提示", "请先导入页面。")
            return
        profile = load_export_profile(self.project.path)
        profile.title = self.project.name or profile.title
        check = build_export_items(
            self._image_paths,
            self.project.output_dir,
            legacy._output_path_for_image,
            project_root=self.project.path,
        )
        if check.missing:
            reply = QMessageBox.question(
                self,
                "导出前检查",
                f"有 {len(check.missing)} 页尚无识别结果。\n"
                "是否仅导出已完成页面？",
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
        if not check.ready:
            QMessageBox.information(self, "无法导出", "没有可导出的识别结果。")
            return
        export_dir = self.project.output_dir / "exports"
        export_dir.mkdir(parents=True, exist_ok=True)
        try:
            md_path = export_markdown(check.ready, export_dir / "diary.md", profile)
            paths = [md_path]
            try:
                paths.append(export_docx(check.ready, export_dir / "diary.docx", profile))
            except RuntimeError as exc:
                self._log(f"⚠️ DOCX 跳过：{exc}")
            try:
                paths.append(export_pdf(check.ready, export_dir / "diary.pdf", profile))
            except Exception as exc:
                self._log(f"⚠️ PDF 导出失败：{exc}")
            save_export_profile(self.project.path, profile)
            listing = "\n".join(str(path) for path in paths)
            self._log(f"📚 成册导出完成：\n{listing}")
            QMessageBox.information(
                self,
                "导出完成",
                f"已导出 {len(check.ready)} 页：\n{listing}",
            )
        except Exception as exc:
            self._log(f"❌ 导出失败：{exc}")
            QMessageBox.critical(self, "导出失败", str(exc))

    def _backup_project(self) -> None:
        try:
            archive = create_project_backup(self.project.path)
            self._log(f"📦 项目备份已创建：{archive}")
            QMessageBox.information(self, "备份完成", f"已保存到：\n{archive}")
        except Exception as exc:
            QMessageBox.critical(self, "备份失败", str(exc))

    def _create_diagnostic(self) -> None:
        try:
            log_lines = []
            if hasattr(self, "_log_console"):
                log_lines = self._log_console._log_area.toPlainText().splitlines()[-200:]
            pack = create_diagnostic_pack(
                app_version=__version__,
                config=self._config,
                project_path=self.project.path,
                log_lines=log_lines,
            )
            QMessageBox.information(self, "诊断包", f"已生成脱敏诊断包：\n{pack}")
        except Exception as exc:
            QMessageBox.critical(self, "诊断包失败", str(exc))

    def _open_settings(self):
        if self._batch_ui_running or (
            self._pdf_worker and self._pdf_worker.isRunning()
        ):
            return
        # Present the global default export folder in settings, not the project
        # output path (which is fixed for project workspaces).
        dialog_config = dict(self._config)
        dialog_config["output_dir"] = self._global_output_dir
        # Show runtime key if stored in credential manager.
        try:
            from ..credentials import load_api_key

            if not dialog_config.get("api_key"):
                dialog_config["api_key"] = load_api_key(dialog_config)
        except Exception:
            pass
        dialog = legacy.SettingsDialog(dialog_config, self)
        if dialog.exec() != legacy.QDialog.DialogCode.Accepted:
            return
        new_config = dialog.get_config()
        self._global_output_dir = new_config.get(
            "output_dir", self._global_output_dir
        )
        self._config.update(new_config)
        self._config["output_dir"] = str(self.project.output_dir)
        self._ocr_mode = str(self._config.get("ocr_mode", "cloud"))
        self._privacy_local_only = bool(self._config.get("privacy_local_only", False))
        # Keep runtime api_key available even when stored in credential manager.
        try:
            from ..credentials import load_api_key

            key = load_api_key(self._config)
            if key:
                self._config["api_key"] = key
        except Exception:
            pass
        self._persist_global_config()
        self._log("⚙️ 设置已保存")

    def _save_config(self):
        """Persist API settings without writing the project path as global output_dir."""
        self._persist_global_config()

    def _persist_global_config(self) -> None:
        import json

        from ..paths import CONFIG_PATH

        payload = dict(self._config)
        payload["output_dir"] = getattr(
            self, "_global_output_dir", self._config.get("output_dir", "")
        )
        # Do not write API key to disk when credential manager holds it.
        if payload.get("api_key_storage") == "windows-credential-manager":
            payload["api_key"] = ""
        # Runtime always targets the current project's output directory.
        self._config["output_dir"] = str(self.project.output_dir)
        try:
            CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
            temporary = CONFIG_PATH.with_suffix(CONFIG_PATH.suffix + ".tmp")
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            temporary.replace(CONFIG_PATH)
        except Exception as exc:
            self._log(f"⚠️ 保存配置失败: {exc}")

    def _set_batch_ui_running(self, running: bool):
        super()._set_batch_ui_running(running)
        if hasattr(self, "_act_pdf"):
            pdf_busy = bool(self._pdf_worker and self._pdf_worker.isRunning())
            self._act_pdf.setEnabled(not running and not pdf_busy)
        if hasattr(self, "_act_export"):
            self._act_export.setEnabled(not running)
        if hasattr(self, "_act_backup"):
            self._act_backup.setEnabled(not running)

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
            # Never destroy the window from close; return to project list instead.
            event.ignore()
            self.hide()
            self.back_requested.emit()
