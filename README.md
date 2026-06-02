# Data Curation Pipeline

Extract and curate text from **PDF files** and **web URLs** (including Wikipedia) for the 22 scheduled languages of India. The pipeline:

1. Extracts main body text (not image OCR on web; PDFs use text layer or optional OCR).
2. Splits text into paragraphs and keeps fragments that match your target language.
3. Merges fragments into JSONL/CSV rows where every `paragraph` has **at least** `--min-words` words.

---

## Setup

### 1. Create a virtual environment

```bash
cd /path/to/data_curation
python3 -m venv .venv
source .venv/bin/activate   # Linux/macOS
# .venv\Scripts\activate  # Windows
```

### 2. Install Python dependencies

```bash
pip install -r requirements.txt
```

### 3. Optional: Tesseract (recommended for PDF OCR)

For scanned or CorelDRAW PDFs (no text layer):

```bash
# Ubuntu/Debian
sudo apt install tesseract-ocr tesseract-ocr-hin tesseract-ocr-kan tesseract-ocr-tam tesseract-ocr-tel

# Or use EasyOCR only (slower, no system install):
pip install easyocr
```

On first run, the **fastText** language model (`lid.176.bin`, ~126 MB) is downloaded to `~/.cache/data_curation/models/`.

---

## Quick start

```bash
source .venv/bin/activate

python -m curation \
  --url "https://ta.wikipedia.org/wiki/இந்தியா" \
  --target-lang ta \
  --output curated_ta.jsonl
```

---

## Output format

Each line in the JSONL file is one record:

```json
{
  "source": "https://ta.wikipedia.org/wiki/இந்தியா",
  "paragraph": "… merged Tamil text (≥ min-words) …",
  "language_code": "ta",
  "confidence": 0.98,
  "detection_method": "fasttext"
}
```

| Field | Description |
|-------|-------------|
| `source` | PDF path or URL the text came from |
| `paragraph` | Curated text (packed to meet `--min-words`) |
| `language_code` | Detected / target language code |
| `confidence` | Language or script-match score (0–1) |
| `detection_method` | `fasttext`, `script`, or `fasttext+script` |

Use `--format csv` for CSV instead of JSONL.

---

## Supported languages (`--target-lang`)

One of the 22 scheduled Indic codes:

`as`, `bn`, `brx`, `doi`, `gu`, `hi`, `kn`, `ks`, `kok`, `mai`, `ml`, `mni`, `mr`, `ne`, `or`, `pa`, `sa`, `sat`, `sd`, `ta`, `te`, `ur`

**Note:** `brx`, `doi`, `kok`, `mai`, `mni`, `sat` are not in fastText `lid.176`; the pipeline uses **Unicode script** detection for those instead. Manipuri (`mni`) is matched against **Meetei Mayek** (Meitei script), not Bengali.

---

## CLI reference

### Required

| Argument | Description |
|----------|-------------|
| `--target-lang CODE` | Language to keep (see list above). |
| `--output PATH` | Output file path (e.g. `curated_ta.jsonl`). |

You must also provide at least one source:

| Argument | Description |
|----------|-------------|
| `--pdf PATH` | PDF file or directory (repeatable: `--pdf a.pdf --pdf dir/`). |
| `--url URL` | Web URL (repeatable). |
| `--urls-file FILE` | Text file with one URL per line (`#` comments ignored). |

### Output and text packing

| Argument | Default | Description |
|----------|---------|-------------|
| `--format` | `jsonl` | `jsonl` or `csv`. |
| `--min-words` | `500` | **Minimum words per output row.** Fragments are merged (across pages if needed) until every row’s `paragraph` has at least this many words. Does **not** filter text at scrape time. |

### Language filtering

| Argument | Default | Description |
|----------|---------|-------------|
| `--confidence` | `0.85` | Minimum score to keep a fragment. fastText languages: label + confidence. Also requires enough text in the expected script (e.g. Tamil script for `ta`). |
| `--model-path` | auto | Path to `lid.176.bin` (downloaded if missing). |

### Web / URL extraction

