"""Regression tests for robustness fixes."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image
from PyQt6.QtWidgets import QApplication

from diary_ocr.image_import import import_folder, import_images
from diary_ocr.legacy import module as legacy
from diary_ocr.pdf_import import inspect_pdf, validate_page_range
from diary_ocr.project_store import ProjectStore, slugify
from diary_ocr.session import ProjectSession
from diary_ocr.ui.main_window import ProjectEditor


class RobustnessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt_app = QApplication.instance() or QApplication([])

    def test_merge_keeps_project_session(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = ProjectStore(root / "projects")
            project = store.create("合并保进度")
            image = root / "page.jpg"
            Image.new("RGB", (12, 12), "white").save(image)
            page = import_images(project.path, [image])[0].page

            with patch.object(ProjectEditor, "_load_preview"):
                editor = ProjectEditor(project, store)
                editor._text_editor.setPlainText("正文")
                self.assertIsNotNone(
                    editor._save_md(0, str(page), editor._text_editor.toPlainText())
                )
                with patch.object(legacy.QMessageBox, "information"):
                    editor._merge_outputs()
                self.assertTrue(editor._session.exists())
                self.assertTrue(editor._session_save_silent())
                editor._auto_save_timer.stop()
                editor.deleteLater()

                restored = ProjectEditor(project, store)
                self.assertEqual(restored._image_paths, [str(page)])
                restored._auto_save_timer.stop()
                restored.deleteLater()

    def test_session_skips_invalid_paths_instead_of_failing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pages = root / "pages"
            pages.mkdir()
            good = pages / "ok.jpg"
            good.write_bytes(b"ok")
            session = ProjectSession(root)
            session.path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "pages": ["pages/ok.jpg", "../escape.jpg", "pages/missing.jpg"],
                        "current_page": "pages/ok.jpg",
                        "done_pages": ["pages/ok.jpg", "../escape.jpg"],
                        "drafts": {"pages/ok.jpg": "draft"},
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            data = session.load()
            self.assertIsNotNone(data)
            paths, current, done, drafts = session.materialize(data)
            self.assertEqual(paths, [str(good.resolve())])
            self.assertEqual(current, 0)
            self.assertEqual(done, {0})
            self.assertEqual(drafts, {0: "draft"})

    def test_slugify_avoids_windows_reserved_names(self):
        self.assertTrue(slugify("CON").upper().startswith("PROJECT"))
        self.assertNotEqual(slugify("nul").upper(), "NUL")

    def test_page_count_ignores_non_image_files(self):
        with tempfile.TemporaryDirectory() as directory:
            project = ProjectStore(Path(directory)).create("计数")
            (project.pages_dir / "note.txt").write_text("x", encoding="utf-8")
            (project.pages_dir / "page.jpg").write_bytes(b"fake")
            self.assertEqual(project.page_count, 1)

    def test_empty_pdf_is_rejected(self):
        with self.assertRaises(ValueError):
            validate_page_range(0, 1, 1)
        # PyMuPDF cannot even save a zero-page document; import_pdf must still
        # reject page_count < 1 after open.
        with patch("diary_ocr.pdf_import._fitz") as mock_fitz:
            document = MagicMock()
            document.needs_pass = False
            document.page_count = 0
            document.is_encrypted = False
            mock_fitz.return_value.open.return_value = document
            with tempfile.TemporaryDirectory() as directory:
                pdf = Path(directory) / "empty.pdf"
                pdf.write_bytes(b"%PDF-1.4")
                with self.assertRaises(ValueError):
                    inspect_pdf(pdf)

    def test_ocr_client_rejects_null_content(self):
        client = legacy.OCRAPIClient(api_key="sk-test")
        fake_response = MagicMock()
        fake_response.choices = [MagicMock()]
        fake_response.choices[0].message.content = None
        fake_openai = MagicMock()
        fake_openai.OpenAI.return_value.chat.completions.create.return_value = (
            fake_response
        )
        with patch.dict("sys.modules", {"openai": fake_openai}):
            with self.assertRaises(RuntimeError):
                client.recognize(b"fake-jpeg")

    def test_folder_import_continues_after_single_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "project"
            source = root / "source"
            source.mkdir()
            good = source / "a.jpg"
            bad = source / "b.jpg"
            # Distinct content so SHA-256 dedup does not skip b.jpg before copy.
            Image.new("RGB", (8, 8), "white").save(good)
            Image.new("RGB", (8, 8), "black").save(bad)
            errors = []

            original = legacy.shutil.copy2 if hasattr(legacy, "shutil") else None
            from diary_ocr import image_import as image_import_module

            real_copy = image_import_module.shutil.copy2

            def flaky_copy(src, dst, *args, **kwargs):
                if Path(src).name == "b.jpg":
                    raise OSError("simulated lock")
                return real_copy(src, dst, *args, **kwargs)

            with patch.object(image_import_module.shutil, "copy2", side_effect=flaky_copy):
                imported = import_folder(
                    project,
                    source,
                    on_error=lambda path, exc: errors.append(path.name),
                )
            self.assertEqual(len(imported), 1)
            self.assertEqual(imported[0].page.name, "a.jpg")
            self.assertEqual(errors, ["b.jpg"])


if __name__ == "__main__":
    unittest.main()
