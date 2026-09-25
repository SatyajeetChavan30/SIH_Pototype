"""Real-data validation pipeline (Equinor Volve format), exercised on a SYNTHETIC Volve-style fixture.

The fixture only mimics the XML structure (namespaces, ft depths, operator activity codes). Real numbers come
from `python -m nwis.cli validate-volve <folder>` on the downloaded public data.
"""
import pytest

from nwis import config
from nwis.ingest.witsml import parse_drill_reports, reports_to_pages
from nwis.validate.volve import code_hazard, labelled_sentences, load_reports, truth_events

NS = "http://www.witsml.org/schemas/1series"


def _report(well: str, day: int, acts: list[tuple[str, str, str, float]]) -> str:
    a = "".join(f"""<activity><dTimStart>2026-01-{day:02d}T{h}:00:00Z</dTimStart><dTimEnd>2026-01-{day:02d}T{h}:59:00Z</dTimEnd>
        <md uom="ft">{md}</md><proprietaryCode>{code}</proprietaryCode><state>{'fail' if 'interruption' in code else 'ok'}</state>
        <comments>{text}</comments></activity>""" for h, code, text, md in acts)
    return f"""<drillReport uidWell="{well}"><nameWell>{well}</nameWell><dTimStart>2026-01-{day:02d}T00:00:00Z</dTimStart>
      <fluid><density uom="g/cm3">1.30</density></fluid>{a}</drillReport>"""


def _write_fixture(folder, n_wells: int = 3):
    for w in range(n_wells):
        well = f"SYN 15/9-X-{w + 1}"
        reports = [
            _report(well, 1, [("08", "drilling -- drill", "Drilled 12 1/4in hole from 7200 ft to 7400 ft.", 7400)]),
            _report(well, 2, [("09", "interruption -- lost circulation", "Lost returns while drilling, approx 60 bbl/hr loss rate.", 7520),
                              ("10", "interruption -- lost circulation", "Pumped 50 bbl LCM pill, losses cured.", 7520),
                              ("11", "drilling -- drill", "Drilled ahead, no losses observed.", 7600)]),
            _report(well, 3, [("06", "interruption -- stuck pipe", "Pipe stuck at 8100 ft, unable to rotate.", 8100),
                              ("07", "interruption -- stuck pipe", "Jarred and worked pipe free.", 8100)]),
            _report(well, 4, [("12", "interruption -- fishing", "Ran overshot and recovered fish.", 8300)]),
        ]
        (folder / f"well_{w}.xml").write_text(f'<drillReports xmlns="{NS}" version="1.4.1.1">{"".join(reports)}</drillReports>',
                                              encoding="utf-8")


def test_code_mapping_and_truth_merging(tmp_path):
    _write_fixture(tmp_path, 1)
    reports = next(iter(load_reports(tmp_path).values()))
    assert code_hazard("interruption -- lost circulation") == "LOSS" and code_hazard("drilling -- drill") is None
    truth = truth_events(reports)
    assert [t["hazard"] for t in truth] == ["LOSS", "STUCK", "FISH"]           # the two LOSS activities merge
    assert truth[0]["md"] == pytest.approx(7520 * 0.3048)
    labels = [s["label"] for s in labelled_sentences(reports)]
    assert labels[:4] == ["NONE", "LOSS", "ACTION", "NONE"]


def test_free_text_rendering_does_not_leak_operator_codes(tmp_path):
    _write_fixture(tmp_path, 1)
    reports = parse_drill_reports((tmp_path / "well_0.xml").read_bytes())
    text = "\n".join(p.text for p in reports_to_pages(reports, include_codes=False))
    assert "interruption" not in text and "NPT" not in text and "[" not in text
    assert "Lost returns while drilling" in text
    assert "interruption -- lost circulation" in "\n".join(p.text for p in reports_to_pages(reports))


@pytest.mark.skipif(not (config.MODELS_DIR / "sentence_clf.joblib").exists(), reason="demo models not built")
def test_volve_evaluation_runs_end_to_end(tmp_path):
    from nwis.validate.volve import evaluate_volve
    _write_fixture(tmp_path, 3)
    res = evaluate_volve(tmp_path, k_steps=(0, 2), folds=3, log=lambda *_: None)
    assert res["n_wells"] == 3 and res["n_reports"] == 12
    assert res["zero_shot"]["n_truth"] == 9                                     # 3 wells x (LOSS, STUCK, FISH)
    assert 0.0 <= res["zero_shot"]["f1"] <= 1.0
    assert [c["k_report_days"] for c in res["adaptation"]] == [0, 2]
    assert all(f["test_wells"] for c in res["adaptation"] for f in c["folds"])
    assert res["code_mapping"]["interruption -- lost circulation"] == "LOSS"
