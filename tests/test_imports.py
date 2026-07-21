import tempfile
import unittest
from pathlib import Path

from PIL import Image

from diary_ocr.image_import import import_folder, import_images
from diary_ocr.pdf_import import import_pdf, validate_page_range


class ImportTests(unittest.TestCase):
    def test_image_import_is_self_contained_and_renames_collisions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "project"
            (project / "sources").mkdir(parents=True)
            (project / "pages").mkdir()
            external = root / "external"
            external.mkdir()
            first_src = external / "one" / "page.jpg"
            second_src = external / "two" / "page.jpg"
            first_src.parent.mkdir(parents=True)
            second_src.parent.mkdir(parents=True)
            # Different pixels so SHA-256 dedup does not collapse them.
            Image.new("RGB", (20, 20), "white").save(first_src)
            Image.new("RGB", (20, 20), "black").save(second_src)

            first = import_images(project, [first_src])[0]
            second = import_images(project, [second_src])[0]
            first_src.unlink()
            second_src.unlink()

            self.assertTrue(first.source.exists())
            self.assertTrue(first.page.exists())
            self.assertTrue(second.page.exists())
            self.assertNotEqual(first.page, second.page)

    def test_folder_import_is_recursive_and_naturally_sorted(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "project"
            source = root / "source"
            (source / "nested").mkdir(parents=True)
            colors = {
                "nested/page10.png": "red",
                "nested/page2.png": "green",
                "page1.png": "blue",
            }
            for relative, color in colors.items():
                path = source / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                Image.new("RGB", (10, 10), color).save(path)

            imported = import_folder(project, source)
            self.assertEqual(
                [item.page.name for item in imported],
                ["page1.png", "page2.png", "page10.png"],
            )

    def test_pdf_range_import_renders_selected_pages(self):
        import fitz

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "project"
            pdf = root / "book.pdf"
            document = fitz.open()
            for number in range(5):
                page = document.new_page(width=100, height=100)
                page.insert_text((10, 50), f"page {number + 1}")
            document.save(str(pdf))
            document.close()

            progress = []
            pages = import_pdf(
                project,
                pdf,
                start_page=2,
                end_page=4,
                dpi=72,
                progress=lambda current, total, path: progress.append(
                    (current, total, path.name)
                ),
            )

            self.assertEqual(len(pages), 3)
            self.assertEqual(progress[-1][:2], (3, 3))
            self.assertTrue((project / "sources" / "book.pdf").exists())
            self.assertEqual(
                [path.name for path in pages],
                ["book_p0002.jpg", "book_p0003.jpg", "book_p0004.jpg"],
            )

    def test_pdf_range_validation(self):
        with self.assertRaises(ValueError):
            validate_page_range(5, 0, 3)
        with self.assertRaises(ValueError):
            validate_page_range(5, 4, 3)
        with self.assertRaises(ValueError):
            validate_page_range(5, 1, 6)


if __name__ == "__main__":
    unittest.main()

