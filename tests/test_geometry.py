import unittest

from diary_ocr.geometry import map_boxes, normalize_box


class GeometryTests(unittest.TestCase):
    def test_normalize_nested_box(self):
        pts = normalize_box([[1, 2], [3, 2], [3, 4], [1, 4]])
        self.assertEqual(pts, [(1.0, 2.0), (3.0, 2.0), (3.0, 4.0), (1.0, 4.0)])

    def test_map_boxes_scales(self):
        boxes = [
            {
                "text": "A",
                "score": 0.95,
                "box": [[0, 0], [100, 0], [100, 50], [0, 50]],
            }
        ]
        mapped = map_boxes(boxes, ocr_size=(200, 100), preview_size=(100, 50))
        self.assertEqual(len(mapped), 1)
        self.assertEqual(mapped[0]["box"][0], (0.0, 0.0))
        self.assertEqual(mapped[0]["box"][1], (50.0, 0.0))
        self.assertEqual(mapped[0]["box"][2], (50.0, 25.0))
        self.assertEqual(mapped[0]["text"], "A")

    def test_map_boxes_identity_without_sizes(self):
        boxes = [
            {"text": "B", "score": 1.0, "box": [[10, 10], [20, 10], [20, 20], [10, 20]]}
        ]
        mapped = map_boxes(boxes, None, None)
        self.assertEqual(mapped[0]["box"][0], (10.0, 10.0))


if __name__ == "__main__":
    unittest.main()
