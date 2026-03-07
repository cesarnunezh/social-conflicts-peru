from __future__ import annotations

from pathlib import Path

import fitz

from .utils import (
    assign_local_case_ids,
    extract_71_ubicacion_caso_from_range,
    extract_inicio_dep_den_tipo_from_range,
    find_section_page_ranges,
    find_subsection_starts,
)


def _attach_metadata(rows: list[dict], pdf_path: Path, subsection: str) -> None:
    for row in rows:
        row["_section"] = "VII"
        row["_subsection"] = subsection
        row["_source_pdf"] = pdf_path.name
        row["_seccion"] = "VII"
        row["_grupo_conflicto"] = "latentes"


def extract_latent(
    pdf_path: Path,
    doc: fitz.Document,
    x_split: float = 300.0,
    section_range: tuple[int, int] | None = None,
    subsection_starts_override: dict[str, int] | None = None,
    section_label: str = "VII",
) -> list[dict]:
    _ = x_split  # kept for CLI compatibility
    if section_range is None:
        section_ranges = find_section_page_ranges(doc)
        if "VII" not in section_ranges:
            return []
        start, end = section_ranges["VII"]
    else:
        start, end = section_range

    subsection_starts = subsection_starts_override or find_subsection_starts(doc, start, end)
    out: list[dict] = []

    # Table right after VII heading (before 7.1): 4 fields schema.
    detail_sub = "7.1" if "7.1" in subsection_starts else ("6.1" if "6.1" in subsection_starts else None)
    table_end = subsection_starts.get(detail_sub, end) if detail_sub else end
    if start <= table_end:
        rows = extract_inicio_dep_den_tipo_from_range(
            doc,
            start,
            table_end,
            section="VII",
            subsection="7.tabla",
            pdf_name=pdf_path.name,
            begin_on_heading="DETALLE DE LOS CONFLICTOS LATENTES",
            stop_on_heading="CONFLICTOS QUE HAN PASADO DE ESTADO ACTIVO A LATENTE",
            numbered_rows=True,
        )
        _attach_metadata(rows, pdf_path, subsection="7.tabla")
        out.extend(rows)

    # 7.1 detailed records: only Ubicacion + Caso.
    if detail_sub is not None:
        sub_start = subsection_starts[detail_sub]
        rows = extract_71_ubicacion_caso_from_range(
            doc,
            sub_start,
            end,
            section="VII",
            subsection=detail_sub,
            pdf_name=pdf_path.name,
            begin_on_heading="CONFLICTOS QUE HAN PASADO DE ESTADO ACTIVO A LATENTE",
            stop_on_heading="CASOS QUE SALIERON DEL REPORTE DURANTE EL MES",
        )
        _attach_metadata(rows, pdf_path, subsection=detail_sub)
        out.extend(rows)

    for row in out:
        row["_section"] = section_label
        row["_seccion"] = section_label
        row["_grupo_conflicto"] = "latentes"

    assign_local_case_ids(out, pdf_path)
    return out
