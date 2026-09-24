"""PDF text extraction with automatic OCR fallback for scanned pages."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pymupdf

from .ocr import get_ocr_engine


@dataclass
class PageText:
    page_no: int          # 1-based
    text: str
    ocr: bool
    ocr_conf: float | None
    needs_ocr: bool = False


MIN_TEXT_CHARS = 25


def extract_pages(path: Path | str) -> list[PageText]:
    doc = pymupdf.open(path)
    out: list[PageText] = []
    engine = None
    for i, page in enumerate(doc):
        text = page.get_text("text") or ""
        if len(text.strip()) >= MIN_TEXT_CHARS:
            out.append(PageText(i + 1, text, False, None))
            continue
        # Image-only (scanned) page -> OCR
        engine = engine or get_ocr_engine()
        if engine is None:
            out.append(PageText(i + 1, text, False, None, needs_ocr=True))
            continue
        pix = page.get_pixmap(dpi=200, colorspace=pymupdf.csGRAY)
        ocr_text, conf = engine.recognize(pix.tobytes("png"))
        out.append(PageText(i + 1, ocr_text, True, conf))
    doc.close()
    return out
