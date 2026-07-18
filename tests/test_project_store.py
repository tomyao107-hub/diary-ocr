import json
import tempfile
import unittest
from pathlib import Path

from diary_ocr.project_store import ProjectStore


class ProjectStoreTests(unittest.TestCase):
    def test_projects_are_isolated_sorted_and_archivable(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ProjectStore(Path(directory))
            first = store.create("第一本日记")
            second = store.create("第一本日记")

            self.assertNotEqual(first.path, second.path)
            self.assertTrue((first.path / "sources").is_dir())
            self.assertTrue((first.path / "pages").is_dir())
            self.assertTrue((first.path / "output").is_dir())
            self.assertEqual(len(store.list_projects()), 2)

            store.archive(first)
            self.assertEqual(store.list_projects(), [second])
            self.assertEqual(len(store.list_projects(include_archived=True)), 2)

    def test_delete_removes_only_managed_project(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = ProjectStore(root / "projects")
            project = store.create("待删除")
            outside = root / "outside"
            outside.mkdir()
            (outside / "project.json").write_text("{}", encoding="utf-8")

            store.delete(project)
            self.assertFalse(project.path.exists())
            self.assertTrue(outside.exists())

    def test_project_metadata_has_separate_schema_version(self):
        with tempfile.TemporaryDirectory() as directory:
            project = ProjectStore(Path(directory)).create("Schema")
            data = json.loads(
                (project.path / "project.json").read_text(encoding="utf-8")
            )
            self.assertEqual(data["schema_version"], 1)
            self.assertTrue(data["id"])


if __name__ == "__main__":
    unittest.main()

