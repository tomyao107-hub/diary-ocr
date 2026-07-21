"""Tests for PaddleOCR-json engine helpers (no real subprocess required)."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from diary_ocr.engines.paddle_json import PADDLE_JSON_ENGINE_ID, PaddleOCRJsonEngine
from diary_ocr.engines.registry import HybridRouter, default_registry
from diary_ocr.paths import resolve_paddleocr_json_exe


class PaddleJsonEngineTests(unittest.TestCase):
    def test_parse_result_success(self):
        text, warnings, boxes = PaddleOCRJsonEngine.parse_result(
            {
                "code": 100,
                "data": [
                    {
                        "text": "甲",
                        "score": 0.9,
                        "box": [[0, 0], [10, 0], [10, 10], [0, 10]],
                    },
                    {
                        "text": "乙",
                        "score": 0.8,
                        "box": [[0, 20], [10, 20], [10, 30], [0, 30]],
                    },
                ],
            }
        )
        self.assertEqual(text, "甲\n乙")
        self.assertEqual(warnings, [])
        self.assertEqual(len(boxes), 2)
        self.assertEqual(boxes[0]["text"], "甲")
        self.assertEqual(boxes[0]["box"][0], [0, 0])

    def test_parse_result_empty(self):
        text, warnings, boxes = PaddleOCRJsonEngine.parse_result(
            {"code": 101, "data": "No text found"}
        )
        self.assertEqual(text, "")
        self.assertTrue(warnings)
        self.assertEqual(boxes, [])

    def test_parse_result_error(self):
        with self.assertRaises(RuntimeError):
            PaddleOCRJsonEngine.parse_result({"code": 200, "data": "missing"})

    def test_resolve_exe_from_engines_dir(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            engine_dir = root / "engines" / "PaddleOCR-json"
            engine_dir.mkdir(parents=True)
            exe = engine_dir / "PaddleOCR-json.exe"
            exe.write_bytes(b"fake")
            found = resolve_paddleocr_json_exe(engines_dir=str(root / "engines"))
            self.assertEqual(found, exe.resolve())

    def test_pick_local_prefers_paddle_json(self):
        registry = default_registry(api_key="k")
        paddle = registry.get(PADDLE_JSON_ENGINE_ID)
        self.assertIsNotNone(paddle)
        with (
            patch.object(paddle, "is_available", return_value=True),
            patch.object(
                registry.get("ppocr-local"), "is_available", return_value=True
            ),
        ):
            picked = registry.pick_local()
            self.assertEqual(picked.capabilities().name, PADDLE_JSON_ENGINE_ID)

    def test_hybrid_privacy_blocks_cloud(self):
        registry = default_registry(api_key="k", include_mock=True)
        router = HybridRouter(registry, mode="cloud", privacy_local_only=True)
        with self.assertRaises(RuntimeError):
            router.recognize(b"abc")

    def test_recognize_uses_base64_payload(self):
        engine = PaddleOCRJsonEngine()
        fake_response = {
            "code": 100,
            "data": [{"text": "日记", "score": 0.99, "box": []}],
        }
        with (
            patch.object(engine, "resolve_exe", return_value=Path("fake.exe")),
            patch.object(engine, "is_available", return_value=True),
            patch.object(engine, "_run_dict_unlocked", return_value=fake_response) as run,
        ):
            # Minimal JPEG-ish bytes are fine; recognize only base64-encodes them.
            result = engine.recognize(b"\xff\xd8\xfffakejpeg")
        self.assertEqual(result.engine, PADDLE_JSON_ENGINE_ID)
        self.assertEqual(result.text, "日记")
        payload = run.call_args[0][0]
        self.assertIn("image_base64", payload)


if __name__ == "__main__":
    unittest.main()
