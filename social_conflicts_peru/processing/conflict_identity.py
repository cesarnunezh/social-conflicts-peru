from __future__ import annotations

import argparse
import json
import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path

from ..config import directories

@dataclass
class ScoreConfig:
    high_threshold: float = 0.82
    review_threshold: float = 0.74
    w_tipo: float = 0.20
    w_inicio: float = 0.20
    w_caso: float = 0.40
    w_actores: float = 0.20


def normalize_text(s: str | None) -> str:
    if not s:
        return ""
    s = unicodedata.normalize("NFD", s)
    s = "".join(ch for ch in s if unicodedata.category(ch) != "Mn")
    s = s.lower()
    s = re.sub(r"\s+", " ", s).strip()
    s = re.sub(r"[^a-z0-9 ]+", "", s)
    return s


def similarity(a: str, b: str) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def build_features(record: dict) -> dict[str, str]:
    tipo = str(record.get("Tipo") or "").strip()
    inicio = str(record.get("Ingreso como caso nuevo") or "").strip()
    caso = str(record.get("Caso") or "").strip()
    actores = " ".join(
        str(record.get(key) or "")
        for key in ["Actores Primarios", "Actores secundarios", "Actores terciarios"]
    ).strip()
    return {
        "tipo_norm": normalize_text(tipo),
        "inicio_norm": normalize_text(inicio),
        "caso_norm": normalize_text(caso),
        "actores_norm": normalize_text(actores),
    }


def score_pair(a: dict[str, str], b: dict[str, str], cfg: ScoreConfig) -> float:
    st = similarity(a["tipo_norm"], b["tipo_norm"])
    si = similarity(a["inicio_norm"], b["inicio_norm"])
    sc = similarity(a["caso_norm"], b["caso_norm"])
    sa = similarity(a.get("actores_norm", ""), b.get("actores_norm", ""))
    return cfg.w_tipo * st + cfg.w_inicio * si + cfg.w_caso * sc + cfg.w_actores * sa


def load_registry(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


def save_json(path: Path, obj: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def next_uid(registry: list[dict]) -> str:
    max_n = 0
    for item in registry:
        uid = str(item.get("conflict_uid") or "")
        m = re.match(r"^CF-(\d+)$", uid)
        if m:
            max_n = max(max_n, int(m.group(1)))
    return f"CF-{max_n + 1:07d}"


def assign_conflict_ids(records: list[dict], registry_path: Path, cfg: ScoreConfig | None = None) -> tuple[list[dict], list[dict]]:
    cfg = cfg or ScoreConfig()
    registry = load_registry(registry_path)

    # Ensure deterministic ordering
    records = sorted(
        records,
        key=lambda r: (
            str(r.get("_source_pdf") or ""),
            int(r.get("_page_start") or 0),
            str(r.get("_case_id_local") or ""),
        ),
    )

    for rec in records:
        features = build_features(rec)

        best_score = -1.0
        best_idx = -1
        for idx, reg in enumerate(registry):
            reg_features = {
                "tipo_norm": reg.get("tipo_norm", ""),
                "inicio_norm": reg.get("inicio_norm", ""),
                "caso_norm": reg.get("caso_norm", ""),
                "actores_norm": reg.get("actores_norm", ""),
            }
            s = score_pair(features, reg_features, cfg)
            if s > best_score:
                best_score = s
                best_idx = idx

        link_existing = best_idx >= 0 and best_score >= cfg.review_threshold

        if link_existing:
            reg = registry[best_idx]
            rec["conflict_uid"] = reg["conflict_uid"]
            reg["last_seen_pdf"] = rec.get("_source_pdf")
            reg["occurrences"] = int(reg.get("occurrences", 0)) + 1

            # If very strong match, allow canonical text refresh when longer.
            if best_score >= cfg.high_threshold and len(features["caso_norm"]) > len(reg.get("caso_norm", "")):
                reg["caso_norm"] = features["caso_norm"]
                reg["canonical_caso"] = rec.get("Caso")
            if best_score >= cfg.high_threshold and len(features["actores_norm"]) > len(reg.get("actores_norm", "")):
                reg["actores_norm"] = features["actores_norm"]
                reg["canonical_actores"] = " ".join(
                    str(rec.get(key) or "")
                    for key in ["Actores Primarios", "Actores secundarios", "Actores terciarios"]
                ).strip()
        else:
            uid = next_uid(registry)
            rec["conflict_uid"] = uid
            registry.append(
                {
                    "conflict_uid": uid,
                    "tipo_norm": features["tipo_norm"],
                    "inicio_norm": features["inicio_norm"],
                    "caso_norm": features["caso_norm"],
                    "actores_norm": features["actores_norm"],
                    "canonical_tipo": rec.get("Tipo"),
                    "canonical_inicio": rec.get("Ingreso como caso nuevo"),
                    "canonical_caso": rec.get("Caso"),
                    "canonical_actores": " ".join(
                        str(rec.get(key) or "")
                        for key in ["Actores Primarios", "Actores secundarios", "Actores terciarios"]
                    ).strip(),
                    "first_seen_pdf": rec.get("_source_pdf"),
                    "last_seen_pdf": rec.get("_source_pdf"),
                    "occurrences": 1,
                }
            )

    return records, registry


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Assign stable conflict IDs from extracted JSON.")
    parser.add_argument("--input-json", type=Path, required=True)
    parser.add_argument(
        "--registry-path",
        type=Path,
        default=directories.PROCESSED_DATA / "conflicts_registry.json",
    )
    parser.add_argument(
        "--occurrences-path",
        type=Path,
        default=directories.PROCESSED_DATA / "conflicts_occurrences.json",
    )
    parser.add_argument("--high-threshold", type=float, default=0.82)
    parser.add_argument("--review-threshold", type=float, default=0.74)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    records = json.loads(args.input_json.read_text(encoding="utf-8"))

    cfg = ScoreConfig(high_threshold=args.high_threshold, review_threshold=args.review_threshold)
    records_with_uid, registry = assign_conflict_ids(records, args.registry_path, cfg=cfg)

    save_json(args.registry_path, registry)
    save_json(args.occurrences_path, records_with_uid)

    print(f"records={len(records_with_uid)} | registry={len(registry)}")


if __name__ == "__main__":
    main()
