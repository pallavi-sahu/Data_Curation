# Validation Pipeline

Validates curated JSONL records from `final/` (fields: `source`, `paragraph`, `language_code`).

```bash
# Deterministic checks only (default — no API key needed)
python -m validation --input final/gujarati.jsonl

# All languages in final/
python -m validation --input-dir final/

# Enable LLM validators (requires API key for chosen provider)
export VALIDATION_LLM_PROVIDER=gemini   # or: openai
export GEMINI_API_KEY=...               # if provider=gemini
# export OPENAI_API_KEY=...             # if provider=openai
python -m validation --input final/gujarati.jsonl --enable-llm
```

Output: `validation_reports/{language}_summary.json` only (per-record JSONL is off by default; use `--write-records` to enable).

The summary includes:
- **`llm_metrics`** — domain, coherence, grammar, sensitivity (distributions, means, per-record details)
- **`deterministic_metrics`** — words, links, nsw, encoding, punctuation, etc. with flagged records and `items` arrays (e.g. all NSW tokens)
- **`flags`** — same flagged issues grouped by flag name (flat view across all validators)

**Input scope:** Every validator analyzes only the JSONL `paragraph` field (`record.text`). The `source` URL is stored in the report for traceability but is never scanned for links, PII, or other metrics.


## Coverage by method

### Using LLM (`--enable-llm`)

| # | Metric | Validator | Module | What it does |
|---|--------|-----------|--------|--------------|
| 1 | **Domain** (content category) | `domain` | `metrics/domain.py` | Classifies paragraph into `education`, `public_domain`, `news`, `stories`, `information`, `biography`, `science_technology`, `legal_government`, `religion_culture`, `other`, or a custom snake_case label |
| 2 | **Coherence** | `coherence` | `metrics/coherence.py` | Scores 0–1 whether sentences read as natural, coherent prose |
| 5 | **Grammar** (error %) | `grammar` | `metrics/grammar.py` | Estimates grammatical error rate and gives examples |
| 3 | **Sensitivity** (partial) | `sensitivity` | `metrics/sensitivity.py` | Flags hate speech, violence, sexual content, etc. (score 0–1). Without `--enable-llm`, falls back to regex-only PII hints |

LLM client: `validation/llm/client.py` · Providers: **gemini** (Google) or **openai** (GPT) · Select via `VALIDATION_LLM_PROVIDER` env or `--llm-provider` · Models: `GEMINI_MODEL` / `OPENAI_MODEL` or shared `VALIDATION_LLM_MODEL`

---

### Using regex

| # | Metric | Validator | Module | What it does |
|---|--------|-----------|--------|--------------|
| 3 | **PII** | `pii` | `metrics/pii.py` | Detects email, Indian phone (+91), Aadhaar (12-digit), PAN |
| 4 | **Links** | `links` | `metrics/links.py` | Finds `http(s)://` and `www.` URLs; computes link density |
| 9 | **Abbreviations & acronyms** | `abbreviations` | `metrics/abbreviations.py` | Latin ALL-CAPS acronyms (e.g. `UTC`, `U.S.A.`); counts bracket footnote markers `[` `]` |
| 10 | **NSW density** | `nsw` | `metrics/nsw.py` | Non-standard words: numbers, dates, times, currency, percentages, phone-like strings, URLs, emails |
| 11 | **Emoji** | `emoji` | `metrics/emoji.py` | Unicode emoji blocks; ASCII kaomoji reported but only emoji triggers flags |
| 15 | **Punctuation & capitalization** | `punctuation` | `metrics/punctuation.py` | Missing terminal punctuation, Latin ALL-CAPS words, unbalanced `()` `[]` quotes, repeated `...` / `!!!` |

Shared patterns: `validation/tokenization.py`

---

### Using script / tokenization (Indic-aware)

Works across Tamil, Telugu, Gujarati, Kannada, Malayalam, Marathi, Odia, Punjabi, Urdu, Assamese, Sindhi, etc.

