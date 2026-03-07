from __future__ import annotations

from pathlib import Path

import fitz

from .utils import (
    assign_local_case_ids,
    extract_64_reactivated_from_range,
    extract_detailed_cases_from_range,
    extract_inicio_dep_den_tipo_from_range,
    find_section_page_ranges,
    find_subsection_starts,
)


def _attach_metadata(rows: list[dict], pdf_path: Path, subsection: str) -> None:
    for row in rows:
        row["_section"] = "VI"
        row["_subsection"] = subsection
        row["_source_pdf"] = pdf_path.name
        # compatibility with old key name
        row["_seccion"] = "VI"
        row["_grupo_conflicto"] = "activos"


def extract_active(
    pdf_path: Path,
    doc: fitz.Document,
    x_split: float = 300.0,
    section_range: tuple[int, int] | None = None,
    subsection_starts_override: dict[str, int] | None = None,
    section_label: str = "VI",
) -> list[dict]:
    _ = x_split  # kept for CLI compatibility
    if section_range is None:
        section_ranges = find_section_page_ranges(doc)
        if "VI" not in section_ranges:
            return []
        start, end = section_ranges["VI"]
    else:
        start, end = section_range

    subsection_starts = subsection_starts_override or find_subsection_starts(doc, start, end)

    out: list[dict] = []

    # Keep detailed active conflicts (6.1/6.2 style) before 6.3.
    first_table_start = min([subsection_starts[k] for k in ["6.3", "6.4"] if k in subsection_starts], default=end + 1)
    detail_end = first_table_start - 1
    if start <= detail_end:
        detailed = extract_detailed_cases_from_range(doc, start, detail_end, x_split=x_split)
        _attach_metadata(detailed, pdf_path, subsection="6.detalle")
        out.extend(detailed)

    # 6.3 -> Ingreso, Departamento, Denominacion, Tipo
    if "6.3" in subsection_starts:
        sub_start = subsection_starts["6.3"]
        sub_end = min(
            [subsection_starts[k] for k in ["6.4"] if k in subsection_starts and subsection_starts[k] > sub_start]
            + [end]
        )
        rows = extract_inicio_dep_den_tipo_from_range(
            doc,
            sub_start,
            sub_end,
            section="VI",
            subsection="6.3",
            pdf_name=pdf_path.name,
            begin_on_heading="6.3 CONFLICTOS ACTIVOS QUE NO REGISTRARON HECHOS DURANTE EL MES",
            stop_on_heading="6.4 CONFLICTOS REACTIVADOS",
            numbered_rows=False,
        )
        _attach_metadata(rows, pdf_path, subsection="6.3")
        out.extend(rows)

    # 6.4 currently uses same output schema.
    if "6.4" in subsection_starts:
        sub_start = subsection_starts["6.4"]
        sub_end = end
        rows = extract_64_reactivated_from_range(
            doc,
            sub_start,
            sub_end,
            section="VI",
            subsection="6.4",
            pdf_name=pdf_path.name,
            stop_on_heading="VII. DETALLE DE LOS CONFLICTOS LATENTES",
        )
        _attach_metadata(rows, pdf_path, subsection="6.4")
        out.extend(rows)

    for row in out:
        row["_section"] = section_label
        row["_seccion"] = section_label
        row["_grupo_conflicto"] = "activos"

    assign_local_case_ids(out, pdf_path)
    return out
