from __future__ import annotations

from typing import Literal, Optional, TypeAlias, TypedDict


SectionName = Literal["V", "VI", "VII", "VIII"]
DialogueStatus = Literal["HAY DIÁLOGO", "NO HAY DIÁLOGO"]

# Extraction records keep original report keys (including spaces), so we use a
# generic alias here and enforce shape at runtime.
ConflictRecord: TypeAlias = dict[str, object]


class StructureReportEntry(TypedDict, total=False):
    pdf_name: str
    report_number: int
    detected_structure_profile: str
    sections_found: list[str]
    subsections_found: dict[str, int]
    drift_score: float
    drift_flags: list[str]
    records_total: int
    records_low_confidence: int


class ConflictRegistryEntry(TypedDict, total=False):
    conflict_uid: str
    tipo_norm: str
    inicio_norm: str
    caso_norm: str
    actores_norm: str
    canonical_tipo: str
    canonical_inicio: Optional[str]
    canonical_caso: str
    canonical_actores: str
    first_seen_pdf: str
    last_seen_pdf: str
    occurrences: int
