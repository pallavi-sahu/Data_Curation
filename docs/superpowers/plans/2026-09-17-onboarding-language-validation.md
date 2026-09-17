# Onboarding Language Validation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make validation, TTS review splitting, filename inference, and OCR defaults correct for Hindi, Bengali, Kashmiri, Konkani, Dogri, Sanskrit, and Nepali using the dataset-primary scripts and language-preferred sentence punctuation.

**Architecture:** Keep `LANGUAGE_SCRIPT` as the language-to-script map. Extend Unicode ranges in `curation.processing.script`. Prefer danda vs Arabic full stop in `validation.cleaning.default_sentence_terminator`. Make `validation.tokenization.split_sentences` the only sentence splitter used by TTS review (and keep `filter_min_words` in sync). Add the seven language names to both `FILENAME_TO_LANG` copies. Align Kashmiri/Konkani/Sanskrit/Nepali OCR lookups without changing CLI or output schemas.

**Tech Stack:** Python 3, existing `curation` / `validation` packages, `pytest` (add if missing).

## Global Constraints

- Dataset-primary scripts only: `hi`/`kok`/`doi`/`sa`/`ne` Devanagari, `bn` Bengali, `ks` Perso-Arabic.
- Do not add Devanagari Kashmiri or Kannada/Roman Konkani.
- Do not add these languages to `HIGH_RESOURCE_LANGUAGES`.
- Do not rewrite dataset JSONL/CSV files.
- Preserve existing recognized terminal punctuation; only append a terminator when missing.
- Arabic semicolon `؛` (U+061B) is not a sentence terminator.
- No CLI or output-schema changes.

**Files:**

- Create: `tests/test_onboarding_language_validation.py`
- Modify: `curation/processing/script.py`
- Modify: `validation/tokenization.py`
- Modify: `validation/cleaning.py`
- Modify: `scripts/clean_tts_new_jsonl.py`
- Modify: `scripts/filter_min_words.py` (duplicate splitter must drop `؛`)
- Modify: `validation/config.py`
- Modify: `scripts/export_phase_one_dataset.py`
- Modify: `curation/config.py`
- Modify: `requirements.txt` (add `pytest` if absent)

---

### Task 1: Extended script ranges

**Files:**
- Create: `tests/test_onboarding_language_validation.py`
- Modify: `curation/processing/script.py`
- Modify: `requirements.txt`

**Interfaces:**
- Consumes: `LANGUAGE_SCRIPT` mappings already in `curation/config.py`
- Produces: `SCRIPT_RANGES["devanagari"]` includes Devanagari Extended (`0xA8E0–0xA8FF`) and Vedic Extensions (`0x1CD0–0x1CFF`); `SCRIPT_RANGES["arabic"]` includes Arabic Extended-B (`0x0870–0x089F`) and Arabic Extended-A (`0x08A0–0x08FF`); `script_character_ratio()` and `char_in_script()` use those ranges

- [ ] **Step 1: Add pytest if missing**

`requirements.txt` currently has no pytest. Append:

```
pytest>=8.0
```

Install: `python3 -m pip install pytest`

- [ ] **Step 2: Write the failing tests**

Create `tests/test_onboarding_language_validation.py`:

```python
from pathlib import Path

from curation.config import LANGUAGE_SCRIPT, OCR_EASYOCR_LANG, OCR_TESSERACT_LANG
from curation.processing.script import script_character_ratio
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
    text = "अग्निम्" + "\u1CD0" + "क" + "\uA8E0"
    assert script_character_ratio(text, "devanagari") == 1.0


def test_kashmiri_arabic_extended_count_as_arabic():
    text = "کٲشُر" + "\u08A0"
    assert script_character_ratio(text, "arabic") == 1.0
```

Keep later tests in this same file as subsequent tasks add them; for this task only the four tests above need to exist and fail on the Sanskrit/Kashmiri cases.

