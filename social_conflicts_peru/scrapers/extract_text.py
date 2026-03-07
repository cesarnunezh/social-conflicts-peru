from __future__ import annotations

import argparse
import glob
import hashlib
import json
from pathlib import Path
from typing import Optional

import fitz

from ..config import directories
from ..processing.conflict_identity import ScoreConfig, assign_conflict_ids
from .extract_active import extract_active
from .extract_inactive import extract_inactive
from .extract_latent import extract_latent
from .utils import (
    detect_structure_profile,
    extract_report_number,
    resolve_active_latent_context,
    sort_records,
)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def unique_pdf_paths(paths: list[Path]) -> list[Path]:
    seen_hashes: set[str] = set()
    unique: list[Path] = []
    for path in paths:
        try:
            h = file_sha256(path)
        except OSError:
            continue
        if h in seen_hashes:
            continue
        seen_hashes.add(h)
        unique.append(path)
    return unique


def field_contains_source(field_value: str, source_text: str) -> bool:
    import re
    import unicodedata

    def norm(s: str) -> str:
        s = unicodedata.normalize("NFD", s)
        s = "".join(ch for ch in s if unicodedata.category(ch) != "Mn")
        s = s.lower()
        s = re.sub(r"[^a-z0-9]+", "", s)
        return s

    left = norm(field_value)
    if len(left) < 40:
        return True
    left = left[:260]
    right = norm(source_text)
    if left[:120] in right:
        return True
    chunks = [left[i : i + 50] for i in range(0, min(len(left), 220), 25)]
    hits = sum(1 for chunk in chunks if len(chunk) >= 35 and chunk in right)
    return hits >= 2


def generate_qa_report(cases: list[dict], docs_dir: Path, previous_report: Optional[dict]) -> dict:
    import re

    header_bleed_re = re.compile(r"ADJUNT[ÍI]A PARA LA PREVENCI[ÓO]N|DEFENSOR[ÍI]A DEL PUEBLO\s*\d+", re.IGNORECASE)

    per_type: dict[str, int] = {}
    per_case: list[dict] = []

    grouped: dict[str, list[dict]] = {}
    for case in cases:
        grouped.setdefault(case.get("_source_pdf", ""), []).append(case)

    for pdf_name, pdf_cases in grouped.items():
        pdf_path = docs_dir / pdf_name
        if not pdf_path.exists():
            continue

        doc = fitz.open(pdf_path)
        page_cache: dict[int, str] = {}

        for case in pdf_cases:
            p_start = int(case.get("_page_start") or 1)
            p_end = int(case.get("_page_end") or p_start)
            issues: list[str] = []

            joined_source: list[str] = []
            for pno in range(p_start - 1, p_end):
                if pno < 0 or pno >= doc.page_count:
                    continue
                if pno not in page_cache:
                    page_cache[pno] = doc.load_page(pno).get_text("text")
                joined_source.append(page_cache[pno])
            source_text = "\n".join(joined_source)

            for field_name in ["Caso", "Actores secundarios", "Actores terciarios", "Hechos del Mes"]:
                value = case.get(field_name)
                if value and isinstance(value, str) and header_bleed_re.search(value):
                    issues.append("header_bleed_in_fields")
                    break

            if case.get("_dialogo") not in {"HAY DIÁLOGO", "NO HAY DIÁLOGO"}:
                issues.append("dialogue_status_missing")

            for field_name in ["Caso", "Hechos del Mes"]:
                value = case.get(field_name)
                if value and isinstance(value, str) and not field_contains_source(value, source_text):
                    issues.append(f"{field_name.lower().replace(' ', '_')}_not_in_source")

            if issues:
                uniq = sorted(set(issues))
                for issue in uniq:
                    per_type[issue] = per_type.get(issue, 0) + 1
                per_case.append(
                    {
                        "case_id": case.get("_case_id_local"),
                        "source_pdf": case.get("_source_pdf"),
                        "page_start": case.get("_page_start"),
                        "page_end": case.get("_page_end"),
                        "issues": uniq,
                    }
                )

        doc.close()

    current_summary = {
        "total_cases": len(cases),
        "cases_with_issues": len(per_case),
        "issue_counts": dict(sorted(per_type.items(), key=lambda kv: kv[0])),
    }

    previous_counts = {}
    if previous_report and isinstance(previous_report, dict):
        previous_counts = ((previous_report.get("summary") or {}).get("issue_counts") or {})

    delta: dict[str, int] = {}
    keys = set(previous_counts.keys()) | set(current_summary["issue_counts"].keys())
    for key in sorted(keys):
        delta[key] = int(current_summary["issue_counts"].get(key, 0)) - int(previous_counts.get(key, 0))

    return {
        "summary": current_summary,
        "delta_vs_previous": delta,
        "issues_by_case": per_case,
    }


