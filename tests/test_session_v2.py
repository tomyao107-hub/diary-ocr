import json
import tempfile
import unittest
from pathlib import Path

from diary_ocr.ocr_task import PageTask, TaskStatus, recover_running_tasks, select_jobs
from diary_ocr.session import SESSION_SCHEMA_VERSION, ProjectSession


def _key(path: str) -> str:
    import os

    return os.path.normcase(os.path.abspath(path))


class SessionV2Tests(unittest.TestCase):
    def test_schema1_migrates_to_schema2_with_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pages = root / "pages"
            pages.mkdir()
            first = pages / "a.jpg"
            second = pages / "b.jpg"
            first.write_bytes(b"a")
            second.write_bytes(b"b")
            (root / "output").mkdir()
            (root / "output" / "a.md").write_text("done", encoding="utf-8")

            session_path = root / "session.json"
            session_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "pages": ["pages/a.jpg", "pages/b.jpg"],
                        "current_page": "pages/b.jpg",
                        "done_pages": ["pages/a.jpg"],
                        "drafts": {"pages/b.jpg": "draft"},
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            session = ProjectSession(root)
            data = session.load()
            self.assertIsNotNone(data)
            self.assertEqual(data["schema_version"], SESSION_SCHEMA_VERSION)
            self.assertTrue((root / "session.json.bak").exists())

            tasks = session.materialize_tasks(data)
            self.assertEqual(tasks[_key(str(first))].status, TaskStatus.SUCCEEDED.value)
            self.assertEqual(tasks[_key(str(second))].status, TaskStatus.PENDING.value)

            # Migration is idempotent.
            data2 = session.load()
            self.assertEqual(data2["schema_version"], SESSION_SCHEMA_VERSION)

    def test_running_recovers_to_pending(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pages = root / "pages"
            pages.mkdir()
            page = pages / "x.jpg"
            page.write_bytes(b"x")
            session = ProjectSession(root)
            tasks = {
                _key(str(page)): PageTask(
                    path="pages/x.jpg", status=TaskStatus.RUNNING.value, attempts=1
                )
            }
            self.assertTrue(
                session.save(
                    [str(page)],
                    0,
                    str(root / "output"),
                    set(),
                    {},
                    page_tasks=tasks,
                )
            )
            data = session.load()
            restored = session.materialize_tasks(data)
            self.assertEqual(
                restored[_key(str(page))].status, TaskStatus.PENDING.value
            )

    def test_select_jobs_modes(self):
        paths = ["a.jpg", "b.jpg", "c.jpg"]
        tasks = {
            "a.jpg": PageTask(path="a.jpg", status=TaskStatus.SUCCEEDED.value),
            "b.jpg": PageTask(path="b.jpg", status=TaskStatus.FAILED.value),
            "c.jpg": PageTask(path="c.jpg", status=TaskStatus.PENDING.value),
        }
        unfinished = select_jobs(paths, tasks, "unfinished", key_fn=lambda p: p)
        failed = select_jobs(paths, tasks, "failed", key_fn=lambda p: p)
        all_jobs = select_jobs(paths, tasks, "all", key_fn=lambda p: p)
        self.assertEqual([p for _, p in unfinished], ["b.jpg", "c.jpg"])
        self.assertEqual([p for _, p in failed], ["b.jpg"])
        self.assertEqual(len(all_jobs), 3)

    def test_recover_running_helper(self):
        tasks = [
            PageTask(path="a", status=TaskStatus.RUNNING.value),
            PageTask(path="b", status=TaskStatus.SUCCEEDED.value),
        ]
        recovered = recover_running_tasks(tasks)
        self.assertEqual(len(recovered), 1)
        self.assertEqual(tasks[0].status, TaskStatus.PENDING.value)


if __name__ == "__main__":
    unittest.main()
