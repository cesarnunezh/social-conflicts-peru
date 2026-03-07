from __future__ import annotations

from pathlib import Path

import fitz

from .utils import assign_local_case_ids, extract_viii_records_from_range, find_section_page_ranges, find_subsection_starts


def extract_inactive(
    pdf_path: Path,
    doc: fitz.Document,
    section_range: tuple[int, int] | None = None,
    subsection_starts_override: dict[str, int] | None = None,
    section_label: str = "VIII",
) -> list[dict]:
    if section_range is None:
        section_ranges = find_section_page_ranges(doc)
        if "VIII" not in section_ranges:
            return []
        start, end = section_ranges["VIII"]
    else:
        start, end = section_range

    subsection_starts = subsection_starts_override or find_subsection_starts(doc, start, end)

    out: list[dict] = []

    if "8.1" in subsection_starts:
        s81 = subsection_starts["8.1"]
        e81 = min([subsection_starts[k] for k in ["8.2"] if k in subsection_starts and subsection_starts[k] > s81] + [end])
        out.extend(
            extract_viii_records_from_range(
                doc,
                s81,
                e81,
                section="VIII",
                subsection="8.1",
                pdf_name=pdf_path.name,
                begin_on_heading="8.1 CONFLICTOS RESUELTOS",
                stop_on_heading="8.2 CONFLICTOS EN ESTADO LATENTE",
            )
        )

    if "8.2" in subsection_starts:
        s82 = subsection_starts["8.2"]
        out.extend(
            extract_viii_records_from_range(
                doc,
                s82,
                end,
                section="VIII",
                subsection="8.2",
                pdf_name=pdf_path.name,
                begin_on_heading="8.2 CONFLICTOS EN ESTADO LATENTE",
                stop_on_heading="IX. OTROS INDICADORES",
            )
        )

    # Fallback: if 8.1/8.2 headings are not detected, parse entire VIII range.
    if not out:
        out.extend(extract_viii_records_from_range(doc, start, end, section="VIII", subsection="8.detalle", pdf_name=pdf_path.name))

    for row in out:
        row["_section"] = section_label
        row["_seccion"] = section_label
        row["_grupo_conflicto"] = "inactivos"

    assign_local_case_ids(out, pdf_path)
    return out
