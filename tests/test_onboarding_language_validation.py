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


def test_preferred_terminators_for_onboarding_languages():
    assert default_sentence_terminator("ks") == "\u06D4"
    for code in ("hi", "bn", "kok", "doi", "sa", "ne"):
        assert default_sentence_terminator(code) == "\u0964"
    assert default_sentence_terminator("") == "."
    assert default_sentence_terminator("xx") == "."


def test_missing_punctuation_gets_language_terminator():
    hindi, changed_hi = fix_terminal_punctuation("यह एक वाक्य है", "hi")
    assert changed_hi
    assert hindi.endswith("\u0964")
    kashmiri, changed_ks = fix_terminal_punctuation("یہ جملہ ہے", "ks")
    assert changed_ks
    assert kashmiri.endswith("\u06D4")
    bengali, changed_bn = fix_terminal_punctuation("এটি একটি বাক্য", "bn")
    assert changed_bn
    assert bengali.endswith("\u0964")


def test_existing_terminal_punctuation_is_preserved():
    text = "यह पहले से पूरा है।"
    fixed, changed = fix_terminal_punctuation(text, "hi")
    assert changed is False
    assert fixed.endswith("।")
    latin = "This already ends."
    fixed_latin, changed_latin = fix_terminal_punctuation(latin, "hi")
    assert changed_latin is False
    assert fixed_latin.endswith(".")


def test_kashmiri_splits_on_arabic_stop_and_question_not_semicolon():
    text = "جملہ اول۔ جملہ دوم؟ حصہ ا؛ حصہ ب۔"
    parts = split_sentences(text)
    assert len(parts) == 3
    assert parts[0].endswith("۔")
    assert parts[1].endswith("؟")
    assert "؛" in parts[2]


def _load_clean_tts_new():
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "scripts" / "clean_tts_new_jsonl.py"
    spec = importlib.util.spec_from_file_location("clean_tts_new_jsonl", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_tts_review_splitter_matches_validation_splitter():
    clean_tts_new = _load_clean_tts_new()
    text = "جملہ اول۔جملہ دوم؟ حصہ ا؛ حصہ ب।"
    assert clean_tts_new.split_sentences(text) == split_sentences(text)


def test_tts_review_splits_kashmiri_without_requiring_space():
    clean_tts_new = _load_clean_tts_new()
    parts = clean_tts_new.split_sentences("اول۔دوم؟")
    assert parts == ["اول۔", "دوم؟"]


def _load_export_phase_one():
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "scripts" / "export_phase_one_dataset.py"
    spec = importlib.util.spec_from_file_location("export_phase_one_dataset", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_filename_inference_for_onboarding_languages():
    export_phase_one = _load_export_phase_one()
    expected = {
        "hindi": "hi",
        "bengali": "bn",
        "kashmiri": "ks",
        "konkani": "kok",
        "dogri": "doi",
        "sanskrit": "sa",
        "nepali": "ne",
    }
    for name, code in expected.items():
        assert FILENAME_TO_LANG[name] == code
        assert infer_language_from_filename(Path(f"{name}.jsonl")) == code
        assert export_phase_one.FILENAME_TO_LANG[name] == code
        assert code not in HIGH_RESOURCE_LANGUAGES
