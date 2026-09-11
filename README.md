# Data Curation Pipeline

Extract and curate text from **PDF files** and **web URLs** (including Wikipedia) for the 22 scheduled languages of India. The pipeline:

1. Extracts main body text (not image OCR on web; PDFs use text layer or optional OCR).
2. Splits text into paragraphs and keeps fragments that match your target language.
3. Merges fragments into JSONL/CSV rows (per page/URL): each row has **at least** `--min-words` words from a **single** source; leftover text under that limit on one page is **not** written.

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
  "paragraph": "… merged Tamil text from one URL (≥ min-words when possible) …",
  "language_code": "ta",
  "confidence": 0.98,
  "detection_method": "fasttext"
}
```

| Field | Description |
|-------|-------------|
| `source` | PDF path, HTML path, or URL the text came from |
| `paragraph` | Curated text from one source, packed to `--min-words` when possible |
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
| `--html PATH` | HTML file or directory (repeatable: `--html a.html --html dir/`). |
| `--url URL` | Web URL (repeatable). `.html` URLs use the HTML extractor; Wikipedia uses the API. |
| `--htmls-file FILE` | Text file with one local HTML path per line (`#` comments ignored). |
| `--urls-file FILE` | Text file with one URL per line (`#` comments ignored). |

### Output and text packing

| Argument | Default | Description |
|----------|---------|-------------|
| `--format` | `jsonl` | `jsonl` or `csv`. |
| `--min-words` | `500` | **Minimum words per output row, per source.** Fragments from the same URL/PDF/HTML file are merged until a row reaches this size, then it is written. Remaining text under the limit at the end of one page is **dropped** (not merged into the next page). Does **not** filter text at scrape time. |

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

### URL crawl (expand from seed URLs — Wikipedia and other sites)

The same flags apply to **Wikipedia** (`/wiki/` links) and **other sites** (same-host links from article/main content; nav/footer stripped).

| Argument | Default | Description |
|----------|---------|-------------|
| `--wiki-crawl-depth` | `0` | `0` = only URLs you pass. `1` = seed + pages linked from seed. `2+` = more hops (e.g. `5` for news sites). Wikipedia: `/wiki/` links from article body. Other URLs: same-site links from article/main. |
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

### PDF extraction (textbooks — text layer, no images)

By default PDFs use **PyMuPDF** on the embedded **text layer** only: text blocks are read in order, **image blocks are skipped**, and pages are not rendered. This is the right mode for Unicode textbooks.

| Argument | Default | Description |
|----------|---------|-------------|
| `--pdf PATH` | — | PDF file or directory (repeatable). |
| `--ocr-max-pages` | all | Only read the first N pages (testing). |
| `--pdf-ocr` | off | Force OCR (renders every page as an image; slow). |
| `--auto-ocr` | off | OCR only if the text layer is nearly empty (&lt;15 words/page). |
| `--ocr-engine` | `auto` | `tesseract`, `easyocr`, or `auto` (only with OCR flags). |
| `--ocr-lang` | inferred | Tesseract packs, e.g. `tel+eng`, `kan+eng`. |
| `--ocr-dpi` | `200` | Render DPI when using OCR. |

**Example — Kannada textbook (text layer only):**

```bash
python -m curation \
  --pdf ./textbooks/kannada_social.pdf \
  --target-lang kn \
  --min-words 500 \
  --output curated_kn_textbook.jsonl
```

If you see a warning about **low script ratio**, the PDF may use legacy/custom fonts (not real Unicode in the text layer). Fix the source PDF or use `--pdf-ocr` as a last resort.

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

### Bodo news site — recursive link crawl (depth 5)

Same `--wiki-crawl-*` flags work for non-Wikipedia sites (same-host links from article/main content):

```bash
python -m curation \
  --url "https://bodo.sentinelassam.com/" \
  --target-lang brx \
  --wiki-crawl-depth 5 \
  --wiki-links-per-page 0 \
  --wiki-max-pages 500 \
  --wiki-crawl-delay 3 \
  --min-words 500 \
  --output curated_bodo_news.jsonl
```

