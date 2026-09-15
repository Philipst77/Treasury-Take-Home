"""Unit tests for comparison rules. These run without OCR."""

import pytest

from app.matching import (
    FAIL, PASS, REVIEW, STANDARD_WARNING, Field,
    check_alcohol, check_net_contents, check_text_field, check_warning,
    normalize, parse_volumes_ml, verify_label,
)

LABEL = f"""
OLD TOM DISTILLERY
Kentucky Straight Bourbon Whiskey
45% Alc./Vol. (90 Proof)
750 mL
Distilled and bottled by Old Tom Distillery, Bardstown, Kentucky
{STANDARD_WARNING}
"""


# ---------------------------------------------------------------- text

def test_normalize_ignores_case_and_punctuation():
    assert normalize("STONE'S THROW") == normalize("Stone\u2019s Throw") == "stones throw"


@pytest.mark.parametrize("expected", ["OLD TOM DISTILLERY", "Old Tom Distillery", "old tom distillery"])
def test_brand_case_insensitive(expected):
    assert check_text_field(Field.BRAND, expected, LABEL).status == PASS


def test_brand_exact_case_note():
    r = check_text_field(Field.BRAND, "OLD TOM DISTILLERY", LABEL)
    assert r.note == "Exact match."
    assert r.found == "OLD TOM DISTILLERY"


def test_brand_small_typo_goes_to_review_or_pass():
    r = check_text_field(Field.BRAND, "Old Tom Distilery", LABEL)
    assert r.status in (PASS, REVIEW)


def test_brand_different_goes_to_fail():
    assert check_text_field(Field.BRAND, "Blue Heron Spirits", LABEL).status == FAIL


def test_ocr_noise_in_brand_still_passes():
    noisy = LABEL.replace("OLD TOM DISTILLERY", "OLD T0M DISTILLERY")
    assert check_text_field(Field.BRAND, "OLD TOM DISTILLERY", noisy).status == PASS


def test_partial_word_does_not_count_as_exact():
    # "Tom" must not match inside "Tomato"
    assert check_text_field(Field.BRAND, "Tom", "Tomato Liqueur").status != PASS


# ---------------------------------------------------------------- ABV

@pytest.mark.parametrize("expected", ["45%", "45% Alc./Vol.", "45% ABV", "45% Alc./Vol. (90 Proof)", "90 Proof"])
def test_abv_formats_match(expected):
    assert check_alcohol(expected, LABEL).status == PASS


def test_abv_mismatch():
    r = check_alcohol("40%", LABEL)
    assert r.status == FAIL
    assert "45%" in r.note


def test_proof_mismatch_fails():
    label = LABEL.replace("(90 Proof)", "(80 Proof)")
    assert check_alcohol("45% Alc./Vol. (90 Proof)", label).status == FAIL


def test_inconsistent_proof_on_label_needs_review():
    label = LABEL.replace("(90 Proof)", "(80 Proof)")
    assert check_alcohol("45%", label).status == REVIEW


def test_decimal_abv():
    assert check_alcohol("13.5%", "Red Wine 13.5% alc/vol").status == PASS
    assert check_alcohol("13%", "Red Wine 13.5% alc/vol").status == FAIL


def test_european_decimal_comma():
    assert check_alcohol("12.5%", "12,5% vol").status == PASS


def test_no_abv_on_label():
    assert check_alcohol("45%", "OLD TOM DISTILLERY 750 mL").status == FAIL


def test_unreadable_application_abv_needs_review():
    assert check_alcohol("forty five", LABEL).status == REVIEW


# ---------------------------------------------------------------- net contents

@pytest.mark.parametrize("text,ml", [
    ("750 mL", 750), ("750ML", 750), ("75 cl", 750), ("0.75 L", 750), ("1 Liter", 1000),
    ("25.4 FL. OZ.", 751.2), ("12 fl oz", 354.9),
])
def test_parse_volumes(text, ml):
    assert parse_volumes_ml(text)[0][0] == pytest.approx(ml, rel=0.001)


def test_net_contents_unit_conversion():
    assert check_net_contents("750 mL", "Contents 25.4 FL. OZ.").status == PASS
    assert check_net_contents("750 mL", "75cl").status == PASS


def test_net_contents_mismatch():
    r = check_net_contents("750 mL", "1 L")
    assert r.status == FAIL


def test_net_contents_missing():
    assert check_net_contents("750 mL", "OLD TOM").status == FAIL


# ---------------------------------------------------------------- warning

def test_exact_warning_passes():
    r = check_warning(LABEL)
    assert r.status == PASS
    assert "Bold" in r.note


def test_warning_across_line_breaks_and_hyphenation():
    wrapped = STANDARD_WARNING.replace("pregnancy", "pregnan-\ncy").replace(" women ", "\nwomen\n")
    assert check_warning(wrapped).status == PASS


def test_title_case_header_fails():
    r = check_warning(STANDARD_WARNING.replace("GOVERNMENT WARNING", "Government Warning"))
    assert r.status == FAIL
    assert "capital" in r.note


def test_missing_colon_fails():
    assert check_warning(STANDARD_WARNING.replace("WARNING:", "WARNING")).status == FAIL


def test_reworded_warning_fails():
    r = check_warning(STANDARD_WARNING.replace("health problems", "health issues"))
    assert r.status == FAIL
    assert "issues" in r.note


def test_dropped_clause_fails():
    r = check_warning(STANDARD_WARNING.replace(" or operate machinery,", ","))
    assert r.status == FAIL
    assert "missing" in r.note


def test_missing_warning_fails():
    assert check_warning("OLD TOM DISTILLERY 45% 750 mL").status == FAIL


def test_body_without_header_fails():
    body_only = STANDARD_WARNING.replace("GOVERNMENT WARNING: ", "")
    r = check_warning(body_only)
    assert r.status == FAIL
    assert "heading" in r.note


def test_ocr_noise_in_warning_goes_to_review():
    noisy = STANDARD_WARNING.replace("alcoholic beverages during", "alcoholic beverag3s during")
    assert check_warning(noisy).status == REVIEW


def test_text_after_warning_is_ignored():
    assert check_warning(STANDARD_WARNING + " Please recycle. Visit oldtom.example").status == PASS


# ---------------------------------------------------------------- overall

def test_verify_label_overall_pass():
    app = dict(brand_name="Old Tom Distillery", class_type="Kentucky Straight Bourbon Whiskey",
               alcohol_content="45%", net_contents="750 mL")
    result = verify_label(app, LABEL)
    assert result["overall"] == PASS
    assert len(result["fields"]) == 5  # four fields + warning


def test_verify_label_skips_empty_fields():
    result = verify_label({"brand_name": "Old Tom Distillery"}, LABEL)
    assert [f["field"] for f in result["fields"]] == ["brand_name", "government_warning"]


def test_verify_label_worst_status_wins():
    result = verify_label({"brand_name": "Old Tom Distillery", "alcohol_content": "40%"}, LABEL)
    assert result["overall"] == FAIL


def test_warning_check_can_be_disabled():
    result = verify_label({"brand_name": "Old Tom Distillery", "check_warning": "false"}, "Old Tom Distillery")
    assert result["overall"] == PASS