- [ ] **Step 3: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_onboarding_language_validation.py::test_sanskrit_vedic_and_extended_count_as_devanagari tests/test_onboarding_language_validation.py::test_kashmiri_arabic_extended_count_as_arabic -v`

Expected: FAIL because `\u1CD0`, `\uA8E0`, and `\u08A0` are outside current `SCRIPT_RANGES`.

- [ ] **Step 4: Extend Unicode ranges**

In `curation/processing/script.py`, replace the `devanagari` and `arabic` entries:

```python
SCRIPT_RANGES: dict[str, list[tuple[int, int]]] = {
    "devanagari": [(0x0900, 0x097F), (0xA8E0, 0xA8FF), (0x1CD0, 0x1CFF)],
    "bengali": [(0x0980, 0x09FF)],
    "gurmukhi": [(0x0A00, 0x0A7F)],
    "gujarati": [(0x0A80, 0x0AFF)],
    "oriya": [(0x0B00, 0x0B7F)],
    "tamil": [(0x0B80, 0x0BFF)],
    "telugu": [(0x0C00, 0x0C7F)],
    "kannada": [(0x0C80, 0x0CFF)],
    "malayalam": [(0x0D00, 0x0D7F)],
    "ol_chiki": [(0x1C50, 0x1C7F)],
    "meetei_mayek": [(0xABC0, 0xABFF), (0xAA00, 0xAA5F)],
    "arabic": [
        (0x0600, 0x06FF),
        (0x0750, 0x077F),
        (0x0870, 0x089F),
        (0x08A0, 0x08FF),
    ],
}
```

Leave other scripts unchanged.

- [ ] **Step 5: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_onboarding_language_validation.py::test_primary_scripts_for_onboarding_languages tests/test_onboarding_language_validation.py::test_bengali_script_ratio_is_high tests/test_onboarding_language_validation.py::test_sanskrit_vedic_and_extended_count_as_devanagari tests/test_onboarding_language_validation.py::test_kashmiri_arabic_extended_count_as_arabic -v`

Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add requirements.txt tests/test_onboarding_language_validation.py curation/processing/script.py
git commit -m "$(cat <<'EOF'
Add Devanagari and Arabic extended ranges for onboarding languages.

EOF
)"
```

---

### Task 2: Preferred terminators and sentence boundaries

**Files:**
- Modify: `tests/test_onboarding_language_validation.py`
- Modify: `validation/cleaning.py`
- Modify: `validation/tokenization.py`
- Modify: `scripts/filter_min_words.py`

**Interfaces:**
- Consumes: `split_sentences`, `has_terminal_punctuation`
- Produces: `default_sentence_terminator("ks") -> "\u06D4"`; `default_sentence_terminator` for `hi`/`bn`/`kok`/`doi`/`sa`/`ne` -> `"\u0964"`; unknown codes -> `"."`; `SENTENCE_TERMINATORS` without U+061B; `split_sentences` splits on `۔` and `؟` but not `؛`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_onboarding_language_validation.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_onboarding_language_validation.py::test_preferred_terminators_for_onboarding_languages tests/test_onboarding_language_validation.py::test_missing_punctuation_gets_language_terminator tests/test_onboarding_language_validation.py::test_kashmiri_splits_on_arabic_stop_and_question_not_semicolon -v`

Expected: FAIL because `bn`/`doi`/`kok` currently return `"."`, and `split_sentences` currently treats `؛` as a terminator.

- [ ] **Step 3: Fix terminator lookup**

In `validation/cleaning.py`, add `"bn"`, `"doi"`, and `"kok"` to `_INDIC_LANGS`. Leave `_ARABIC_STOP_LANGS = frozenset({"ur", "sd", "ks"})` first in `default_sentence_terminator` so Kashmiri still gets `۔`.

```python
_INDIC_LANGS = frozenset(
    {
        "as",
        "bn",
        "brx",
        "doi",
        "gu",
        "hi",
        "kn",
        "ks",
        "kok",
        "mai",
        "ml",
        "mni",
        "mr",
        "ne",
        "or",
        "pa",
        "sa",
        "sat",
        "sd",
        "ta",
        "te",
        "ur",
    }
)
```