| Argument | Default | Description |
|----------|---------|-------------|
| `--url-timeout` | `30` | HTTP timeout (seconds) per URL. |

**Wikipedia** URLs use the MediaWiki API and article-body extraction (not generic page chrome).

### Wikipedia crawl (expand from seed URLs)

| Argument | Default | Description |
|----------|---------|-------------|
| `--wiki-crawl-depth` | `0` | `0` = only URLs you pass. `1` = seed + pages linked from seed. `2` = one more hop. Links come from **article body** (`p`, `li`), not site-wide nav headers. |
| `--wiki-max-pages` | `150` | Maximum Wikipedia pages to fetch in one run. Use **`0`** = no page limit. |
| `--wiki-links-per-page` | `40` | Max `/wiki/` links to follow **from each page** during crawl. Use **`0`** = **no limit** (follow all article links on that page). |
| `--wiki-discover-links` | `0` | Shorthand for **depth 1** with up to `N` links per page. Ignored if you set `--wiki-links-per-page` to something other than the default. Use `0` with `--wiki-crawl-depth 1` for unlimited links per page. |
| `--wiki-crawl-delay` | `0.5` | Pause (seconds) between Wikipedia API requests (helps avoid HTTP 429). |

**Crawl depth cheat sheet**

| Setting | What gets extracted |
|---------|---------------------|
| (none) | Only `--url` you provide |
| `--wiki-crawl-depth 1` | Seed + all followed links (one hop) |
| `--wiki-crawl-depth 2` | Seed → links → links of those pages |
| `--wiki-crawl-depth 1 --wiki-links-per-page 0` | One hop, **every** `/wiki/` link on the seed page |

**Main page vs article:** Wikipedia **main pages** for all 22 scheduled languages are recognized (see table below). They are portals with short blurbs in layout tables. For large corpora, use crawl depth ≥ 1 with a high `--wiki-max-pages`. Redirect aliases (e.g. Urdu `صفحہ اول` → `صفحۂ اول`) are followed automatically.

| Code | Main page title | Wikipedia host |
|------|-----------------|----------------|
| `as` | বেটুপাত | as.wikipedia.org |
| `bn` | প্রধান পাতা | bn.wikipedia.org |
| `brx` | *(incubator only)* | — |
| `doi` | *(incubator only)* | — |
| `gu` | મુખપૃષ્ઠ | gu.wikipedia.org |
| `hi` | मुखपृष्ठ | hi.wikipedia.org |
| `kn` | ಮುಖ್ಯ ಪುಟ | kn.wikipedia.org |
| `ks` | اَہَم صَفہٕ | ks.wikipedia.org |
| `kok` | मुखेल पान | **gom**.wikipedia.org |
| `mai` | सम्मुख पन्ना | mai.wikipedia.org |
| `ml` | പ്രധാന താൾ | ml.wikipedia.org |
| `mni` | ꯃꯔꯨꯑꯣꯏꯕ ꯂꯃꯥꯏ | mni.wikipedia.org |
| `mr` | मुख्य पान | mr.wikipedia.org |
| `ne` | मुख्य पृष्ठ | ne.wikipedia.org |
| `or` | ପ୍ରଧାନ ପୃଷ୍ଠା | or.wikipedia.org |
| `pa` | ਮੁੱਖ ਸਫ਼ਾ | pa.wikipedia.org |
| `sa` | मुख्यपृष्ठम् | sa.wikipedia.org |
| `sat` | ᱢᱩᱬᱩᱛ ᱥᱟᱦᱴᱟ | sat.wikipedia.org |
| `sd` | مُک صفحو | sd.wikipedia.org |
| `ta` | முதற் பக்கம் | ta.wikipedia.org |
| `te` | మొదటి పేజీ | te.wikipedia.org |
| `ur` | صفحۂ اول | ur.wikipedia.org |

### PDF extraction and OCR

