import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image
from PyQt6.QtWidgets import QApplication


ROOT = Path(__file__).resolve().parents[1]
APP_PATH = ROOT / "diary_ocr_app (5).py"
SPEC = importlib.util.spec_from_file_location("diary_ocr_app", APP_PATH)
app_module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(app_module)


class FakeOCRClient:
    def recognize(self, jpeg_bytes: bytes) -> str:
        return f"recognized-{len(jpeg_bytes)}"


class CoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt_app = QApplication.instance() or QApplication([])

    def test_session_restore_keeps_state_with_its_original_path(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = [root / name for name in ("a.jpg", "b.jpg", "c.jpg")]
            for path in paths:
                path.write_bytes(b"image")

            manager = app_module.SessionManager(root / "session.json")
            self.assertTrue(manager.save(
                [str(path) for path in paths],
                current_index=2,
                output_dir=str(root / "out"),
                done_indices={1, 2},
                draft_texts={1: "draft-b", 2: "draft-c"},
            ))
            paths[1].unlink()

            restored = manager.load()
            materialized = manager.materialize(restored)
            existing, current, done, drafts = materialized

            self.assertEqual(existing, [str(paths[0]), str(paths[2])])
            self.assertEqual(current, 1)
            self.assertEqual(done, {1})
            self.assertEqual(drafts, {1: "draft-c"})

    def test_version_one_session_is_migrated(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = [root / "a.jpg", root / "b.jpg"]
            for path in paths:
                path.write_bytes(b"image")
            session_path = root / "session.json"
            session_path.write_text(json.dumps({
                "version": 1,
                "image_paths": [str(path) for path in paths],
                "current_index": 1,
                "done_indices": [1],
                "draft_texts": {"1": "draft-b"},
            }), encoding="utf-8")

            data = app_module.SessionManager(session_path).load()
            self.assertEqual(data["current_path"], str(paths[1]))
            self.assertEqual(data["done_paths"], {str(paths[1])})
            self.assertEqual(data["draft_texts"], {str(paths[1]): "draft-b"})

    def test_duplicate_stems_get_distinct_output_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "one" / "page.jpg"
            second = root / "two" / "page.png"
            images = [str(first), str(second)]

            first_output = app_module._output_path_for_image(str(first), str(root), images)
            second_output = app_module._output_path_for_image(str(second), str(root), images)

            self.assertNotEqual(first_output, second_output)
            self.assertTrue(first_output.name.startswith("page__"))
            self.assertTrue(second_output.name.startswith("page__"))

    def test_compressor_applies_exif_orientation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "rotated.jpg"
            image = Image.new("RGB", (40, 20), "white")
            exif = Image.Exif()
            exif[274] = 6
            image.save(path, exif=exif)

            _, info = app_module.ImageCompressor.compress(str(path))

            self.assertEqual(info["original_resolution"], "20×40")
            self.assertTrue(info["compliant"])

    def test_batch_progress_counts_completions_and_writes_all_outputs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            images = []
            for index in range(3):
                path = root / f"page-{index}.jpg"
                Image.new("RGB", (20, 20), "white").save(path)
                images.append(str(path))

            progress = []
            summary = []
            worker = app_module.BatchOCRWorker(
                images, FakeOCRClient(), str(root / "out"), max_workers=2
            )
            worker.progress.connect(lambda current, total, path: progress.append(current))
            worker.batch_done.connect(summary.append)
            worker.run()

            self.assertEqual(progress, [1, 2, 3])
            self.assertEqual(summary[0]["succeeded"], 3)
            self.assertEqual(summary[0]["failed"], 0)
            for image_path in images:
                output = app_module._output_path_for_image(
                    image_path, str(root / "out"), images
                )
                self.assertTrue(output.exists())

    def test_merge_uses_current_queue_order_only(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "out"
            output.mkdir()
            first = root / "10.jpg"
            second = root / "2.jpg"
            images = [str(first), str(second)]
            for path in (first, second):
                path.write_bytes(b"image")
            app_module._write_text_atomic(
                app_module._output_path_for_image(str(first), str(output), images),
                "first",
            )
            app_module._write_text_atomic(
                app_module._output_path_for_image(str(second), str(output), images),
                "second",
            )
            (output / "stale.md").write_text("must-not-appear", encoding="utf-8")

            window = app_module.MainWindow()
            window._session = app_module.SessionManager(root / "session.json")
            window._config["output_dir"] = str(output)
            window._image_paths = images
            with patch.object(app_module.QMessageBox, "information"):
                window._merge_outputs()
            merged = (output / "final_diary_output.md").read_text(encoding="utf-8")
            window.deleteLater()

            self.assertLess(merged.index("10"), merged.index("2"))
            self.assertNotIn("must-not-appear", merged)

    def test_single_ocr_result_is_routed_to_its_source_page(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = str(root / "first.jpg")
            second = str(root / "second.jpg")
            window = app_module.MainWindow()
            window._session = app_module.SessionManager(root / "session.json")
            window._image_paths = [first, second]
            window._current_index = 1
            window._text_editor.setPlainText("second-page-draft")

            window._on_ocr_done(first, "first-page-result")

            self.assertEqual(window._ocr_texts[0], "first-page-result")
            self.assertEqual(window._text_editor.toPlainText(), "second-page-draft")
            window.deleteLater()

    def test_save_failure_does_not_advance(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            window = app_module.MainWindow()
            window._image_paths = [str(root / "first.jpg"), str(root / "second.jpg")]
            window._current_index = 0
            window._text_editor.setPlainText("draft")

            with (
                patch.object(window, "_save_md", return_value=None),
                patch.object(window, "_go_next") as go_next,
                patch.object(app_module.QMessageBox, "warning"),
            ):
                window._save_and_next()

            go_next.assert_not_called()
            self.assertEqual(window._current_index, 0)
            window.deleteLater()


if __name__ == "__main__":
    unittest.main()