def qa_report_to_markdown(report: dict) -> str:
    lines = [
        "# Extraction QA Report",
        "",
        f"- Total cases: {report['summary']['total_cases']}",
        f"- Cases with issues: {report['summary']['cases_with_issues']}",
        "",
        "## Issue Counts",
    ]

    issue_counts = report["summary"]["issue_counts"]
    if not issue_counts:
        lines.append("- No issues detected")
    else:
        for issue, count in issue_counts.items():
            delta = report["delta_vs_previous"].get(issue, 0)
            sign = "+" if delta > 0 else ""
            lines.append(f"- {issue}: {count} ({sign}{delta} vs previous)")

    lines.append("")
    lines.append("## Cases")
    if not report.get("issues_by_case"):
        lines.append("- No flagged cases")
    else:
        for item in report["issues_by_case"]:
            lines.append(
                f"- {item['case_id']} | {item['source_pdf']} p{item['page_start']}-{item['page_end']} | "
                f"{', '.join(item['issues'])}"
            )
    lines.append("")
    return "\n".join(lines)


def structure_report_to_markdown(entries: list[dict]) -> str:
    lines = ["# PDF Structure Alerts", ""]
    drifted = [e for e in entries if float(e.get("drift_score") or 0) >= 0.35]
    if not drifted:
        lines.append("- No structure drifts above warning threshold")
        lines.append("")
        return "\n".join(lines)

    for e in sorted(drifted, key=lambda x: float(x.get("drift_score") or 0), reverse=True):
        flags = ", ".join(e.get("drift_flags") or []) or "none"
        lines.append(
            f"- {e.get('pdf_name')}: score={e.get('drift_score')} | profile={e.get('detected_structure_profile')} | flags={flags}"
        )
    lines.append("")
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Extract active/latent/inactive social conflict cases from PDF reports.")
    parser.add_argument(
        "--input-glob",
        default=str(directories.DOCUMENTS / "*.pdf*"),
        help="Glob pattern for input PDFs.",
    )
    parser.add_argument(
        "--output-json",
        type=Path,
        default=directories.ROOT_DIR / "conflictos_structured.json",
        help="Unified extraction JSON output path.",
    )
    parser.add_argument(
        "--qa-report-json",
        type=Path,
        default=directories.LOGS / "extract_text_qa_report.json",
    )
    parser.add_argument(
        "--qa-report-md",
        type=Path,
        default=directories.LOGS / "extract_text_qa_report.md",
    )
    parser.add_argument(
        "--structure-report-json",
        type=Path,
        default=directories.LOGS / "pdf_structure_report.json",
    )
    parser.add_argument(
        "--structure-alert-md",
        type=Path,
        default=directories.LOGS / "pdf_structure_alerts.md",
    )
    parser.add_argument(
        "--section-map-json",
        type=Path,
        default=directories.LOGS / "pdf_section_map.json",
        help="Section mapping generated by track_structure.py",
    )
    parser.add_argument(
        "--structure-mode",
        choices=["map-first", "builder-only", "map-only"],
        default="map-first",
        help="How to resolve active/latent section ranges.",
    )
    parser.add_argument("--x-split", type=float, default=300.0)
    parser.add_argument("--with-identity", action="store_true")
    parser.add_argument(
        "--identity-registry-path",
        type=Path,
        default=directories.PROCESSED_DATA / "conflicts_registry.json",
    )
    parser.add_argument(
        "--identity-occurrences-path",
        type=Path,
        default=directories.PROCESSED_DATA / "conflicts_occurrences.json",
    )
    parser.add_argument("--high-threshold", type=float, default=0.82)
    parser.add_argument("--review-threshold", type=float, default=0.74)
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    pdf_paths = sorted((Path(p) for p in glob.glob(args.input_glob)), key=extract_report_number)
    pdf_paths = unique_pdf_paths(pdf_paths)
    if not pdf_paths:
        raise FileNotFoundError(f"No PDF files matched pattern: {args.input_glob}")

    all_results: list[dict] = []
    structure_entries: list[dict] = []

    for pdf_path in pdf_paths:
        try:
            doc = fitz.open(pdf_path)
            context = resolve_active_latent_context(
                doc,
                pdf_path,
                args.section_map_json,
                mode=args.structure_mode,
            )

            active = extract_active(
                pdf_path,
                doc,
                x_split=args.x_split,
                section_range=context.get("active_range"),
                subsection_starts_override=context.get("active_subsections"),
                section_label=str(context.get("active_section_id") or "VI"),
            )
            latent = extract_latent(
                pdf_path,
                doc,
                x_split=args.x_split,
                section_range=context.get("latent_range"),
                subsection_starts_override=context.get("latent_subsections"),
                section_label=str(context.get("latent_section_id") or "VII"),
            )
            inactive = extract_inactive(
                pdf_path,
                doc,
                section_range=context.get("inactive_range"),
                subsection_starts_override=context.get("inactive_subsections"),
                section_label=str(context.get("inactive_section_id") or "VIII"),
            )

            records = active + latent + inactive
            sort_records(records)
            all_results.extend(records)

            section_ranges: dict[str, tuple[int, int]] = {}
            if context.get("active_range"):
                section_ranges[str(context.get("active_section_id") or "ACTIVE")] = context["active_range"]
            if context.get("latent_range"):
                section_ranges[str(context.get("latent_section_id") or "LATENT")] = context["latent_range"]
            if context.get("inactive_range"):
                section_ranges[str(context.get("inactive_section_id") or "INACTIVE")] = context["inactive_range"]
            profile = detect_structure_profile(doc, records, section_ranges)
            profile["pdf_name"] = pdf_path.name
            profile["report_number"] = extract_report_number(pdf_path)
            profile["structure_source"] = context.get("source")
            profile["structure_flags"] = context.get("flags", [])
            structure_entries.append(profile)

            doc.close()
        except Exception as exc:
            print(f"[WARN] Failed to parse {pdf_path.name}: {exc}")

    sort_records(all_results)

    if args.with_identity:
        cfg = ScoreConfig(high_threshold=args.high_threshold, review_threshold=args.review_threshold)
        all_results, registry = assign_conflict_ids(all_results, args.identity_registry_path, cfg=cfg)
        args.identity_registry_path.parent.mkdir(parents=True, exist_ok=True)
        args.identity_registry_path.write_text(json.dumps(registry, ensure_ascii=False, indent=2), encoding="utf-8")
        args.identity_occurrences_path.parent.mkdir(parents=True, exist_ok=True)
        args.identity_occurrences_path.write_text(json.dumps(all_results, ensure_ascii=False, indent=2), encoding="utf-8")

    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(all_results, ensure_ascii=False, indent=2), encoding="utf-8")

    previous_report = None
    if args.qa_report_json.exists():
        try:
            previous_report = json.loads(args.qa_report_json.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            previous_report = None

    qa_report = generate_qa_report(all_results, directories.DOCUMENTS, previous_report)
    args.qa_report_json.parent.mkdir(parents=True, exist_ok=True)
    args.qa_report_json.write_text(json.dumps(qa_report, ensure_ascii=False, indent=2), encoding="utf-8")
    args.qa_report_md.parent.mkdir(parents=True, exist_ok=True)
    args.qa_report_md.write_text(qa_report_to_markdown(qa_report), encoding="utf-8")

    args.structure_report_json.parent.mkdir(parents=True, exist_ok=True)
    args.structure_report_json.write_text(json.dumps(structure_entries, ensure_ascii=False, indent=2), encoding="utf-8")
    args.structure_alert_md.parent.mkdir(parents=True, exist_ok=True)
    args.structure_alert_md.write_text(structure_report_to_markdown(structure_entries), encoding="utf-8")

    print(f"Extracted {len(all_results)} cases into {args.output_json}")
    print(f"QA report JSON: {args.qa_report_json}")
    print(f"Structure report JSON: {args.structure_report_json}")


if __name__ == "__main__":
    main()