- [ ] **Step 4: Stop treating Arabic semicolon as a sentence end**

In `validation/tokenization.py`:

```python
SENTENCE_TERMINATORS = frozenset(".\u0964\u0965\u06D4\u061F\u2026!?")

SENTENCE_TERMINATOR_RUN = re.compile(
    r"(?:"
    r"[\u0964\u0965\u06D4\u061F\u2026!?]+"
    r"|(?<!\d)\.(?!\d)"
    r")"
    r"[\s\u200B\u200C\u200D]*"
)
```

Update the comment above `SENTENCE_TERMINATORS` so it lists `۔` and `؟` but not `؛`.

In `scripts/filter_min_words.py`, delete the local `SENTENCE_TERMINATOR_RUN` copy and local `split_sentences`. Import `split_sentences` from `validation.tokenization` so word-count splitting matches validation.

If `TOKEN_PATTERN` in `filter_min_words.py` is still used, leave it. Only the sentence splitter must be shared.

- [ ] **Step 5: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_onboarding_language_validation.py::test_preferred_terminators_for_onboarding_languages tests/test_onboarding_language_validation.py::test_missing_punctuation_gets_language_terminator tests/test_onboarding_language_validation.py::test_existing_terminal_punctuation_is_preserved tests/test_onboarding_language_validation.py::test_kashmiri_splits_on_arabic_stop_and_question_not_semicolon -v`

Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add tests/test_onboarding_language_validation.py validation/cleaning.py validation/tokenization.py scripts/filter_min_words.py
git commit -m "$(cat <<'EOF'
Fix Indic terminal punctuation defaults and stop splitting on Arabic semicolon.

EOF
)"
```

---

### Task 3: Shared TTS review sentence splitter

**Files:**
- Modify: `tests/test_onboarding_language_validation.py`
- Modify: `scripts/clean_tts_new_jsonl.py`

**Interfaces:**
- Consumes: `validation.tokenization.split_sentences`
- Produces: `scripts.clean_tts_new_jsonl.split_sentences` is the shared function (import/alias), not a regex that requires whitespace after `।` and ignores `۔`/`؟`

- [ ] **Step 1: Write the failing tests**

Append:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_onboarding_language_validation.py::test_tts_review_splitter_matches_validation_splitter tests/test_onboarding_language_validation.py::test_tts_review_splits_kashmiri_without_requiring_space -v`

Expected: FAIL because `_SENTENCE_SPLIT` only splits on `।॥.!?` followed by whitespace.

- [ ] **Step 3: Reuse the shared splitter**

In `scripts/clean_tts_new_jsonl.py`:

1. After the existing `_tts = _load_tts_cleanup()` block, import:

```python
from validation.tokenization import split_sentences as split_sentences
```

2. Delete `_SENTENCE_SPLIT` and the local `def split_sentences`.

3. Keep `flagged_sentences` calling `split_sentences(text)`.

If `re` is unused after removing `_SENTENCE_SPLIT`, drop the unused import.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_onboarding_language_validation.py::test_tts_review_splitter_matches_validation_splitter tests/test_onboarding_language_validation.py::test_tts_review_splits_kashmiri_without_requiring_space -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_onboarding_language_validation.py scripts/clean_tts_new_jsonl.py
git commit -m "$(cat <<'EOF'
Use shared sentence splitting for TTS numeric-review CSVs.

EOF
)"
```

---

### Task 4: Filename language inference

**Files:**
- Modify: `tests/test_onboarding_language_validation.py`
- Modify: `validation/config.py`
- Modify: `scripts/export_phase_one_dataset.py`

**Interfaces:**
- Consumes: `infer_language_from_filename`, `FILENAME_TO_LANG`
- Produces: stems `hindi`/`bengali`/`kashmiri`/`konkani`/`dogri`/`sanskrit`/`nepali` map to `hi`/`bn`/`ks`/`kok`/`doi`/`sa`/`ne` in both validation and export maps; `HIGH_RESOURCE_LANGUAGES` unchanged

