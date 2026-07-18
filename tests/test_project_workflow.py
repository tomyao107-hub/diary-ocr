import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image
from PyQt6.QtWidgets import QApplication

from diary_ocr.image_import import import_images
from diary_ocr.legacy import module as legacy
from diary_ocr.project_store import ProjectStore
from diary_ocr.ui.main_window import ProjectEditor


class ProjectWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt_app = QApplication.instance() or QApplication([])

    def test_create_import_edit_merge_and_restore(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = ProjectStore(root / "projects")
            project = store.create("完整流程")
            external = root / "source.jpg"
            Image.new("RGB", (40, 30), "white").save(external)
            page = import_images(project.path, [external])[0].page
            external.unlink()

            with patch.object(ProjectEditor, "_load_preview"):
                editor = ProjectEditor(project, store)
                self.assertEqual(editor._image_paths, [str(page)])
                editor._text_editor.setPlainText("校对后的日记文字")
                saved = editor._save_md(0, str(page), editor._text_editor.toPlainText())
                self.assertIsNotNone(saved)
                editor._done_indices.add(0)
                self.assertTrue(editor._session_save_silent())
                with patch.object(legacy.QMessageBox, "information"):
                    editor._merge_outputs()
                editor._auto_save_timer.stop()
                editor.deleteLater()

                restored = ProjectEditor(project, store)
                self.assertEqual(restored._image_paths, [str(page)])
                self.assertEqual(
                    restored._text_editor.toPlainText(), "校对后的日记文字"
                )
                merged = project.output_dir / "final_diary_output.md"
                self.assertIn("校对后的日记文字", merged.read_text(encoding="utf-8"))
                restored._auto_save_timer.stop()
                restored.deleteLater()


if __name__ == "__main__":
    unittest.main()

