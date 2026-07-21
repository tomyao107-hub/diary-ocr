from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .. import __version__
from ..backup import restore_project_backup
from ..legacy import module as legacy
from ..paths import save_global_config
from ..project_store import Project, ProjectStore


class ProjectHome(QMainWindow):
    open_requested = pyqtSignal(object)
    root_changed = pyqtSignal(str)

    def __init__(self, store: ProjectStore, config: dict):
        super().__init__()
        self.store = store
        self.config = config
        self.setWindowTitle(f"Diary OCR {__version__} — 项目")
        self.resize(940, 640)
        self._setup_ui()
        self.refresh()

    def _setup_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(28, 24, 28, 24)
        title_row = QHBoxLayout()
        title = QLabel("历史日记 OCR")
        title.setStyleSheet("font-size: 28px; font-weight: 700; color: #89b4fa;")
        version = QLabel(f"v{__version__}")
        version.setStyleSheet("color: #6c7086;")
        title_row.addWidget(title)
        title_row.addWidget(version)
        title_row.addStretch()
        layout.addLayout(title_row)
        subtitle = QLabel("选择一个项目继续，所有原始素材、页面和识别结果都保存在项目内。")
        subtitle.setStyleSheet("color: #a6adc8; margin-bottom: 12px;")
        layout.addWidget(subtitle)

        self.project_list = QListWidget()
        self.project_list.itemDoubleClicked.connect(lambda _item: self._open())
        layout.addWidget(self.project_list, 1)

        buttons = QHBoxLayout()
        new_button = QPushButton("新建项目")
        open_button = QPushButton("打开")
        remove_button = QPushButton("移除 / 删除")
        remove_button.setObjectName("danger")
        restore_button = QPushButton("从备份恢复")
        settings_button = QPushButton("OCR 设置")
        root_button = QPushButton("更改项目目录")
        new_button.clicked.connect(self._create)
        open_button.clicked.connect(self._open)
        remove_button.clicked.connect(self._remove)
        restore_button.clicked.connect(self._restore_backup)
        settings_button.clicked.connect(self._settings)
        root_button.clicked.connect(self._choose_root)
        for button in (
            new_button,
            open_button,
            remove_button,
            restore_button,
            settings_button,
            root_button,
        ):
            buttons.addWidget(button)
        buttons.addStretch()
        layout.addLayout(buttons)
        self.root_label = QLabel()
        self.root_label.setStyleSheet("color: #6c7086; font-size: 11px;")
        layout.addWidget(self.root_label)

    def refresh(self) -> None:
        self.project_list.clear()
        for project in self.store.list_projects():
            pages = project.page_count
            updated = project.updated_at.replace("T", " ")[:19]
            item = QListWidgetItem(
                f"{project.name}\n{pages} 页  ·  更新于 {updated}"
            )
            item.setData(Qt.ItemDataRole.UserRole, project)
            item.setSizeHint(item.sizeHint() * 1.45)
            self.project_list.addItem(item)
        self.root_label.setText(f"项目目录：{self.store.root}")

    def _selected(self) -> Project | None:
        item = self.project_list.currentItem()
        if item is None:
            QMessageBox.information(self, "请选择项目", "请先选择一个项目。")
            return None
        return item.data(Qt.ItemDataRole.UserRole)

    def _create(self) -> None:
        name, accepted = QInputDialog.getText(self, "新建项目", "项目名称")
        if not accepted:
            return
        if not name.strip():
            QMessageBox.warning(self, "创建失败", "项目名称不能为空。")
            return
        try:
            project = self.store.create(name)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "创建失败", str(exc))
            return
        self.refresh()
        self.open_requested.emit(project)

    def _open(self) -> None:
        project = self._selected()
        if project:
            self.open_requested.emit(project)

    def _remove(self) -> None:
        project = self._selected()
        if project is None:
            return
        box = QMessageBox(self)
        box.setWindowTitle("移除或删除项目")
        box.setText(f"要如何处理“{project.name}”？")
        box.setInformativeText("移除仅从列表隐藏，项目文件仍保留；彻底删除不可恢复。")
        archive_button = box.addButton("仅从列表移除", QMessageBox.ButtonRole.ActionRole)
        delete_button = box.addButton("彻底删除文件", QMessageBox.ButtonRole.DestructiveRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.exec()
        if box.clickedButton() is archive_button:
            self.store.archive(project)
            self.refresh()
        elif box.clickedButton() is delete_button:
            typed, accepted = QInputDialog.getText(
                self,
                "确认彻底删除",
                f"请输入项目名称“{project.name}”确认：",
            )
            if accepted and typed == project.name:
                try:
                    self.store.delete(project)
                except (OSError, ValueError) as exc:
                    QMessageBox.critical(self, "删除失败", str(exc))
                self.refresh()
            elif accepted:
                QMessageBox.warning(self, "名称不匹配", "项目名称不匹配，未删除。")

    def _restore_backup(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "选择项目备份", "", "备份文件 (*.zip)"
        )
        if not path:
            return
        try:
            restored = restore_project_backup(Path(path), self.store.root)
        except Exception as exc:
            QMessageBox.critical(self, "恢复失败", str(exc))
            return
        self.refresh()
        QMessageBox.information(
            self, "恢复完成", f"项目已恢复到：\n{restored}\n请从列表中打开。"
        )

    def _settings(self) -> None:
        dialog_config = dict(self.config)
        try:
            from ..credentials import load_api_key

            if not dialog_config.get("api_key"):
                dialog_config["api_key"] = load_api_key(dialog_config)
        except Exception:
            pass
        dialog = legacy.SettingsDialog(dialog_config, self)
        if dialog.exec() == legacy.QDialog.DialogCode.Accepted:
            self.config.update(dialog.get_config())
            try:
                from ..credentials import load_api_key

                key = load_api_key(self.config)
                if key:
                    self.config["api_key"] = key
            except Exception:
                pass
            try:
                payload = dict(self.config)
                if payload.get("api_key_storage") == "windows-credential-manager":
                    payload["api_key"] = ""
                save_global_config(payload)
            except OSError as exc:
                QMessageBox.warning(self, "保存失败", f"无法写入全局配置：{exc}")

    def _choose_root(self) -> None:
        selected = QFileDialog.getExistingDirectory(
            self, "选择项目总目录", str(self.store.root)
        )
        if not selected:
            return
        try:
            resolved = str(Path(selected).expanduser().resolve())
        except (OSError, RuntimeError) as exc:
            QMessageBox.warning(self, "无效目录", str(exc))
            return
        self.config["projects_root"] = resolved
        try:
            save_global_config(self.config)
        except OSError as exc:
            QMessageBox.warning(self, "保存失败", f"无法写入全局配置：{exc}")
            return
        self.root_changed.emit(resolved)

