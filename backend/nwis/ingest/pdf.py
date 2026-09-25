"""PDF text extraction with automatic OCR fallback for scanned pages."""
from __future__ import annotations

import re
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

# OCR letter/digit confusions inside numbers ('3,21l m' -> '3,211 m', '1,O45' -> '1,045', 'l0.8' -> '10.8').
# A confusable letter is only changed when it touches a digit and is not part of a word, so 'Well', 'Oil',
# 'Iso' or 'MOL' never change.
_ONE = re.compile(r"(?<=[\d,])[lI|](?=[\dlIOo.,]|[\s;:)]|$)|(?<![A-Za-z\d])[lI|](?=\d|[Oo][.,]?\d)")
_ZERO = re.compile(r"(?<=[\d,.])[Oo](?=[\dOo.,]|[\s;:)]|$)")


def fix_ocr_digits(text: str) -> str:
    for _ in range(2):   # a second pass catches runs such as '1OO'
        text = _ZERO.sub("0", _ONE.sub("1", text))
    return text


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
        out.append(PageText(i + 1, fix_ocr_digits(ocr_text), True, conf))
    doc.close()
    return out
