"""Unit tests for PP-OCR local engine (no model download required)."""

import io
import os
import unittest
from unittest.mock import MagicMock, patch

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image

from diary_ocr.engines.local import LOCAL_ENGINE_ID, PPOCRLocalEngine
from diary_ocr.engines.registry import HybridRouter, default_registry


def _jpeg_bytes(color: str = "white") -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (40, 20), color).save(buffer, format="JPEG")
    return buffer.getvalue()


class FakePageResult:
    def __init__(self, texts):
        self.rec_texts = texts


class PPOCREngineTests(unittest.TestCase):
    def test_resolve_model_names_v5_mobile(self):
        engine = PPOCRLocalEngine(ocr_version="PP-OCRv5", model_size="mobile")
        det, rec = engine._resolve_model_names()
        self.assertEqual(det, "PP-OCRv5_mobile_det")
        self.assertEqual(rec, "PP-OCRv5_mobile_rec")

    def test_resolve_model_names_v6_small_alias(self):
        engine = PPOCRLocalEngine(ocr_version="PP-OCRv6", model_size="mobile")
        det, rec = engine._resolve_model_names()
        self.assertEqual(det, "PP-OCRv6_small_det")
        self.assertEqual(rec, "PP-OCRv6_small_rec")

    def test_extract_texts_from_rec_texts_object(self):
        page = FakePageResult(["甲", "乙"])
        lines = PPOCRLocalEngine._extract_texts([page])
        self.assertEqual(lines, ["甲", "乙"])

    def test_extract_texts_legacy_list(self):
        legacy = [
            [
                [[0, 0], [1, 0], [1, 1], [0, 1]],
                ("你好", 0.99),
            ]
        ]
        lines = PPOCRLocalEngine._extract_texts(legacy)
        self.assertEqual(lines, ["你好"])

        boxes = PPOCRLocalEngine._extract_boxes(legacy)
        self.assertEqual(len(boxes), 1)
        self.assertEqual(boxes[0]["text"], "你好")
        self.assertEqual(boxes[0]["box"], legacy[0][0])

    def test_extract_numpy_boxes_from_v3_result(self):
        page = FakePageResult(np.array(["甲"], dtype=object))
        page.rec_polys = np.array(
            [[[0, 0], [10, 0], [10, 5], [0, 5]]], dtype=np.float32
        )
        page.rec_scores = np.array([0.9], dtype=np.float32)

        self.assertEqual(PPOCRLocalEngine._extract_texts([page]), ["甲"])
        boxes = PPOCRLocalEngine._extract_boxes([page])

        self.assertEqual(len(boxes), 1)
        self.assertEqual(boxes[0]["text"], "甲")
        self.assertAlmostEqual(boxes[0]["score"], 0.9, places=5)
        self.assertEqual(boxes[0]["box"][2], [10.0, 5.0])

    def test_extract_boxes_from_nested_json_result(self):
        page = MagicMock(spec=[])
        page.json = {
            "res": {
                "rec_texts": ["乙"],
                "rec_scores": [0.8],
                "rec_polys": [[[1, 2], [3, 2], [3, 4], [1, 4]]],
            }
        }

        boxes = PPOCRLocalEngine._extract_boxes([page])

        self.assertEqual(boxes[0]["text"], "乙")
        self.assertEqual(boxes[0]["box"][0], [1.0, 2.0])

    def test_recognize_uses_predict_and_joins_lines(self):
        engine = PPOCRLocalEngine(ocr_version="PP-OCRv5", model_size="mobile")
        fake_pipeline = MagicMock()
        fake_pipeline.predict.return_value = [FakePageResult(["第一行", "第二行"])]
        engine._pipeline = fake_pipeline
        with patch.object(engine, "is_available", return_value=True):
            result = engine.recognize(_jpeg_bytes())
        self.assertEqual(result.engine, LOCAL_ENGINE_ID)
        self.assertEqual(result.text, "第一行\n第二行")
        self.assertIn("PP-OCRv5", result.model)
        fake_pipeline.predict.assert_called_once()

    def test_hybrid_router_prefers_ppocr_when_available(self):
        registry = default_registry(api_key="k", include_mock=False)
        local = registry.get(LOCAL_ENGINE_ID)
        paddle_json = registry.get("paddleocr-json")
        self.assertIsNotNone(local)
        # When portable engine is absent, in-process PP-OCR is selected.
        with (
            patch.object(paddle_json, "is_available", return_value=False),
            patch.object(local, "is_available", return_value=True),
        ):
            router = HybridRouter(registry, mode="local")
            decision = router.decide()
            self.assertEqual(decision.engine_id, LOCAL_ENGINE_ID)


if __name__ == "__main__":
    unittest.main()