Use `--wiki-max-pages` to cap total pages (default `150`). `0` = no limit.

**Short news articles:** A typical story is 200–400 words, so `--min-words 500` often yields **0 rows** (nothing is merged across URLs, and sub-500 tails are dropped). Use e.g. `--min-words 200` for Bodo/Indic news sites.

### Project Madurai HTML (local file or URL)

```bash
# Local .html file
python -m curation \
  --html pmuni0001.html \
  --target-lang ta \
  --min-words 500 \
  --output curated_thirukkural.jsonl

# Same etext via URL (.html URLs use the HTML extractor)
python -m curation \
  --url "https://www.projectmadurai.org/pm_etexts/utf8/pmuni0001.html" \
  --target-lang ta \
  --min-words 500 \
  --output curated_thirukkural.jsonl
```

### Many HTML URLs from a list (`tamil.txt`) → one JSONL

Processes each URL separately and **appends** rows to a single file (earlier URLs are kept):

```bash
python scripts/batch_curate_urls.py \
  --urls-file tamil.txt \
  --target-lang ta \
  --min-words 500 \
  --output tamil_html_all.jsonl \
  --delay 1
```

Use `--fresh` to wipe the output file before starting. Use `--start 100 --end 200` to resume a slice. Failed URLs are logged; the script continues.

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
  --url "https://www.maayboli.com/gulmohar/marathi-katha" \
  --target-lang mr \
  --wiki-crawl-depth 5 \
  --wiki-links-per-page 0 \
  --min-words 500 \
  --output marathi_stories.jsonl
```

### Combine seeds: article + main page (explicit URLs)

```bash
python -m curation \
  --url "https://www.maayboli.com/gulmohar/marathi-katha" \
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
│   │   ├── pdf.py           # PDF text layer (blocks; images skipped)
│   │   ├── pdf_ocr.py       # Optional OCR path
│   │   ├── html.py          # Local HTML + .html URLs (Trafilatura)
│   │   ├── web.py           # URLs (routes Wikipedia / HTML)
│   │   ├── crawl.py         # Same-site link crawl (non-Wikipedia)
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
| PDF returns 0 paragraphs | Text layer empty or legacy fonts (Priyaanka etc.); check with `-v`. Try a proper Unicode PDF, or `--pdf-ocr` for scans. |
| `brx` / low-resource language drops text | Lower `--confidence` (e.g. `0.70`); Bodo uses script detection, not fastText `brx`. |
| NumPy / fastText error | Ensure `numpy<2.0` (`pip install 'numpy<2.0'`). |
| Mixed English in Tamil rows | Script filter should drop most; try `--confidence 0.90` or use article URLs, not language-footer portal pages. |

---

## Help

```bash
python -m curation --help

```
python3 -c "
import json
from pathlib import Path
rows = [json.loads(l) for l in Path('new_data_collection/urdu/final_urdu_new.jsonl').read_text().splitlines() if l.strip()]
wc = [len(r['paragraph'].split()) for r in rows]
under_300 = sum(1 for w in wc if w < 300)
mid = sum(1 for w in wc if 300 <= w <= 450)
over_450 = sum(1 for w in wc if w > 450)
print(f'rows={len(rows)}, <300={under_300}, 300-450={mid}, >450={over_450}')
"

```


# Single language (full pipeline)
python3 scripts/run_validation_pipeline.py --input final/gujarati/gujarati.jsonl --workers 10

# All languages under final/*/
python3 scripts/run_validation_pipeline.py --all --workers 8

# Only run word filter (if validated_cleaned already exists)
python3 scripts/run_validation_pipeline.py --input final/Assamese/assamese_validated_cleaned.jsonl --skip-validate

# Only run validation/cleaning (skip word filter)
python3 scripts/run_validation_pipeline.py --input 