from __future__ import annotations

import argparse
import glob
import json
from datetime import datetime, timezone
from pathlib import Path

import fitz

from ..config import directories
from .utils import (
    assign_parent_roman_by_heading_order,
    build_heading_ranges,
    detect_decimal_headings_with_positions,
    detect_roman_headings_with_positions,
    extract_report_number,
    iter_pdf_pages_text,
    nest_subsections_within_roman,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Track PDF section structure by page ranges.")
    parser.add_argument(
        "--input-glob",
        default=str(directories.DOCUMENTS / "*.pdf*"),
        help="Glob pattern for input PDFs.",
    )
    parser.add_argument(
        "--output-json",
        type=Path,
        default=directories.LOGS / "pdf_section_map.json",
        help="Output JSON for document structure mapping.",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Raise on structural anomalies instead of recording warnings.",
    )
    return parser.parse_args()


def _build_report_structure(pdf_path: Path, strict: bool = False) -> dict:
    with fitz.open(pdf_path) as doc:
        pages = iter_pdf_pages_text(doc)
        page_count = doc.page_count

    roman_hits: list[dict] = []
    decimal_hits: list[dict] = []
    for page in pages:
        page_no = int(page["page"])
        text = str(page["text"])
        for hit in detect_roman_headings_with_positions(text):
            roman_hits.append(
                {
                    "id": hit["id"],
                    "title": hit["title"],
                    "start_page": page_no,
                    "line_index": int(hit["line_index"]),
                }
            )
        for hit in detect_decimal_headings_with_positions(text):
            decimal_hits.append(
                {
                    "id": hit["id"],
                    "title": hit["title"],
                    "start_page": page_no,
                    "line_index": int(hit["line_index"]),
                }
            )

    roman_sections = build_heading_ranges(roman_hits, page_count)
    subsections = build_heading_ranges(decimal_hits, page_count)
    subsections = assign_parent_roman_by_heading_order(roman_hits, decimal_hits, subsections)
    roman_ids = {str(row["id"]) for row in roman_sections}
    int_to_roman = {
        1: "I",
        2: "II",
        3: "III",
        4: "IV",
        5: "V",
        6: "VI",
        7: "VII",
        8: "VIII",
        9: "IX",
        10: "X",
    }
    normalized_subsections: list[dict] = []
    for row in subsections:
        updated = dict(row)
        sid = str(updated.get("id") or "")
        major = sid.split(".", 1)[0]
        if major.isdigit():
            candidate = int_to_roman.get(int(major))
            if candidate and candidate in roman_ids:
                updated["parent_roman"] = candidate
        normalized_subsections.append(updated)
    subsections = normalized_subsections
    subsections = nest_subsections_within_roman(roman_sections, subsections)
    nested_subsections: dict[str, list[dict]] = {}
    for subsection in subsections:
        parent = subsection.get("parent_roman")
        if parent is None:
            continue
        nested_subsections.setdefault(str(parent), []).append(
            {
                "id": subsection["id"],
                "title": subsection["title"],
                "start_page": subsection["start_page"],
                "end_page": subsection["end_page"],
            }
        )
    for roman in roman_sections:
        roman["subsections"] = nested_subsections.get(str(roman["id"]), [])

    warnings: list[str] = []
    if not roman_sections:
        warnings.append("no_roman_sections_detected")
    if not subsections:
        warnings.append("no_decimal_subsections_detected")
    if any(item.get("parent_roman") is None for item in subsections):
        warnings.append("subsections_without_parent_roman")

    if strict and warnings:
        raise ValueError(f"{pdf_path.name}: {', '.join(warnings)}")

    return {
        "pdf_name": pdf_path.name,
        "report_number": extract_report_number(pdf_path),
        "page_count": page_count,
        "roman_sections": roman_sections,
        "subsections": subsections,
        "warnings": warnings,
    }


def main() -> None:
    args = parse_args()
    pdf_paths = sorted((Path(p) for p in glob.glob(args.input_glob)), key=lambda p: (extract_report_number(p), p.name))
    if not pdf_paths:
        raise FileNotFoundError(f"No PDF files matched pattern: {args.input_glob}")

    reports: list[dict] = []
    for pdf_path in pdf_paths:
        try:
            reports.append(_build_report_structure(pdf_path, strict=args.strict))
        except Exception as exc:
            if args.strict:
                raise
            reports.append(
                {
                    "pdf_name": pdf_path.name,
                    "report_number": extract_report_number(pdf_path),
                    "page_count": None,
                    "roman_sections": [],
                    "subsections": [],
                    "warnings": [f"parse_error: {exc}"],
                }
            )

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_dir": str(directories.DOCUMENTS),
        "total_reports": len(reports),
        "reports": reports,
    }
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Saved section map for {len(reports)} reports to {args.output_json}")


if __name__ == "__main__":
    main()
