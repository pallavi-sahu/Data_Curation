# Onboarding Language Validation and Cleanup Design

## Goal

Make the validation and TTS cleanup pipeline correctly handle Hindi, Bengali,
Kashmiri, Konkani, Dogri, Sanskrit, and Nepali using the scripts selected for
the onboarding datasets and language-appropriate sentence punctuation.

## Scope

The pipeline will use these language/script pairs:

- `hi` (Hindi): Devanagari
- `bn` (Bengali): Bengali
- `ks` (Kashmiri): Perso-Arabic
- `kok` (Konkani): Devanagari
- `doi` (Dogri): Devanagari
- `sa` (Sanskrit): Devanagari, including Vedic and Devanagari extensions
- `ne` (Nepali): Devanagari

The change applies to shared curation script checks, validation cleanup,
punctuation validation, and sentence extraction in
`scripts/clean_tts_new_jsonl.py`. It also covers filename-based language
inference and OCR defaults used during curation. It will not rewrite existing
datasets or add support for alternate Kashmiri or Konkani scripts.

## Design

### Script validation

Keep `LANGUAGE_SCRIPT` as the source of language-to-primary-script mappings.
Extend the Unicode ranges used by script checks:

- Devanagari: base Devanagari, Devanagari Extended, and Vedic Extensions.
- Arabic: base Arabic, Arabic Supplement, Arabic Extended-A, and Arabic
  Extended-B.

The existing mappings already select Bengali for `bn`, Arabic for `ks`, and
Devanagari for the other five languages. The extended ranges prevent valid
Kashmiri and Sanskrit combining marks or letters from lowering script ratios
or being removed as foreign-script content.

### Language inference

Add `hindi`, `bengali`, `kashmiri`, `konkani`, `dogri`, `sanskrit`, and
`nepali` to filename-to-language mappings with codes `hi`, `bn`, `ks`, `kok`,
`doi`, `sa`, and `ne`. Apply the same additions to phase export metadata so
validation reports and exported manifests do not fall back to unknown or
language-name values.

Do not add the languages to `HIGH_RESOURCE_LANGUAGES` until they have canonical
files in `final/`; this set describes current storage, not general support.

### OCR defaults

Align OCR with the selected primary scripts:

- Kashmiri Tesseract uses `urd+eng` rather than Hindi OCR.
- Kashmiri EasyOCR uses `ur+en` as the available Perso-Arabic proxy because
  EasyOCR has no Kashmiri model.
- Konkani, Sanskrit, and Nepali EasyOCR use their supported codes `gom`, `sa`,
  and `ne` instead of the generic Hindi model.
- Dogri continues to use Hindi as the Devanagari proxy because EasyOCR has no
  Dogri model.

### Sentence punctuation

Define the preferred cleanup terminator by language:

- Kashmiri: Arabic full stop `۔`.
- Hindi, Bengali, Konkani, Dogri, Sanskrit, and Nepali: danda `।`.
- Unconfigured languages: Latin full stop `.`.

Recognition remains permissive: danda `।`, double danda `॥`, Arabic full stop
`۔`, Arabic question mark `؟`, Latin `.`, `!`, `?`, and ellipsis `…` are valid
sentence endings in input. Arabic semicolon `؛` is not a sentence ending and
must not split a sentence.

Only a missing terminator is repaired. Existing recognized punctuation is
preserved rather than normalized to the preferred mark.

### Shared sentence splitting

`validation.tokenization.split_sentences` remains the shared implementation.
`scripts/clean_tts_new_jsonl.py` will call it instead of maintaining a second,
less complete regular expression. This ensures Kashmiri review segments split
on `۔` and `؟` and that validation and TTS review agree about sentence
boundaries.

### Compatibility

No CLI arguments or output schemas change. Existing IDs, JSONL outputs, CSV
columns, and statistics remain unchanged. Existing Latin punctuation in Indic
text remains accepted.

## Error Handling

Unknown or empty language codes continue to use `.` as the cleanup fallback.
Text with no recognized sentence boundary remains one segment. Empty text
continues to return no sentences and is handled by the existing cleanup flow.

## Testing

Add focused unit coverage that verifies:

1. Preferred terminators for all seven onboarding language codes.
2. Existing recognized terminal punctuation is preserved.
3. Missing punctuation receives `۔` for Kashmiri and `।` for the other six.
4. Kashmiri text splits on `۔` and `؟`, but not on `؛`.
5. The TTS review sentence extractor follows the shared splitter.
6. Script ratios recognize Bengali, Kashmiri Arabic extensions, and Sanskrit
   Vedic/Devanagari extensions.
7. Filename inference and export metadata resolve all seven language names.
8. OCR lookup returns Perso-Arabic settings for Kashmiri and native available
   EasyOCR codes for Konkani, Sanskrit, and Nepali.
9. Existing supported-script behavior remains unchanged.

## Acceptance Criteria

- All seven languages use the selected dataset-primary script.
- Cleanup adds the correct language-preferred terminator.
- Validation and TTS review sentence boundaries are consistent.
- Valid Kashmiri and Sanskrit extended characters count toward their expected
  script.
- Filename inference recognizes all seven onboarding datasets.
- OCR defaults use the selected script and the closest supported language
  model.
- Regression tests pass without modifying dataset files.
