"""Read the existing text layer of a PDF without recognizing images."""

import re
from pathlib import Path

from pypdf import PdfReader


class PdfExtractionError(ValueError):
    """The PDF has no usable text or cannot be opened."""


def _page_text(page):
    resources = page.get("/Resources")
    images = resources.get_object().get("/XObject") if resources else None
    images = images.get_object() if images else {}
    area = float(page.mediabox.width * page.mediabox.height)
    has_large_image = False

    def inspect_operand(operator, operands, matrix, _text_matrix):
        nonlocal has_large_image
        if operator == b"Do" and operands:
            image = images.get(operands[0])
            scale_x, shear_y, shear_x, scale_y, *_ = matrix
            if (image and image.get_object().get("/Subtype") == "/Image"
                    and abs(scale_x * scale_y - shear_y * shear_x) >= area * 0.55):
                has_large_image = True

    text = (page.extract_text(visitor_operand_before=inspect_operand) or "").strip()
    return "" if has_large_image and len(re.sub(r"\s+", "", text)) <= 36 else text


def extract_pdf_pages(path: Path) -> list[tuple[int, str]]:
    try:
        with Path(path).open("rb") as source:
            reader = PdfReader(source)
            if reader.is_encrypted and not reader.decrypt(""):
                raise PdfExtractionError("PDF 受密码保护，请先解密。")
            pages = [_page_text(page) for page in reader.pages]
    except PdfExtractionError:
        raise
    except Exception as exc:
        raise PdfExtractionError("无法读取 PDF，请检查文件是否完整。") from exc
    if not any(pages):
        raise PdfExtractionError("未找到可读取的 PDF 文字层，请核对原件或使用文字版 PDF。")
    return list(enumerate(pages, 1))