| Argument | Default | Description |
|----------|---------|-------------|
| `--pdf-ocr` | off | Force OCR on all PDF pages. |
| `--no-auto-ocr` | off | Disable auto-OCR when the PDF has almost no text layer. |
| `--ocr-engine` | `auto` | `tesseract`, `easyocr`, or `auto` (Tesseract if installed, else EasyOCR). |
| `--ocr-lang` | inferred | Tesseract language packs, e.g. `hin+eng`, `kan+eng`, `tam+eng`. |
| `--ocr-dpi` | `200` | Render resolution for Tesseract. |
| `--ocr-max-pages` | all | Only OCR/extract the first N pages (testing). |

PDFs with a normal text layer use **PyMuPDF** (no OCR). Image-only PDFs trigger OCR automatically unless `--no-auto-ocr`.

### Debugging

| Argument | Description |
|----------|-------------|
| `-v`, `--verbose` | Debug logging (dropped paragraphs, crawl details). |

---

## Sample commands

### Tamil — single Wikipedia article

```bash
python -m curation \
  --url "https://ta.wikipedia.org/wiki/இந்தியா" \
  --target-lang ta \
  --min-words 50 \
  --output curated_tamil_india.jsonl
```

### Tamil — main page + crawl linked articles (depth 2)

```bash
python -m curation \
  --url "https://ta.wikipedia.org/wiki/முதற்_பக்கம்" \
  --target-lang ta \
  --wiki-crawl-depth 2 \
  --wiki-max-pages 150 \
  --wiki-links-per-page 40 \
  --wiki-crawl-delay 0.6 \
  --min-words 50 \
  --output curated_ta.jsonl
```

### Telugu — main page, follow **all** links (depth 1, no per-page cap)

```bash
python -m curation \
  --url "https://te.wikipedia.org/wiki/మొదటి_పేజీ" \
  --target-lang te \
  --wiki-crawl-depth 1 \
  --wiki-links-per-page 0 \
  --wiki-max-pages 200 \
  --wiki-crawl-delay 0.6 \
  --min-words 50 \
  --output curated_telugu.jsonl
```

### Kannada — main page, depth 1, up to 50 links per page (shorthand)

```bash
python -m curation \
  --url "https://kn.wikipedia.org/wiki/ಮುಖ್ಯ_ಪುಟ" \
  --target-lang kn \
  --wiki-discover-links 50 \
  --wiki-max-pages 200 \
  --min-words 500 \
  --output curated_kannada.jsonl
```

### Kannada — large crawl (~500+ output rows): all links, no page cap

Follow every link on each page, depth 2, no Wikipedia page limit. Use a lower `--min-words` (e.g. 50) so one article produces several JSONL rows; default `500` merges more text per row.

```bash
python -m curation \
  --url "https://kn.wikipedia.org/wiki/%E0%B2%AE%E0%B3%81%E0%B2%96%E0%B3%8D%E0%B2%AF_%E0%B2%AA%E0%B3%81%E0%B2%9F" \
  --target-lang kn \
  --wiki-crawl-depth 2 \
  --wiki-max-pages 0 \
  --wiki-links-per-page 0 \
  --wiki-crawl-delay 0.6 \
  --min-words 50 \
  --output curated_kannada.jsonl
```

This can take a long time. Use `--wiki-crawl-delay 3` or higher to avoid HTTP 429. Prefer a page cap first (e.g. `--wiki-max-pages 200`) before `--wiki-max-pages 0`.

### Kannada — large crawl with safer rate limiting (recommended if you see 429)

```bash
python -m curation \
  --url "https://kn.wikipedia.org/wiki/%E0%B2%AE%E0%B3%81%E0%B2%96%E0%B3%8D%E0%B2%AF_%E0%B2%AA%E0%B3%81%E0%B2%9F" \
  --target-lang kn \
  --wiki-crawl-depth 2 \
  --wiki-max-pages 300 \
  --wiki-links-per-page 0 \
  --wiki-crawl-delay 3 \
  --min-words 50 \
  --output curated_kannada.jsonl
```

### Hindi — multiple URLs from a file

