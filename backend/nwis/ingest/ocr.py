"""Pluggable OCR engines (all on-prem). RapidOCR (ONNX, pip-only) preferred, Tesseract fallback."""
from __future__ import annotations

import io
import shutil
from functools import lru_cache


class RapidOCREngine:
    name = "rapidocr-onnx"
    MIN_SCORE = 0.5    # recognitions below this are noise (speckle, stamps, rule lines)

    def __init__(self):
        # rapidocr-onnxruntime (1.x) stops at Python 3.12; its successor, rapidocr (3.x), covers newer Pythons.
        # Both ship their ONNX models inside the wheel, so neither needs the network at run time.
        try:
            from rapidocr_onnxruntime import RapidOCR  # type: ignore
            self._v3 = False
        except ImportError:
            from rapidocr import RapidOCR  # type: ignore
            self._v3 = True
            self.name = "rapidocr-onnx v3"
        self._ocr = RapidOCR()

    def _detect(self, png: bytes) -> list:
        if self._v3:
            det = self._ocr(png, use_det=True, use_cls=False, use_rec=False)
            return [] if det.boxes is None else [b.tolist() for b in det.boxes]
        boxes, _ = self._ocr(png, use_rec=False, use_cls=False)
        return boxes or []

    def _rec(self, crops: list) -> list[tuple[str, float]]:
        if self._v3:
            from rapidocr.ch_ppocr_rec import TextRecInput  # type: ignore
            out = self._ocr.text_rec(TextRecInput(img=crops))
            return list(zip(out.txts or (), out.scores or ()))
        rec = self._rec(crops)
        return [(t, sc) for t, sc in rec]

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
        """Detect lines, then recognise each one from a padded, upright crop.

        RapidOCR's own det -> cls -> rec chain loses text on report scans in two ways: the angle classifier flips
        long, thin full-width lines to 180 degrees (they come back as '' or a single letter), and the tight
        detector crops make the recogniser drop word spaces ('Torquenormalised'). Report pages are never
        upside-down, so the classifier is skipped, and each line gets a little white margin before recognition.
        """
        import numpy as np
        from PIL import Image
        boxes = self._detect(png)
        if not boxes:
            return "", 0.0, 0
        a = np.asarray(Image.open(io.BytesIO(png)).convert("L"))
        H, W = a.shape
        crops, geo = [], []
        for box in boxes:
            xs = [p[0] for p in box]
            ys = [p[1] for p in box]
            x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
            pad = max(4.0, 0.2 * (y1 - y0))
            crop = a[max(0, int(y0 - pad)):min(H, int(y1 + pad) + 1), max(0, int(x0 - pad)):min(W, int(x1 + pad) + 1)]
            if crop.size == 0:
                continue
            crops.append(np.ascontiguousarray(np.dstack([crop] * 3)))
            geo.append((y0, y1, x0))
        if not crops:
            return "", 0.0, 0
        rec = self._rec(crops)
        # items: (top, bottom, left, text, score) -> rebuild reading order line by line
        items = [(y0, y1, x0, t.strip(), float(sc)) for (y0, y1, x0), (t, sc) in zip(geo, rec)
                 if t.strip() and float(sc) >= self.MIN_SCORE]
        if not items:
            return "", 0.0, 0
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
