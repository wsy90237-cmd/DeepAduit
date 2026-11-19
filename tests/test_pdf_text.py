from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject, NumberObject

from tb_autofill import pdf_text


class PdfExtractionTests(unittest.TestCase):
    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = Path(folder.name) / "合成 PDF.pdf"

    def test_text_layer_preserves_original_page_numbers(self):
        writer = PdfWriter()
        writer.add_blank_page(400, 400)
        page = writer.add_blank_page(400, 400)
        stream = DecodedStreamObject()
        stream.set_data(b"BT /F1 12 Tf 20 20 Td (Synthetic text layer) Tj ET")
        page.replace_contents(stream)
        page[NameObject("/Resources")] = DictionaryObject({
            NameObject("/Font"): DictionaryObject({
                NameObject("/F1"): DictionaryObject({
                    NameObject("/Type"): NameObject("/Font"),
                    NameObject("/Subtype"): NameObject("/Type1"),
                    NameObject("/BaseFont"): NameObject("/Helvetica"),
                }),
            }),
        })
        writer.add_blank_page(400, 400)
        writer.write(self.path)

        self.assertEqual(pdf_text.extract_pdf_pages(self.path), [(1, ""), (2, "Synthetic text layer"), (3, "")])

    def test_password_protected_pdf_reports_specific_error(self):
        writer = PdfWriter()
        writer.add_blank_page(400, 400)
        writer.encrypt("synthetic-password")
        writer.write(self.path)

        with self.assertRaisesRegex(pdf_text.PdfExtractionError, "密码保护"):
            pdf_text.extract_pdf_pages(self.path)

    def test_thin_number_layer_over_large_image_is_not_used_as_text(self):
        writer = PdfWriter()
        page = writer.add_blank_page(400, 400)
        image = DecodedStreamObject()
        image.set_data(b"\xff")
        image.update({
            NameObject("/Subtype"): NameObject("/Image"),
            NameObject("/Width"): NumberObject(1),
            NameObject("/Height"): NumberObject(1),
            NameObject("/ColorSpace"): NameObject("/DeviceGray"),
            NameObject("/BitsPerComponent"): NumberObject(8),
        })
        layer = DecodedStreamObject()
        layer.set_data(b"q 400 0 0 400 0 0 cm /Im0 Do Q BT /F1 12 Tf 20 20 Td (1) Tj ET")
        page.replace_contents(layer)
        page[NameObject("/Resources")] = DictionaryObject({
            NameObject("/XObject"): DictionaryObject({NameObject("/Im0"): writer._add_object(image)}),
            NameObject("/Font"): DictionaryObject({
                NameObject("/F1"): DictionaryObject({
                    NameObject("/Subtype"): NameObject("/Type1"),
                    NameObject("/BaseFont"): NameObject("/Helvetica"),
                }),
            }),
        })
        writer.write(self.path)

        with self.assertRaisesRegex(pdf_text.PdfExtractionError, "文字层"):
            pdf_text.extract_pdf_pages(self.path)
