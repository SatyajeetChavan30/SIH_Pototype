"""Pluggable OCR engines (all on-prem). RapidOCR (ONNX, pip-only) preferred, Tesseract fallback."""
from __future__ import annotations

import io
import shutil
from functools import lru_cache


class RapidOCREngine:
    name = "rapidocr-onnx"

    def __init__(self):
        from rapidocr_onnxruntime import RapidOCR  # type: ignore
        self._ocr = RapidOCR()

    def recognize(self, png: bytes) -> tuple[str, float]:
        """OCR a page in horizontal strips cut on blank rows (full-page recognition drops lines on A4 scans)."""
        import numpy as np
        from PIL import Image
        img = Image.open(io.BytesIO(png)).convert("L")
        a = np.asarray(img)
        dark = (a < 110).sum(axis=1)
        dark = np.where(dark <= 2, 0, dark)  # tolerate scanner speckle on blank rows
        H = a.shape[0]
        cuts, y = [0], 0
        while y + 380 < H:
            lo, hi = y + 300, min(y + 460, H)
            blank = np.where(dark[lo:hi] == 0)[0]
            y = lo + int(blank[len(blank) // 2]) if len(blank) else y + 380
            cuts.append(y)
        cuts.append(H)
        texts, confs = [], []
        for y0, y1 in zip(cuts[:-1], cuts[1:]):
            if y1 - y0 < 10 or dark[y0:y1].sum() == 0:
                continue
            buf = io.BytesIO()
            img.crop((0, y0, img.width, y1)).save(buf, "PNG")
            t, c, n = self._recognize_one(buf.getvalue())
            if t:
                texts.append(t)
                confs += [c] * n
        return "\n".join(texts), round(float(sum(confs) / len(confs)), 3) if confs else 0.0

    def _recognize_one(self, png: bytes) -> tuple[str, float, int]:
        result, _ = self._ocr(png)
        if not result:
            return "", 0.0, 0
        # result: [ [box(4 pts), text, score], ... ] -> rebuild reading order line by line
        items = []
        for box, text, score in result:
            ys = [p[1] for p in box]
            xs = [p[0] for p in box]
            items.append((min(ys), max(ys), min(xs), text, float(score)))
        items.sort(key=lambda t: (t[0], t[2]))
        lines: list[list[tuple]] = []
        for it in items:
            if lines:
                last = lines[-1]
                y0 = sum(i[0] for i in last) / len(last)
                y1 = sum(i[1] for i in last) / len(last)
                mid = (it[0] + it[1]) / 2
                if y0 <= mid <= y1:
                    last.append(it)
                    continue
            lines.append([it])
        text = "\n".join(" ".join(i[3] for i in sorted(l, key=lambda t: t[2])) for l in lines)
        conf = sum(i[4] for i in items) / len(items)
        return text, conf, len(items)


class TesseractEngine:
    name = "tesseract"

    def __init__(self):
        import pytesseract  # type: ignore  # noqa: F401
        if not shutil.which("tesseract"):
            raise RuntimeError("tesseract binary not found")

    def recognize(self, png: bytes) -> tuple[str, float]:
        import pytesseract  # type: ignore
        from PIL import Image
        img = Image.open(io.BytesIO(png))
        data = pytesseract.image_to_data(img, output_type=pytesseract.Output.DICT)
        confs = [float(c) for c in data["conf"] if float(c) >= 0]
        return pytesseract.image_to_string(img), round(sum(confs) / max(len(confs), 1) / 100, 3)


@lru_cache(maxsize=1)
def get_ocr_engine():
    for cls in (RapidOCREngine, TesseractEngine):
        try:
            return cls()
        except Exception:
            continue
    return None


def ocr_status() -> dict:
    e = get_ocr_engine()
    return {"available": e is not None, "engine": getattr(e, "name", None)}
