"""OCR on scanned reports: digit repair, casing-shoe depths, and full-width lines surviving recognition."""
import pytest

from nwis.ingest.pdf import fix_ocr_digits
from nwis.ingest.pipeline import WellCtx, _named_shoe, detect_kind


@pytest.mark.parametrize("raw,fixed", [
    ("at 3,21l m in Barail", "at 3,211 m in Barail"),
    ("at 1,O45 m", "at 1,045 m"),
    ("Lost 1OO bbl", "Lost 100 bbl"),
    ("NPT: l0.8 hrs", "NPT: 10.8 hrs"),
    ("MW lO.2 ppg", "MW 10.2 ppg"),
    ("in 2O12", "in 2012"),
    # words and codes that must never change
    ("Well BDA-01 Oil", "Well BDA-01 Oil"),
    ("ISO 9001", "ISO 9001"),
    ("BDA-O1", "BDA-O1"),
    ("I/O check", "I/O check"),
    ("partial loss at 2,100 m; low ROP", "partial loss at 2,100 m; low ROP"),
    ("Surface location", "Surface location"),
    ("Iodine lO5 m", "Iodine 105 m"),
    ('12-1/4" hole: 9-5/8" casing', '12-1/4" hole: 9-5/8" casing'),
])
def test_fix_ocr_digits(raw, fixed):
    assert fix_ocr_digits(raw) == fixed


def test_report_kind_survives_glued_ocr_headings():
    assert detect_kind("NWIS DEMO\nWELL COMPLETIONREPORT\nWell: SSN-01") == "WCR"
    assert detect_kind("DAILYDRILLING REPORT - NDH-21") == "DDR"
    assert detect_kind("Mud logging summary") == "OTHER"


def test_cementing_sentence_is_placed_at_the_named_casing_shoe():
    ctx = WellCtx("W", {}, {}, None, [{"casing": '20"', "shoe_md": 76.0}, {"casing": '13-3/8"', "shoe_md": 1351.0},
                                       {"casing": '9-5/8"', "shoe_md": 2758.0}, {"casing": '7" liner', "shoe_md": 4260.0}])
    assert _named_shoe('Losses during cementing of 9-5/8" casing; partial returns.', ctx) == 2758.0
    assert _named_shoe("losses during cementing of 9-5/8casing", ctx) == 2758.0      # quotes and spaces lost by OCR
    assert _named_shoe('Remedial squeeze on the 7" liner.', ctx) == 4260.0
    assert _named_shoe("Poor cement bond across Tipam.", ctx) is None
    assert _named_shoe('cementing of 9-5/8" casing', None) is None


def test_full_width_lines_survive_ocr(tmp_path):
    """RapidOCR's angle classifier used to flip long lines to 180 degrees and return nothing for them."""
    pytest.importorskip("rapidocr_onnxruntime")
    from nwis.data import docs_gen
    from nwis.ingest.pdf import extract_pages

    long_line = "- High torque with torque spikes observed while drilling at 2,853 m in Barail. Reduced drilling"
    w = docs_gen.PdfWriter(fontsize=8.6)
    w.new_page()
    w.line("4. DRILLING COMPLICATIONS", bold=True)
    w.line(long_line)
    w.line("parameters. Torque normalised. NPT: 1.3 hrs.")
    w.line("- Splintery cavings observed on shakers at 3,211 m in Barail; hole instability. Control drilled")
    src, scan = tmp_path / "t.pdf", tmp_path / "s.pdf"
    w.save(src)
    docs_gen.rasterize_pdf(src, scan, seed=3)
    page = extract_pages(scan)[0]
    assert page.ocr
    text = " ".join(page.text.split())
    assert "torque spikes observed while drilling at 2,853 m" in text
    assert "Torque normalised" in text                       # word spaces kept
    assert "3,211 m in Barail" in text
