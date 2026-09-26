"""Unit tests for the drilling NLP layer (no database needed)."""
from stratasense.ingest.nlp import (analyse_sentence, find_mitigations, find_outcome, is_hypothetical, ocr_fix_numbers,
                             parse_depths, parse_mw, parse_rate, parse_volume, repair_ocr_spacing, split_sentences)
from stratasense.search.query import parse_query


def test_depth_units_and_md_tvd():
    d = parse_depths("Observed losses at 2,138 m MD and again @ 7,020 ft; TD 2250 mTVD")
    assert [round(x["value_m"]) for x in d] == [2138, 2140, 2250]
    assert d[2]["kind"] == "tvd"
    # volumetric rates must not be read as depths
    assert parse_depths("loss rate 5.6 m3/hr") == []


def test_mud_weight_and_rate_normalisation():
    assert parse_mw("MW 1.21 SG") == [10.1]
    assert parse_mw("MW: 10.05 ppg") == [10.05]
    assert parse_rate("losing 5.0 m3/hr") == 31.4          # m3/hr -> bbl/hr
    assert parse_rate("partial losses 35 bbl/hr") == 35
    assert parse_volume("pit gain of 12 bbl") == 12


def test_negation_and_hypothetical_are_not_events():
    assert analyse_sentence("No losses observed.", 1, 0, 1, 0).hazard is None
    assert "LOSS" in analyse_sentence("No losses observed.", 1, 0, 1, 0).negated_hazards
    assert analyse_sentence("LOSSES: NIL.", 1, 0, 1, 0).hazard is None
    s = analyse_sentence("Observed partial losses at 2,138 m in Tipam sand, loss rate 35 bbl/hr.", 1, 0, 1, 0)
    assert s.hazard == "LOSS" and s.formation == "TIPAM"
    assert is_hypothetical("As per offset data, anticipated losses in Tipam; watch for returns.")
    assert not is_hypothetical("Observed partial losses at 2,138 m.")


def test_most_specific_hazard_wins():
    assert analyse_sentence("Observed losses during cementing of 9-5/8\" casing.", 1, 0, 1, 0).hazard == "CEMENT"
    assert analyse_sentence("Pack-off while drilling at 1,900 m, unable to move string.", 1, 0, 1, 0).hazard == "STUCK"
    assert analyse_sentence("Bit balled up while drilling sticky clay at 1,701 m.", 1, 0, 1, 0).hazard == "TIGHT"


def test_mitigations_and_outcomes():
    assert find_mitigations("Spotted 50 ppb coarse lcm pill.") == ["LCM_COARSE"]
    assert find_mitigations("Pumped 30 ppb sized calcium carbonate pill.") == ["LCM_FINE"]
    assert "SHUT_IN_DM" in find_mitigations("Shut in the well and circulated out influx by driller's method.")
    assert find_outcome("Losses continued.") == "fail"
    assert find_outcome("Full returns regained.") == "success"


def test_ocr_repairs():
    assert ocr_fix_numbers("cavings at 3,2i1 m") == "cavings at 3,211 m"
    fixed = repair_ocr_spacing("Whiledrillingaheadat191lmexperiencedpartiallossesof51bbl/hr.Spotted50ppbcoarse lcmpill.")
    assert "partial losses" in fixed and "coarse lcm pill" in fixed and "1911" in fixed


def test_sentence_split_handles_ocr_merged_periods():
    parts = [s for _, _, s in split_sentences("Observed losses at shoe 2,262m MD.Carried out squeeze cementation.")]
    assert parts == ["Observed losses at shoe 2,262m MD.", "Carried out squeeze cementation."]


def test_query_parser():
    pq = parse_query("losses in Tipam within 5 km below 2000 m after 2015")
    assert pq.hazards == ["LOSS"] and pq.formation == "TIPAM"
    assert pq.radius_km == 5 and pq.md_min == 2000 and pq.year_min == 2015
    pq = parse_query("stuck pipe near NDH-09")
    assert pq.near_well == "NDH-09" and "STUCK" in pq.hazards