| # | Metric | Validator | Module | What it does |
|---|--------|-----------|--------|--------------|
| 6 | **Word count & frequencies** | `words` | `metrics/words.py` | Tokenizes by script-aware boundaries; word count, top-N frequency list |
| 7 | **Unique words** | `words` | `metrics/words.py` | Unique word count and type-token ratio |
| 8 | **Symbols & special characters** | `symbols` | `metrics/symbols.py` | Unicode category counts; special-char ratio (Indic matras excluded); format/control chars (Cf, Cc) |
| 12 | **Readability** (sentence length) | `readability` | `metrics/readability.py` | Mean/max sentence length, long-sentence count (≥60 words). Sentence splits use cross-script terminators — see [Sentence splitting](#sentence-splitting-by-language) below |

Script matching uses `curation.config.LANGUAGE_SCRIPT` + `curation.processing.script`.

#### Sentence splitting by language

Word tokenization is **script-agnostic** (works for all Indic scripts). Sentence boundaries use **punctuation**, not language-specific NLP:

| Script / languages | Primary sentence marks | Also recognised |
|--------------------|------------------------|-----------------|
| Devanagari — Hindi, Marathi, Nepali, Sanskrit, Maithili, Konkani, Bodo, Dogri | `।` (danda), `॥` (double danda) | `.` `!` `?` |
| Gujarati, Bengali, Assamese, Odia, Tamil, Telugu, Kannada, Malayalam, Punjabi | `।` in Wikipedia/digital text; often `.` in web scrapes | `.` `!` `?` `…` |
| Arabic — Urdu, Sindhi, Kashmiri | `۔` (Arabic full stop), `؟` (Arabic question mark) | `.` `!` `?` |
| Ol Chiki (Santali), Meetei Mayek (Manipuri) | `.` `!` `?` (Latin punct in digital text) | `।` if present |

All terminators are defined in `validation/tokenization.py` as `SENTENCE_TERMINATORS`. Splits do **not** require whitespace after punctuation (Indic text often omits it).

**Flesch-Kincaid** (textstat) remains English/Latin-only — skipped when `latin_character_ratio < 0.50`. Sentence **length** metrics work for every language above.

---

### Using ftfy + Unicode normalization (deterministic)

| # | Metric | Validator | Module | What it does |
|---|--------|-----------|--------|--------------|
| 14 | **Encoding / mojibake / HTML entities** | `encoding` | `metrics/encoding.py` | Mojibake markers (`â€™`, `�`), `&nbsp;` / `&amp;`, smart quotes, NFC normalization check, `ftfy.fix_text` diff |

Requires: `pip install ftfy` (from `requirements-validation.txt`)

---

### Using textstat (deterministic, English-centric fallback)

| # | Metric | Validator | Module | What it does |
|---|--------|-----------|--------|--------------|
| 12 | **Readability** (Flesch-Kincaid) | `readability` | `metrics/readability.py` | Flesch-Kincaid grade + reading ease — only when `latin_character_ratio ≥ 0.50` |

Requires: `pip install textstat`

---

### Scraping artifacts & formatting integrity (#13)

**Status: partially covered** — no single dedicated validator; split across:

| Signal | Covered by | Flags |
|--------|------------|-------|
| Mojibake, HTML entities, nbsp | `encoding` | `mojibake_detected`, `html_entities_present`, `non_breaking_spaces` |
| Format/control characters | `symbols` | `contains_format_characters` |
| Unbalanced brackets/quotes, repeated punctuation | `punctuation` | `unbalanced_brackets_or_quotes`, `repeated_punctuation` |
| High special-char noise | `symbols` | `high_special_char_ratio` |
| Raw URLs in body text | `links` | `contains_raw_urls`, `high_link_density` |

**Not yet implemented:** dedicated checks for boilerplate/nav/footer text, duplicate paragraphs, table-of-contents leakage, citation-only blocks.

---

## Quick reference — all validators

| Validator key | Method | Requires `--enable-llm` |
|---------------|--------|----------------------|
| `domain` | LLM | Yes |
| `coherence` | LLM | Yes |
| `grammar` | LLM | Yes |
| `sensitivity` | LLM + regex | LLM optional |
| `pii` | Regex | No |
| `links` | Regex | No |
| `words` | Script/tokenization | No |
| `symbols` | Script/Unicode | No |
| `abbreviations` | Regex | No |
| `nsw` | Regex | No |
| `encoding` | ftfy + Unicode | No |
| `punctuation` | Regex | No |
| `emoji` | Regex/Unicode | No |
| `readability` | Script + textstat | No |

---

## Original metrics checklist

| # | Metric | Covered? |
|---|--------|----------|
| 1 | Domain | ✅ LLM |
| 2 | Coherence | ✅ LLM |
| 3 | Sensitivity & PII | ✅ LLM + regex (PII always regex) |
| 4 | Links | ✅ Regex |
| 5 | Grammar | ✅ LLM |
| 6 | Word count & frequencies | ✅ Script |
| 7 | Unique words | ✅ Script |
| 8 | Symbols & special characters | ✅ Script/Unicode |
| 9 | Abbreviations & acronyms | ✅ Regex |
| 10 | NSW density | ✅ Regex |
| 11 | Emoji / emoticons / kaomoji | ✅ Regex/Unicode |
| 12 | Readability & sentence complexity | ✅ Script + textstat |
| 13 | Scraping artifacts & formatting | ⚠️ Partial (encoding + punctuation + symbols + links) |
| 14 | Encoding / mojibake / HTML entities | ✅ ftfy + Unicode |
| 15 | Punctuation & capitalization | ✅ Regex |

---

## Output parameters (`{language}_summary.json`)

Every field in the default validation output file. Optional `{language}_validation.jsonl` (with `--write-records`) uses the same metric names under `metrics.<name>.data`.

| Parameter | Description |
|-----------|-------------|
| `total_records` | Number of JSONL rows validated |
| `passed` | Records with zero flags across all validators |
| `failed` | Records with at least one flag |
| `pass_rate` | Fraction of records that passed (`passed / total_records`) |
| `llm_provider` | LLM backend used: `gemini` or `openai` |
| `llm_metrics` | Aggregated results from LLM validators |
| `llm_metrics.domain.method` | Always `llm` |
| `llm_metrics.domain.enabled` | Whether `--enable-llm` was used |
| `llm_metrics.domain.evaluated_count` | Records successfully classified by LLM |
| `llm_metrics.domain.failed_count` | Records where LLM classification failed |
| `llm_metrics.domain.message` | Shown when LLM not enabled (`Not run — use --enable-llm`) |
| `llm_metrics.domain.error` | First LLM error message (bad key, model not found, etc.) |
| `llm_metrics.domain.category_distribution` | Count of records per content category |
| `llm_metrics.domain.custom_categories` | LLM-proposed categories outside the predefined list |
| `llm_metrics.domain.mean_confidence` | Average classification confidence |
| `llm_metrics.domain.records` | Per-record domain results |
| `llm_metrics.domain.records[].line_number` | Input JSONL line number (1-based) |
| `llm_metrics.domain.records[].source` | Source URL from curated record |
| `llm_metrics.domain.records[].category` | Primary content category |
| `llm_metrics.domain.records[].is_custom` | Whether category is outside predefined taxonomy |
| `llm_metrics.domain.records[].confidence` | LLM confidence 0.0–1.0 |
| `llm_metrics.domain.records[].secondary` | Additional category labels |
| `llm_metrics.domain.records[].rationale` | One-sentence LLM explanation |
| `llm_metrics.domain.records[].flags` | Flags for this record (`low_domain_confidence`, `uncategorized_content`) |
| `llm_metrics.coherence.mean_score` | Average coherence score across evaluated records |
| `llm_metrics.coherence.min_score` | Lowest coherence score |
| `llm_metrics.coherence.max_score` | Highest coherence score |
| `llm_metrics.coherence.records` | Per-record coherence results |
| `llm_metrics.coherence.records[].score` | Coherence score 0.0–1.0 |
| `llm_metrics.coherence.records[].issues` | Specific coherence problems identified |
| `llm_metrics.coherence.records[].summary` | Brief LLM quality summary |
| `llm_metrics.coherence.records[].flags` | e.g. `low_coherence` |
| `llm_metrics.grammar.mean_error_rate` | Average grammatical error rate |
| `llm_metrics.grammar.max_error_rate` | Highest error rate seen |
| `llm_metrics.grammar.examples` | Deduplicated grammar error examples (up to 50) |
| `llm_metrics.grammar.records` | Per-record grammar results |
| `llm_metrics.grammar.records[].error_rate` | Estimated fraction of sentences with errors |
| `llm_metrics.grammar.records[].error_count` | Approximate error count |
| `llm_metrics.grammar.records[].examples` | Example problematic phrases |
| `llm_metrics.grammar.records[].summary` | Brief LLM summary |
| `llm_metrics.grammar.records[].flags` | e.g. `high_grammar_error_rate` |
| `llm_metrics.sensitivity.method` | `regex_only` or `llm+regex` |
| `llm_metrics.sensitivity.evaluated_count` | Total records checked |
| `llm_metrics.sensitivity.flagged_count` | Records with any sensitivity flag |
| `llm_metrics.sensitivity.llm_evaluated_count` | Records scored by LLM |
| `llm_metrics.sensitivity.llm_failed_count` | Records where LLM call failed |
| `llm_metrics.sensitivity.regex_flagged_count` | Records flagged by regex PII only |
| `llm_metrics.sensitivity.mean_score` | Mean LLM sensitivity score (flagged records only) |
| `llm_metrics.sensitivity.max_score` | Max LLM sensitivity score (flagged records only) |
| `llm_metrics.sensitivity.category_distribution` | Count of LLM sensitivity categories |
| `llm_metrics.sensitivity.regex_hit_totals` | Corpus-wide regex hits (`email`, `phone`) |
| `llm_metrics.sensitivity.records` | Flagged records only |
| `llm_metrics.sensitivity.records[].score` | LLM sensitivity 0.0–1.0 (`null` if regex-only) |
| `llm_metrics.sensitivity.records[].categories` | LLM sensitivity labels |
| `llm_metrics.sensitivity.records[].regex_hits` | Per-record email/phone match counts |
| `llm_metrics.sensitivity.records[].summary` | LLM explanation (`null` if regex-only) |
| `llm_metrics.sensitivity.records[].flags` | `sensitive_content`, `potential_pii_in_text` |
| `deterministic_metrics` | Aggregated regex/script validator results |
| `deterministic_metrics.<validator>.evaluated_count` | Records where validator ran |
| `deterministic_metrics.<validator>.flagged_count` | Total flag occurrences for this validator |
| `deterministic_metrics.<validator>.flags` | Map of flag name → details |
| `deterministic_metrics.<validator>.flags.<flag>.count` | Records with this flag |
| `deterministic_metrics.<validator>.flags.<flag>.records` | Per-record detail for this flag |
| `deterministic_metrics.<validator>.flags.<flag>.items` | Deduplicated list of flagged tokens/URLs/entities (when applicable) |
| `flags` | Flat index of all flags across every validator |
| `flags.<flag_name>.count` | Total records with this flag |
| `flags.<flag_name>.records` | Per-record details (same shape as in `deterministic_metrics`) |
| `flags.<flag_name>.items` | Deduplicated corpus-wide flagged items (when applicable) |
| `empty_text` | Flag: paragraph has zero tokens |
| `low_unique_word_ratio` | Flag: too few unique words relative to total |
| `low_script_match_ratio` | Flag: text doesn't match expected script for language |
| `contains_raw_urls` | Flag: URLs found in paragraph text |
| `high_link_density` | Flag: too many URLs relative to word count |
| `high_special_char_ratio` | Flag: too many special characters |
| `contains_format_characters` | Flag: Unicode format/control characters present |
| `high_abbreviation_density` | Flag: too many ALL-CAPS acronyms |
| `high_nsw_density` | Flag: too many numbers, dates, URLs, etc. |
| `mojibake_detected` | Flag: encoding corruption markers found |
| `html_entities_present` | Flag: HTML entities like `&nbsp;` found |
| `non_breaking_spaces` | Flag: non-breaking space characters found |
| `ftfy_would_modify` | Flag: text would change after ftfy repair |
| `needs_unicode_normalization` | Flag: text is not NFC-normalized |
| `missing_terminal_punctuation` | Flag: too many sentences lack terminal punctuation |
| `high_all_caps_ratio` | Flag: too many ALL-CAPS Latin words |
| `unbalanced_brackets_or_quotes` | Flag: unmatched brackets or quotes |
| `repeated_punctuation` | Flag: repeated `...`, `!!!`, etc. |
| `contains_emoji` | Flag: Unicode emoji present |
| `high_mean_sentence_length` | Flag: average sentence too long |
| `too_many_long_sentences` | Flag: too many sentences ≥ 60 words |
| `pii_detected` | Flag: email, phone, Aadhaar, or PAN found |
| `low_domain_confidence` | Flag: LLM domain confidence below threshold |
| `uncategorized_content` | Flag: LLM returned category `other` |
| `low_coherence` | Flag: LLM coherence score below threshold |
| `high_grammar_error_rate` | Flag: LLM grammar error rate above threshold |
| `sensitive_content` | Flag: LLM sensitivity score above threshold |
| `potential_pii_in_text` | Flag: email or phone regex match in paragraph |
