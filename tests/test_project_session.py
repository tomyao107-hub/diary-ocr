import json
import tempfile
import unittest
from pathlib import Path

from diary_ocr.session import ProjectSession


class ProjectSessionTests(unittest.TestCase):
    def test_session_persists_relative_paths_and_survives_move(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original = root / "original"
            pages = original / "pages"
            pages.mkdir(parents=True)
            first = pages / "a.jpg"
            second = pages / "b.jpg"
            first.write_bytes(b"a")
            second.write_bytes(b"b")
            session = ProjectSession(original)
            self.assertTrue(
                session.save(
                    [str(first), str(second)],
                    current_index=1,
                    output_dir=str(original / "output"),
                    done_indices={0},
                    draft_texts={1: "draft"},
                )
            )
            raw = json.loads(session.path.read_text(encoding="utf-8"))
            self.assertEqual(raw["schema_version"], 2)
            self.assertEqual(
                [item["path"] for item in raw["pages"]],
                ["pages/a.jpg", "pages/b.jpg"],
            )
            self.assertEqual(raw["pages"][0]["status"], "succeeded")
            self.assertEqual(raw["pages"][1]["status"], "pending")
            self.assertNotIn(str(original), session.path.read_text(encoding="utf-8"))

            moved = root / "moved"
            original.rename(moved)
            restored = ProjectSession(moved)
            data = restored.load()
            paths, current, done, drafts = restored.materialize(data)
            self.assertEqual(paths, [str(moved / "pages/a.jpg"), str(moved / "pages/b.jpg")])
            self.assertEqual(current, 1)
            self.assertEqual(done, {0})
            self.assertEqual(drafts, {1: "draft"})


if __name__ == "__main__":
    unittest.main()

