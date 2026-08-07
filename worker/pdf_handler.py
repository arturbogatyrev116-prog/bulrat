"""PDF text extraction with an optional OCR fallback for scanned documents."""

from __future__ import annotations

from pathlib import Path


def extract_text(pdf_path: str | Path, *, allow_ocr: bool = False) -> str:
    try:
        import fitz
    except ImportError as exc:  # pragma: no cover - dependency failure
        raise RuntimeError("PyMuPDF is required to process PDF tasks") from exc

    path = str(pdf_path)
    document = fitz.open(path)
    try:
        page_text = [page.get_text() for page in document]
    finally:
        document.close()
    text = "\n".join(page_text).strip()
    average_chars = len(text) / max(len(page_text), 1)
    if average_chars >= 100 or not allow_ocr:
        return text
    return _ocr_pdf(path)


def _ocr_pdf(pdf_path: str) -> str:
    try:
        from pdf2image import convert_from_path
        import pytesseract
    except ImportError as exc:  # pragma: no cover - dependency failure
        raise RuntimeError("pdf2image and pytesseract are required for PDF OCR") from exc
    return "\n".join(pytesseract.image_to_string(image) for image in convert_from_path(pdf_path)).strip()
