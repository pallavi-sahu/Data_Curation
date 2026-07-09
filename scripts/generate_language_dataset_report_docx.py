#!/usr/bin/env python3
"""Generate client-facing language dataset report DOCX from JSON summary."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor

_REPO_ROOT = Path(__file__).resolve().parent.parent
JSON_PATH = _REPO_ROOT / "final" / "language_dataset_reports.json"
OUTPUT_PATH = _REPO_ROOT / "final" / "language_dataset_report.docx"

TOP_SOURCES = 8


def _set_cell_shading(cell, fill: str) -> None:
    shading = cell._element.get_or_add_tcPr()
    shd = shading.makeelement(
        qn("w:shd"),
        {qn("w:fill"): fill, qn("w:val"): "clear"},
    )
    shading.append(shd)


def add_table(
    doc: Document,
    headers: list[str],
    rows: list[list[str]],
    *,
    col_widths: list[float] | None = None,
) -> None:
    table = doc.add_table(rows=1 + len(rows), cols=len(headers))
    table.style = "Table Grid"
    hdr = table.rows[0]
    for i, text in enumerate(headers):
        hdr.cells[i].text = text
        _set_cell_shading(hdr.cells[i], "D6E4F0")
        for p in hdr.cells[i].paragraphs:
            for run in p.runs:
                run.bold = True
                run.font.size = Pt(10)

    for r_idx, row_data in enumerate(rows):
        row = table.rows[r_idx + 1]
        for c_idx, text in enumerate(row_data):
            row.cells[c_idx].text = text
            for p in row.cells[c_idx].paragraphs:
                for run in p.runs:
                    run.font.size = Pt(10)

    if col_widths:
        for row in table.rows:
            for i, width in enumerate(col_widths):
                row.cells[i].width = Inches(width)

    doc.add_paragraph()


def _pct(count: int, total: int) -> str:
    return f"{100 * count / total:.1f}%" if total else "0.0%"


def _short_source(url: str) -> str:
    url = url.replace("https://www.kaggle.com/datasets/", "Kaggle: ")
    url = url.replace("https://huggingface.co/datasets/", "HF: ")
    if len(url) > 72:
        return url[:69] + "..."
    return url


def _display_name(lang: str) -> str:
    return "Assamese" if lang == "Assamese" else lang.capitalize()


def _top_entry(items: dict[str, int]) -> tuple[str, int]:
    if not items:
        return "—", 0
    return max(items.items(), key=lambda x: x[1])


def add_title_page(doc: Document, *, languages: int, total_scripts: int) -> None:
    for _ in range(4):
        doc.add_paragraph()

    title = doc.add_paragraph()
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = title.add_run("Language Dataset Report")
    run.bold = True
    run.font.size = Pt(28)
    run.font.name = "Calibri"
    run.font.color.rgb = RGBColor(0x1F, 0x38, 0x64)

    subtitle = doc.add_paragraph()
    subtitle.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = subtitle.add_run("Indic Language Text-to-Speech Corpus — Source & Domain Inventory")
    run.font.size = Pt(14)
    run.font.name = "Calibri"
    run.font.color.rgb = RGBColor(0x44, 0x54, 0x6A)

    doc.add_paragraph()
    meta = doc.add_paragraph()
    meta.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = meta.add_run(
        f"Report Date: {date.today():%d %B %Y}  |  "
        f"{languages} languages  |  {total_scripts:,} scripts"
    )
    run.font.size = Pt(11)
    run.font.name = "Calibri"
    run.italic = True

    doc.add_page_break()


def add_executive_summary(doc: Document, reports: dict) -> None:
    doc.add_heading("Executive Summary", level=1)
    doc.add_paragraph(
        "This report inventories the live curated text corpus across 15 Indic languages. "
        "For each language it summarizes script volume, content sources, and domain "
        "distribution. The underlying records are stored as JSONL files under "
        "final/<language>/<language>.jsonl."
    )

    total_scripts = sum(r["total_scripts"] for r in reports.values())
    total_sources = sum(len(r["sources"]) for r in reports.values())
    domain_labels: set[str] = set()
    for r in reports.values():
        domain_labels.update(r["domains"])

    add_table(
        doc,
        ["Metric", "Value"],
        [
            ("Languages covered", str(len(reports))),
            ("Total scripts", f"{total_scripts:,}"),
            ("Grouped unique sources (all languages)", f"{total_sources:,}"),
            ("Distinct domain labels (all languages)", str(len(domain_labels))),
        ],
        col_widths=[3.0, 3.5],
    )


def add_summary_table(doc: Document, reports: dict) -> None:
    doc.add_heading("1. Corpus Overview by Language", level=1)
    rows = []
    for lang in sorted(reports, key=str.lower):
        r = reports[lang]
        total = r["total_scripts"]
        top_src, top_src_n = _top_entry(r["sources"])
        top_dom, top_dom_n = _top_entry(r["domains"])
        rows.append(
            [
                _display_name(lang),
                f"{total:,}",
                str(len(r["sources"])),
                str(len(r["domains"])),
                f"{_short_source(top_src)} ({_pct(top_src_n, total)})",
                f"{top_dom} ({_pct(top_dom_n, total)})",
            ]
        )

    add_table(
        doc,
        [
            "Language",
            "Scripts",
            "Sources",
            "Domains",
            "Primary source",
            "Top domain",
        ],
        rows,
        col_widths=[0.9, 0.7, 0.7, 0.7, 2.3, 1.2],
    )


def add_language_detail(doc: Document, lang: str, report: dict) -> None:
    total = report["total_scripts"]
    doc.add_heading(f"{_display_name(lang)} — {total:,} scripts", level=2)

    source_rows: list[list[str]] = []
    remaining = 0
    for i, (src, count) in enumerate(report["sources"].items()):
        if i < TOP_SOURCES:
            source_rows.append([_short_source(src), f"{count:,}", _pct(count, total)])
        else:
            remaining += count
    other_sources = max(0, len(report["sources"]) - TOP_SOURCES)
    if other_sources:
        source_rows.append(
            [f"Other sources ({other_sources} grouped sources)", f"{remaining:,}", _pct(remaining, total)]
        )

    doc.add_heading("Sources", level=3)
    add_table(
        doc,
        ["Source", "Scripts", "Share"],
        source_rows,
        col_widths=[4.0, 0.9, 0.8],
    )

    doc.add_heading("Domains", level=3)
    domain_rows = [
        [domain, f"{count:,}", _pct(count, total)]
        for domain, count in report["domains"].items()
    ]
    add_table(
        doc,
        ["Domain", "Scripts", "Share"],
        domain_rows,
        col_widths=[2.5, 0.9, 0.8],
    )


def add_observations(doc: Document, reports: dict) -> None:
    doc.add_heading("2. Key Observations", level=1)

    concentrated = []
    diverse = []
    domain_skew = []

    for lang in sorted(reports, key=str.lower):
        r = reports[lang]
        total = r["total_scripts"]
        top_src_n = _top_entry(r["sources"])[1]
        top_dom, top_dom_n = _top_entry(r["domains"])
        if top_src_n / total >= 0.85:
            concentrated.append(_display_name(lang))
        if len(r["sources"]) >= 100:
            diverse.append(f"{_display_name(lang)} ({len(r['sources'])} sources)")
        if top_dom_n / total >= 0.75:
            domain_skew.append(f"{_display_name(lang)} — {top_dom} ({_pct(top_dom_n, total)})")

    if concentrated:
        doc.add_paragraph(
            "Single-source dominance (>85% of scripts): "
            + ", ".join(concentrated)
            + ".",
            style="List Bullet",
        )
    if diverse:
        doc.add_paragraph(
            "Broadest source diversity: " + "; ".join(diverse) + ".",
            style="List Bullet",
        )
    if domain_skew:
        doc.add_paragraph(
            "Strong domain concentration (>75% in one domain): "
            + "; ".join(domain_skew)
            + ".",
            style="List Bullet",
        )

    doc.add_paragraph(
        "A machine-readable breakdown with full source URLs is available in "
        "final/language_dataset_reports.json.",
    )


def main() -> None:
    reports: dict = json.loads(JSON_PATH.read_text(encoding="utf-8"))
    total_scripts = sum(r["total_scripts"] for r in reports.values())

    doc = Document()
    for section in doc.sections:
        section.top_margin = Inches(1)
        section.bottom_margin = Inches(1)
        section.left_margin = Inches(1)
        section.right_margin = Inches(1)

    style = doc.styles["Normal"]
    style.font.name = "Calibri"
    style.font.size = Pt(11)

    add_title_page(doc, languages=len(reports), total_scripts=total_scripts)
    add_executive_summary(doc, reports)
    add_summary_table(doc, reports)

    doc.add_page_break()
    doc.add_heading("Appendix — Per-Language Detail", level=1)
    doc.add_paragraph(
        "The following tables list the leading sources and full domain mix for each language. "
        "Only the top sources are shown; remaining sources are rolled up."
    )

    for lang in sorted(reports, key=str.lower):
        add_language_detail(doc, lang, reports[lang])

    doc.add_page_break()
    add_observations(doc, reports)

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    doc.save(OUTPUT_PATH)
    print(f"Wrote {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
