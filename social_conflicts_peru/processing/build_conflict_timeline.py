from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from ..config import directories
from .conflict_identity import ScoreConfig, assign_conflict_ids


TYPE_ALIASES = {
    "socioambiental": "Socioambiental",
    "comunal": "Comunal",
    "laboral": "Laboral",
    "electoral": "Electoral",
    "otros asuntos": "Otros asuntos",
    "demarcacion territorial": "Demarcación territorial",
    "asuntos de gobierno nacional": "Asuntos de gobierno nacional",
    "asuntos de gobierno regional": "Asuntos de gobierno regional",
    "asuntos de gobierno local": "Asuntos de gobierno local",
}


def save_json(path: Path, obj: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def extract_report_number_from_name(name: str) -> int:
    match = re.search(r"(\d{2,4})", name or "")
    return int(match.group(1)) if match else -1


def normalize_state(record: dict) -> str:
    group = str(record.get("_grupo_conflicto") or "").strip().lower()
    if group == "activos":
        return "active"
    if group == "latentes":
        return "latent"
    if group == "inactivos":
        return "inactive"
    return "unknown"


def state_priority(state: str) -> int:
    # If the same report has multiple rows for same conflict, keep strongest.
    return {"active": 3, "latent": 2, "inactive": 1}.get(state, 0)


def _clean_text(value: object) -> str:
    return str(value or "").strip()


def _normalize_type_name(raw: str) -> str:
    key = re.sub(r"\s+", " ", raw.lower().strip())
    key = (
        key.replace("á", "a")
        .replace("é", "e")
        .replace("í", "i")
        .replace("ó", "o")
        .replace("ú", "u")
    )
    return TYPE_ALIASES.get(key, raw.strip().capitalize())


def _infer_tipo_from_text(text: str) -> str:
    if not text:
        return ""
    m = re.match(r"^\s*tipo\s+(?:por\s+)?([^\.\n,;:]{3,80})", text, flags=re.IGNORECASE)
    if not m:
        return ""
    candidate = re.sub(r"\s+", " ", m.group(1).strip())
    candidate_norm = (
        candidate.lower()
        .replace("á", "a")
        .replace("é", "e")
        .replace("í", "i")
        .replace("ó", "o")
        .replace("ú", "u")
    )
    for alias in sorted(TYPE_ALIASES.keys(), key=len, reverse=True):
        if candidate_norm.startswith(alias):
            return TYPE_ALIASES[alias]
    return ""


def _best_tipo(rows: list[dict]) -> str | None:
    for r in rows:
        tipo = _clean_text(r.get("Tipo"))
        if tipo:
            return _normalize_type_name(tipo)
    for r in rows:
        tipo = _infer_tipo_from_text(_clean_text(r.get("Caso")))
        if tipo:
            return tipo
        tipo = _infer_tipo_from_text(_clean_text(r.get("Denominacion del caso")))
        if tipo:
            return tipo
        tipo = _infer_tipo_from_text(_clean_text(r.get("Denominación del caso")))
        if tipo:
            return tipo
    return None


def _best_inicio(rows: list[dict]) -> str | None:
    for r in rows:
        inicio = _clean_text(r.get("Ingreso como caso nuevo"))
        if inicio:
            return inicio
    return None


def _best_descripcion(rows: list[dict]) -> str | None:
    candidates: list[str] = []
    for r in rows:
        for key in ("Caso", "Denominacion del caso", "Denominación del caso"):
            text = _clean_text(r.get(key))
            if text:
                candidates.append(text)
    if not candidates:
        return None
    # Prefer rich text that is not just "Tipo <x>" prefix; fallback to longest.
    non_tipo_prefix = [c for c in candidates if not re.match(r"^\s*tipo\b", c, flags=re.IGNORECASE)]
    pool = non_tipo_prefix or candidates
    return max(pool, key=len)


def _best_actores(rows: list[dict]) -> str:
    best = ""
    for r in rows:
        value = " ".join(
            _clean_text(r.get(key))
            for key in ("Actores Primarios", "Actores secundarios", "Actores terciarios")
        ).strip()
        if len(value) > len(best):
            best = value
    return best


def build_timeline(records: list[dict]) -> tuple[list[dict], list[dict]]:
    occurrences: list[dict] = []
    grouped: dict[str, list[dict]] = {}

    for rec in records:
        uid = str(rec.get("conflict_uid") or "").strip()
        if not uid:
            continue

        source_pdf = str(rec.get("_source_pdf") or "")
        occurrence = dict(rec)
        occurrence["state"] = normalize_state(rec)
        occurrence["report_number"] = extract_report_number_from_name(source_pdf)
        occurrences.append(occurrence)
        grouped.setdefault(uid, []).append(occurrence)

    master: list[dict] = []
    for uid, rows in grouped.items():
        rows_sorted = sorted(
            rows,
            key=lambda r: (
                int(r.get("report_number") or -1),
                int(r.get("_page_start") or 0),
                int(r.get("_page_end") or 0),
                str(r.get("_case_id_local") or ""),
            ),
        )

        first = rows_sorted[0]
        last_report = max(int(r.get("report_number") or -1) for r in rows_sorted)
        last_rows = [r for r in rows_sorted if int(r.get("report_number") or -1) == last_report]
        current = max(last_rows, key=lambda r: state_priority(str(r.get("state") or "unknown")))

        master.append(
            {
                "conflict_uid": uid,
                "canonical_tipo": _best_tipo(rows_sorted),
                "canonical_inicio": _best_inicio(rows_sorted),
                "canonical_descripcion": _best_descripcion(rows_sorted),
                "canonical_actores": _best_actores(rows_sorted),
                "first_seen_pdf": first.get("_source_pdf"),
                "first_seen_report": int(first.get("report_number") or -1),
                "last_seen_pdf": current.get("_source_pdf"),
                "last_seen_report": int(current.get("report_number") or -1),
                "occurrences_count": len(rows_sorted),
                "current_state": current.get("state"),
            }
        )

    master.sort(key=lambda r: str(r.get("conflict_uid") or ""))
    occurrences.sort(
        key=lambda r: (
            str(r.get("conflict_uid") or ""),
            int(r.get("report_number") or -1),
            int(r.get("_page_start") or 0),
            str(r.get("_case_id_local") or ""),
        )
    )
    return master, occurrences


def filter_records_by_groups(records: list[dict], allowed_groups: set[str]) -> list[dict]:
    filtered: list[dict] = []
    for rec in records:
        group = str(rec.get("_grupo_conflicto") or "").strip().lower()
        if group in allowed_groups:
            filtered.append(rec)
    return filtered


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build unified conflict timeline dataset with current state.")
    parser.add_argument(
        "--input-json",
        type=Path,
        default=directories.ROOT_DIR / "conflictos_structured.json",
        help="Extracted records JSON path.",
    )
    parser.add_argument(
        "--registry-path",
        type=Path,
        default=directories.PROCESSED_DATA / "conflicts_registry.json",
    )
    parser.add_argument(
        "--occurrences-output",
        type=Path,
        default=directories.PROCESSED_DATA / "conflict_occurrences.json",
    )
    parser.add_argument(
        "--master-output",
        type=Path,
        default=directories.PROCESSED_DATA / "conflicts_master.json",
    )
    parser.add_argument("--high-threshold", type=float, default=0.82)
    parser.add_argument("--review-threshold", type=float, default=0.74)
    parser.add_argument(
        "--groups",
        type=str,
        default="activos,latentes",
        help="Comma-separated _grupo_conflicto values to include. Default: activos,latentes",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    records = json.loads(args.input_json.read_text(encoding="utf-8"))
    allowed_groups = {
        token.strip().lower()
        for token in str(args.groups or "").split(",")
        if token.strip()
    }
    if allowed_groups:
        records = filter_records_by_groups(records, allowed_groups)

    cfg = ScoreConfig(high_threshold=args.high_threshold, review_threshold=args.review_threshold)
    records_with_uid, registry = assign_conflict_ids(records, args.registry_path, cfg=cfg)
    master, occurrences = build_timeline(records_with_uid)

    save_json(args.registry_path, registry)
    save_json(args.occurrences_output, occurrences)
    save_json(args.master_output, master)

    print(
        f"records={len(records_with_uid)} | registry={len(registry)} | "
        f"master={len(master)} | occurrences={len(occurrences)}"
    )


if __name__ == "__main__":
    main()
