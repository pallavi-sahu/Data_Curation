# Unique Word Diversity Report

> **Report date:** 07 July 2026  
> **Corpus:** `final/<language>/<language>.jsonl` (live curated datasets)  
> **Machine-readable JSON:** [`vocabulary_diversity_reports.json`](vocabulary_diversity_reports.json)

Unique-word metrics use the same Indic-aware tokenization as the validation pipeline (`validation/tokenization.py`).

## Summary — unique word % by language

Sorted by average unique word % per script (primary diversity measure).

| Language | Scripts | Total words | Unique words | Avg unique word % (per script) | Corpus unique word % | Scripts below 25% |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Tamil | 2,048 | 778,031 | 290,748 | 84.3% | 37.37% | 0 (0.0%) |
| Kannada | 2,035 | 751,435 | 116,820 | 79.8% | 15.55% | 0 (0.0%) |
| Malayalam | 2,303 | 846,327 | 226,959 | 79.0% | 26.82% | 0 (0.0%) |
| Telugu | 2,534 | 923,486 | 170,464 | 79.0% | 18.46% | 0 (0.0%) |
| Marathi | 2,434 | 897,610 | 118,776 | 73.6% | 13.23% | 1 (0.0%) |
| Odia | 2,441 | 982,677 | 95,224 | 70.8% | 9.69% | 0 (0.0%) |
| Bodo | 5,191 | 1,659,615 | 101,287 | 67.3% | 6.10% | 2 (0.0%) |
| Assamese | 2,125 | 800,781 | 56,807 | 65.7% | 7.09% | 0 (0.0%) |
| Manipuri | 3,142 | 1,211,906 | 63,978 | 62.5% | 5.28% | 0 (0.0%) |
| Gujarati | 2,451 | 897,221 | 73,388 | 62.0% | 8.18% | 1 (0.0%) |
| Maithili | 1,829 | 596,021 | 37,819 | 57.5% | 6.35% | 0 (0.0%) |
| Punjabi | 1,733 | 646,057 | 44,545 | 55.9% | 6.89% | 0 (0.0%) |
| Sindhi | 593 | 212,098 | 15,669 | 53.6% | 7.39% | 1 (0.2%) |
| Urdu | 2,282 | 837,193 | 34,440 | 53.2% | 4.11% | 1 (0.0%) |
| Santali | 869 | 324,060 | 25,642 | 47.8% | 7.91% | 0 (0.0%) |

### Metric definitions

- **Avg unique word % (per script)** — for each script, (unique words in that script ÷ total words in that script) × 100, then averaged across all scripts. This is the primary diversity measure.
- **Corpus unique word %** — (total distinct unique words across the whole language corpus ÷ total word count across all scripts) × 100.
- **Scripts below 25%** — scripts where fewer than 25% of words are unique within the passage (validation threshold).

## Per-language detail

### Assamese

- Scripts: **2,125** | Total words: **800,781** | Unique words: **56,807**
- Avg unique word % (per script): **65.7%** (median **66.4%**, range **35.9%**–**81.4%**)
- Corpus unique word %: **7.09%**
- Scripts below 25% unique words: **0** (0.0%)

### Bodo

- Scripts: **5,191** | Total words: **1,659,615** | Unique words: **101,287**
- Avg unique word % (per script): **67.3%** (median **67.9%**, range **17.9%**–**81.5%**)
- Corpus unique word %: **6.10%**
- Scripts below 25% unique words: **2** (0.0%)

### Gujarati

- Scripts: **2,451** | Total words: **897,221** | Unique words: **73,388**
- Avg unique word % (per script): **62.0%** (median **62.3%**, range **23.1%**–**83.6%**)
- Corpus unique word %: **8.18%**
- Scripts below 25% unique words: **1** (0.0%)

### Kannada

- Scripts: **2,035** | Total words: **751,435** | Unique words: **116,820**
- Avg unique word % (per script): **79.8%** (median **81.7%**, range **35.1%**–**92.3%**)
- Corpus unique word %: **15.55%**
- Scripts below 25% unique words: **0** (0.0%)

### Maithili

- Scripts: **1,829** | Total words: **596,021** | Unique words: **37,819**
- Avg unique word % (per script): **57.5%** (median **57.2%**, range **34.4%**–**77.7%**)
- Corpus unique word %: **6.35%**
- Scripts below 25% unique words: **0** (0.0%)

### Malayalam

- Scripts: **2,303** | Total words: **846,327** | Unique words: **226,959**
- Avg unique word % (per script): **79.0%** (median **80.6%**, range **32.3%**–**100.0%**)
- Corpus unique word %: **26.82%**
- Scripts below 25% unique words: **0** (0.0%)

### Manipuri

- Scripts: **3,142** | Total words: **1,211,906** | Unique words: **63,978**
- Avg unique word % (per script): **62.5%** (median **62.6%**, range **33.4%**–**83.0%**)
- Corpus unique word %: **5.28%**
- Scripts below 25% unique words: **0** (0.0%)

### Marathi

- Scripts: **2,434** | Total words: **897,610** | Unique words: **118,776**
- Avg unique word % (per script): **73.6%** (median **74.4%**, range **23.6%**–**90.9%**)
- Corpus unique word %: **13.23%**
- Scripts below 25% unique words: **1** (0.0%)

### Odia

- Scripts: **2,441** | Total words: **982,677** | Unique words: **95,224**
- Avg unique word % (per script): **70.8%** (median **70.8%**, range **46.7%**–**98.7%**)
- Corpus unique word %: **9.69%**
- Scripts below 25% unique words: **0** (0.0%)

### Punjabi

- Scripts: **1,733** | Total words: **646,057** | Unique words: **44,545**
- Avg unique word % (per script): **55.9%** (median **56.3%**, range **35.3%**–**75.1%**)
- Corpus unique word %: **6.89%**
- Scripts below 25% unique words: **0** (0.0%)

### Santali

- Scripts: **869** | Total words: **324,060** | Unique words: **25,642**
- Avg unique word % (per script): **47.8%** (median **47.3%**, range **28.6%**–**71.8%**)
- Corpus unique word %: **7.91%**
- Scripts below 25% unique words: **0** (0.0%)

### Sindhi

- Scripts: **593** | Total words: **212,098** | Unique words: **15,669**
- Avg unique word % (per script): **53.6%** (median **53.6%**, range **22.5%**–**67.7%**)
- Corpus unique word %: **7.39%**
- Scripts below 25% unique words: **1** (0.2%)

### Tamil

- Scripts: **2,048** | Total words: **778,031** | Unique words: **290,748**
- Avg unique word % (per script): **84.3%** (median **85.8%**, range **32.6%**–**99.1%**)
- Corpus unique word %: **37.37%**
- Scripts below 25% unique words: **0** (0.0%)

### Telugu

- Scripts: **2,534** | Total words: **923,486** | Unique words: **170,464**
- Avg unique word % (per script): **79.0%** (median **79.9%**, range **38.4%**–**91.7%**)
- Corpus unique word %: **18.46%**
- Scripts below 25% unique words: **0** (0.0%)

### Urdu

- Scripts: **2,282** | Total words: **837,193** | Unique words: **34,440**
- Avg unique word % (per script): **53.2%** (median **53.4%**, range **20.9%**–**67.3%**)
- Corpus unique word %: **4.11%**
- Scripts below 25% unique words: **1** (0.0%)