- [ ] **Step 1: Write the failing tests**

Append:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_onboarding_language_validation.py::test_filename_inference_for_onboarding_languages -v`

Expected: FAIL with `KeyError` for `hindi`.

- [ ] **Step 3: Add filename mappings**

In `validation/config.py` `FILENAME_TO_LANG`, add (keep existing keys; do not touch `HIGH_RESOURCE_LANGUAGES`):

```python
    "bengali": "bn",
    "dogri": "doi",
    "hindi": "hi",
    "kashmiri": "ks",
    "konkani": "kok",
    "nepali": "ne",
    "sanskrit": "sa",
```

In `scripts/export_phase_one_dataset.py` `FILENAME_TO_LANG`, add the same seven entries. `LANG_TO_LOCALE` is derived from that dict, so it updates automatically.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_onboarding_language_validation.py::test_filename_inference_for_onboarding_languages -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_onboarding_language_validation.py validation/config.py scripts/export_phase_one_dataset.py
git commit -m "$(cat <<'EOF'
Map onboarding language filenames to ISO codes for validation and export.

EOF
)"
```

---

### Task 5: OCR defaults

**Files:**
- Modify: `tests/test_onboarding_language_validation.py`
- Modify: `curation/config.py`

**Interfaces:**
- Consumes: `OCR_TESSERACT_LANG`, `OCR_EASYOCR_LANG`
- Produces: `ks` Tesseract `urd+eng`; `ks` EasyOCR `["ur", "en"]`; `kok` EasyOCR `["gom", "en"]`; `sa` EasyOCR `["sa", "en"]`; `ne` EasyOCR `["ne", "en"]`; `doi` EasyOCR remains `["hi", "en"]`

- [ ] **Step 1: Write the failing tests**

Append:

```python
def test_ocr_defaults_match_selected_scripts():
    assert OCR_TESSERACT_LANG["ks"] == "urd+eng"
    assert OCR_EASYOCR_LANG["ks"] == ["ur", "en"]
    assert OCR_EASYOCR_LANG["kok"] == ["gom", "en"]
    assert OCR_EASYOCR_LANG["sa"] == ["sa", "en"]
    assert OCR_EASYOCR_LANG["ne"] == ["ne", "en"]
    assert OCR_EASYOCR_LANG["doi"] == ["hi", "en"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_onboarding_language_validation.py::test_ocr_defaults_match_selected_scripts -v`

Expected: FAIL (`ks` Tesseract is `hin+eng`; EasyOCR has no `ks` key and uses Hindi for `kok`/`sa`/`ne`).

- [ ] **Step 3: Update OCR maps**

In `curation/config.py`:

- Set `OCR_TESSERACT_LANG["ks"] = "urd+eng"`.
- Add `"ks": ["ur", "en"]` to `OCR_EASYOCR_LANG`.
- Change `"kok": ["gom", "en"]`, `"sa": ["sa", "en"]`, `"ne": ["ne", "en"]`.
- Leave `"doi": ["hi", "en"]`.

Do not change Tesseract packs for Konkani/Sanskrit/Nepali in this task (spec only changes Kashmiri Tesseract plus EasyOCR codes).

- [ ] **Step 4: Run the full onboarding test file**

Run: `python3 -m pytest tests/test_onboarding_language_validation.py -v`

Expected: all tests PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_onboarding_language_validation.py curation/config.py
git commit -m "$(cat <<'EOF'
Align Kashmiri and related OCR defaults with dataset-primary scripts.

EOF
)"
```

---

## Self-review

1. **Spec coverage:** Script ranges (Task 1), preferred/preserved punctuation and `؛` (Task 2), shared TTS splitter (Task 3), filename inference without `HIGH_RESOURCE_LANGUAGES` (Task 4), OCR (Task 5). Dataset rewrite is explicitly out of scope.
2. **Placeholder scan:** None.
3. **Type consistency:** `split_sentences(text: str) -> list[str]`, `default_sentence_terminator(language_code: str) -> str`, `FILENAME_TO_LANG: dict[str, str]`.
