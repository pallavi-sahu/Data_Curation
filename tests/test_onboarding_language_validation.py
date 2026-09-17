from pathlib import Path

from curation.config import LANGUAGE_SCRIPT, OCR_EASYOCR_LANG, OCR_TESSERACT_LANG
from curation.processing.script import char_in_script, script_character_ratio
from validation.cleaning import default_sentence_terminator, fix_terminal_punctuation
from validation.config import FILENAME_TO_LANG, HIGH_RESOURCE_LANGUAGES
from validation.io import infer_language_from_filename
from validation.tokenization import split_sentences


def test_primary_scripts_for_onboarding_languages():
    assert LANGUAGE_SCRIPT["hi"] == "devanagari"
    assert LANGUAGE_SCRIPT["bn"] == "bengali"
    assert LANGUAGE_SCRIPT["ks"] == "arabic"
    assert LANGUAGE_SCRIPT["kok"] == "devanagari"
    assert LANGUAGE_SCRIPT["doi"] == "devanagari"
    assert LANGUAGE_SCRIPT["sa"] == "devanagari"
    assert LANGUAGE_SCRIPT["ne"] == "devanagari"


def test_bengali_script_ratio_is_high():
    text = "বাংলা ভাষায় লেখা একটি বাক্য।"
    assert script_character_ratio(text, "bengali") >= 0.85


def test_sanskrit_vedic_and_extended_count_as_devanagari():
    assert char_in_script("\u1CD0", "devanagari") is True
    assert char_in_script("\uA8E0", "devanagari") is True
    text = "अग्निम्" + "\u1CD0" + "क" + "\uA8E0"
    assert script_character_ratio(text, "devanagari") == 1.0


def test_kashmiri_arabic_extended_count_as_arabic():
    text = "کٲشُر" + "\u08A0"
    assert script_character_ratio(text, "arabic") == 1.0
