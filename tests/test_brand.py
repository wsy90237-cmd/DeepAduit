"""品牌单一来源：名称、图标、启动文件与安装说明必须指向同一产品。"""

import unittest
from pathlib import Path

from PIL import Image

from tb_autofill import APP_NAME, __version__, brand


class BrandTests(unittest.TestCase):
    def test_product_name_and_executable_are_attesta(self):
        self.assertEqual(APP_NAME, "Attesta")
        self.assertEqual(brand.EXECUTABLE, "Attesta.exe")
        self.assertRegex(__version__, r"^\d+\.\d+\.\d+$")

    def test_every_icon_size_exists_square_and_orange(self):
        for size in brand.ICON_SIZES:
            path = brand.icon(size)
            self.assertTrue(path.is_file(), path)
            with Image.open(path) as image:
                self.assertEqual(image.size, (size, size))
                red, green, blue = image.convert("RGB").getpixel((size // 2, max(1, size // 10)))
                self.assertGreater(red, green, "图标主色必须是橙色")
                self.assertGreater(green, blue, "图标主色必须是橙色")

    def test_unknown_icon_size_is_rejected(self):
        with self.assertRaises(ValueError):
            brand.icon(64)

    def test_windows_icon_covers_small_and_large_sizes(self):
        path = Path(__file__).resolve().parents[1] / "packaging" / "attesta.ico"
        self.assertTrue(path.is_file(), path)
        with Image.open(path) as image:
            sizes = set(image.info.get("sizes", {image.size}))
        self.assertIn((16, 16), sizes)
        self.assertIn((256, 256), sizes)

    def test_launchers_are_ascii_crlf_and_start_the_app(self):
        root = Path(__file__).resolve().parents[1]
        for name in brand.PACKAGE_FILES:
            if not name.endswith(".cmd"):
                continue
            data = (root / name).read_bytes()
            data.decode("ascii")
            self.assertNotIn(b"\n", data.replace(b"\r\n", b""))
            self.assertIn(b"runtime\\python", data)
            self.assertIn(b"tools\\run_gui.py", data)

    def test_copyright_and_company_have_one_source(self):
        self.assertEqual(brand.COMPANY, "WSY")
        self.assertEqual(brand.COPYRIGHT, "© WSY")
        self.assertEqual(brand.PRODUCT_NAME, APP_NAME)
        self.assertIn(f"v{__version__}", brand.FOOTER_TEXT)
        self.assertIn(brand.COPYRIGHT, brand.FOOTER_TEXT)

    def test_version_resource_fills_product_name_version_and_copyright(self):
        text = brand.version_info_text()
        # Windows 属性页的四项：产品名称、产品版本、文件说明、版权。
        self.assertIn("StringStruct('ProductName', 'Attesta')", text)
        self.assertIn(f"StringStruct('ProductVersion', '{__version__}')", text)
        self.assertIn("StringStruct('LegalCopyright', '© WSY')", text)
        self.assertIn("StringStruct('CompanyName', 'WSY')", text)
        self.assertIn("StringStruct('OriginalFilename', 'Attesta.exe')", text)
        self.assertIn("VSVersionInfo(", text)
        self.assertEqual(text.count("StringStruct("), 8)

    def test_version_numbers_are_four_pe_fields(self):
        numbers = brand.version_numbers()
        self.assertEqual(len(numbers), 4)
        self.assertEqual(".".join(str(item) for item in numbers[:3]), __version__)


if __name__ == "__main__":
    unittest.main()