```bash
cat > urls.txt << 'EOF'
https://hi.wikipedia.org/wiki/भारत
https://hi.wikipedia.org/wiki/हिन्दी
EOF

python -m curation \
  --urls-file urls.txt \
  --target-lang hi \
  --min-words 50 \
  --output curated_hi.jsonl
```

### Bodo — PDF with OCR (vector/scanned PDF)

```bash
python -m curation \
  --pdf LO-BODO.pdf \
  --target-lang brx \
  --pdf-ocr \
  --ocr-engine tesseract \
  --confidence 0.70 \
  --min-words 50 \
  --output curated_brx.jsonl
```

Test OCR on a few pages first:

```bash
python -m curation \
  --pdf LO-BODO.pdf \
  --target-lang brx \
  --pdf-ocr \
  --ocr-max-pages 5 \
  --output curated_brx_sample.jsonl
```

### PDF directory + Wikipedia seed

```bash
python -m curation \
  --pdf ./pdfs/ \
  --url "https://ta.wikipedia.org/wiki/தமிழ்" \
  --target-lang ta \
  --wiki-crawl-depth 1 \
  --wiki-links-per-page 30 \
  --output mixed_ta.jsonl
```

### CSV output, lower confidence, smaller rows

```bash
python -m curation \
  --url "https://ml.wikipedia.org/wiki/കേരളം" \
  --target-lang ml \
  --format csv \
  --confidence 0.75 \
  --min-words 30 \
  --output curated_ml.csv
```

### Combine seeds: article + main page (explicit URLs)

```bash
python -m curation \
  --url "https://ta.wikipedia.org/wiki/இந்தியா" \
  --url "https://ta.wikipedia.org/wiki/முதற்_பக்கம்" \
  --target-lang ta \
  --wiki-crawl-depth 1 \
  --wiki-links-per-page 0 \
  --wiki-max-pages 300 \
  --output curated_ta_full.jsonl
```

---

## Project layout

```
data_curation/
├── curation/
│   ├── __main__.py          # CLI entry point
│   ├── pipeline.py          # Orchestration
│   ├── config.py            # Language codes and defaults
│   ├── extractors/
│   │   ├── pdf.py           # PDF text layer + OCR
│   │   ├── web.py           # URLs (routes Wikipedia to API)
│   │   └── wikipedia.py     # Wikipedia API + crawl
│   ├── processing/
│   │   ├── paragraphs.py    # Split text into fragments
│   │   ├── language.py      # fastText + script filter
│   │   └── pack.py          # Merge into min-word rows
│   └── io/
│       └── writer.py        # JSONL / CSV export
└── requirements.txt
```

---

## Troubleshooting

| Problem | What to try |
|---------|-------------|
| Very few JSONL rows from a **main page** | Use `--wiki-crawl-depth 1` (or `2`) and raise `--wiki-max-pages`. Or use a full **article** URL. |
| `HTTP Error 429` from Wikipedia | Use `--wiki-crawl-delay 3` (or `4`), avoid `--wiki-max-pages 0` with depth 2 until a smaller test succeeds. HTML is cached so each page is only fetched once. Wait 10–30 min and retry if blocked. |
| PDF returns 0 paragraphs | PDF may be image-only; use `--pdf-ocr` or install Tesseract. |
| `brx` / low-resource language drops text | Lower `--confidence` (e.g. `0.70`); Bodo uses script detection, not fastText `brx`. |
| NumPy / fastText error | Ensure `numpy<2.0` (`pip install 'numpy<2.0'`). |
| Mixed English in Tamil rows | Script filter should drop most; try `--confidence 0.90` or use article URLs, not language-footer portal pages. |

---

## Help

```bash
python -m curation --help
```


```
script to check if paragraphs length is >= 500 words
python3 -c "                  18:55:17
                                       import json
                                       from pathlib import Path
                                       rows = [json.loads(l) for l in Path('c
urated_tamil.jsonl').read_text().splitlines() if l.strip()]
                                       wc = [len(r['paragraph'].split()) for 
r in rows]
                                       short = [w for w in wc if w < 500]
                                       print(f'rows={len(rows)}, under_500={l
en(short)}, min={min(wc)}')
                                       "
```