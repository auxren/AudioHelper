"""Tests for recording-source and mic detection (ported from TagCleaner)."""
from audiohelper.tc_sources import detect_source


def test_sbd_kind():
    assert detect_source("SBD > DAT > CDR > FLAC").kind == "SBD"


def test_aud_kind():
    assert detect_source("AUD recording from the lawn").kind == "AUD"


def test_matrix_beats_sbd():
    # Matrix is more specific and listed first
    assert detect_source("Matrix of SBD + AUD").kind == "Matrix"


def test_schoeps_mk_model():
    s = detect_source("Schoeps MK4 > Nakamichi CR-7A > DAT")
    assert "Schoeps MK4" in s.mics


def test_akg_model():
    s = detect_source("AKG 414 > Zoom H4n")
    assert "AKG 414" in s.mics
    assert "Zoom H4n" in s.mics


def test_dedupe_drops_bare_family():
    s = detect_source("AKG and AKG 414 used")
    # bare "AKG" dropped when "AKG 414" is present
    assert "AKG 414" in s.mics
    assert "AKG" not in s.mics


def test_label_format():
    s = detect_source("SBD AKG 414")
    assert s.label() == "[SBD AKG 414]"


def test_empty_source_blank_label():
    assert detect_source("").label() == ""
    assert detect_source("just some words").label() == ""
