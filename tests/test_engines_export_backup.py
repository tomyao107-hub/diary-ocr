import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image

from diary_ocr.backup import (
    create_diagnostic_pack,
    create_project_backup,
    restore_project_backup,
)
from diary_ocr.engines.base import OCROptions
from diary_ocr.engines.mock import MockOCREngine
from diary_ocr.engines.registry import HybridRouter, default_registry
from diary_ocr.export_book import ExportProfile, export_markdown, build_export_items
from diary_ocr.image_import import import_images
from diary_ocr.legacy import module as legacy
from diary_ocr.page_model import ImportReport, load_page_catalog
from diary_ocr.project_store import ProjectStore


class EngineExportBackupTests(unittest.TestCase):
    def test_mock_engine_and_hybrid_privacy(self):
        engine = MockOCREngine(text_template="hello-{n}")
        result = engine.recognize(b"12345")
        self.assertEqual(result.text, "hello-5")
        self.assertEqual(result.engine, "mock-offline")

        registry = default_registry(api_key="k", include_mock=True)
        # Privacy + cloud mode must never call the network engine.
        cloud_router = HybridRouter(registry, mode="cloud", privacy_local_only=True)
        with self.assertRaises(RuntimeError):
            cloud_router.recognize(b"abc", OCROptions())

        # Local mode with privacy uses PP-OCR when available; invalid bytes
        # must raise a clear local error (not silent cloud fallback).
        local_router = HybridRouter(registry, mode="local", privacy_local_only=True)
        with self.assertRaises(RuntimeError):
            local_router.recognize(b"abc", OCROptions())

    def test_batch_retry_with_mock_failures(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            images = []
            for index in range(2):
                path = root / f"p{index}.jpg"
                Image.new("RGB", (16, 16), "white").save(path)
                images.append(str(path))

            # Fail once with a retryable error, then succeed.
            fail_then_ok = MockOCREngine(
                fail_times=1,
                text_template="ok-{n}",
                fail_message="APITimeoutError: request timed out",
            )
            client = legacy.OCRAPIClient(api_key="x", engine=fail_then_ok)
            worker = legacy.BatchOCRWorker(
                images,
                client,
                str(root / "out"),
                max_workers=1,
                max_attempts=3,
            )
            summary = []
            worker.batch_done.connect(summary.append)
            worker.run()
            self.assertEqual(summary[0]["succeeded"], 2, summary)
            self.assertEqual(summary[0]["failed"], 0, summary)

    def test_import_dedup_report(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = Path(directory) / "proj"
            (project / "pages").mkdir(parents=True)
            (project / "sources").mkdir()
            (project / "output").mkdir()
            external = root / "photo.jpg"
            Image.new("RGB", (12, 12), "white").save(external)
            report = ImportReport()
            first = import_images(project, [external], report=report)
            self.assertEqual(len(first), 1)
            self.assertEqual(report.succeeded, 1)
            report2 = ImportReport()
            second = import_images(project, [external], report=report2)
            self.assertEqual(report2.duplicates, 1)
            self.assertEqual(len(second), 0)
            catalog = load_page_catalog(project)
            self.assertEqual(len(catalog["pages"]), 1)

    def test_export_markdown_and_backup_roundtrip(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = ProjectStore(root / "projects")
            project = store.create("导出备份")
            page = project.pages_dir / "p1.jpg"
            Image.new("RGB", (10, 10), "white").save(page)
            md = project.output_dir / "p1.md"
            md.write_text("日记正文", encoding="utf-8")

            check = build_export_items(
                [str(page)],
                project.output_dir,
                legacy._output_path_for_image,
            )
            self.assertEqual(len(check.ready), 1)
            out = export_markdown(
                check.ready,
                project.output_dir / "exports" / "diary.md",
                ExportProfile(title="测试"),
            )
            self.assertIn("日记正文", out.read_text(encoding="utf-8"))

            archive = create_project_backup(project.path, root / "backup.zip")
            self.assertTrue(archive.exists())
            restored_root = root / "restored_projects"
            restored = restore_project_backup(archive, restored_root)
            self.assertTrue((restored / "project.json").exists())
            self.assertTrue((restored / "output" / "p1.md").exists())

            pack = create_diagnostic_pack(
                app_version="2.0.0",
                config={"api_key": "SECRET", "model": "x"},
                project_path=project.path,
                log_lines=["api_key=SECRET should redact"],
                destination=root / "diag.zip",
            )
            self.assertTrue(pack.exists())
            import zipfile

            with zipfile.ZipFile(pack) as zf:
                info = json.loads(zf.read("diagnostic.json"))
                self.assertEqual(info["config"]["api_key"], "***REDACTED***")
                logs = zf.read("logs.txt").decode("utf-8")
                self.assertNotIn("SECRET", logs)


if __name__ == "__main__":
    unittest.main()
