from __future__ import annotations

import argparse
import glob
import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import fitz  # PyMuPDF

from ..config import directories


def norm_spaces(s: str) -> str:
    s = s.replace("\xa0", " ")
    s = re.sub(r"[ \t]+\n", "\n", s)
    s = re.sub(r"\n[ \t]+", "\n", s)
    s = re.sub(r"[ \t]{2,}", " ", s)
    return s.strip()


def normalize_for_compare(s: str) -> str:
    s = unicodedata.normalize("NFD", s)
    s = "".join(ch for ch in s if unicodedata.category(ch) != "Mn")
    s = s.lower()
    s = re.sub(r"[^a-z0-9]+", "", s)
    return s


def normalize_heading(s: str) -> str:
    s = unicodedata.normalize("NFD", s)
    s = "".join(ch for ch in s if unicodedata.category(ch) != "Mn")
    s = s.upper()
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def drop_footer_noise(s: str) -> str:
    patterns = [
        r"Reporte Mensual de Conflictos Sociales.*?$",
        r"ADJUNT[ÍI]A PARA LA PREVENCI[ÓO]N.*?DEFENSOR[ÍI]A DEL PUEBLO\s*\d+\s*$",
        r"–\s*DEFENSOR[ÍI]A DEL PUEBLO\s*\d+\s*$",
    ]
    out = s
    for pattern in patterns:
        out = re.sub(pattern, "", out, flags=re.IGNORECASE | re.MULTILINE | re.DOTALL)
    return out.strip()


def extract_between(text: str, start_label: str, end_labels: list[str]) -> Optional[str]:
    start_idx = text.find(start_label)
    if start_idx == -1:
        return None

    start_idx += len(start_label)
    tail = text[start_idx:]
    end_positions = [tail.find(lbl) for lbl in end_labels]
    end_positions = [p for p in end_positions if p != -1]

    if not end_positions:
        return norm_spaces(tail)

    return norm_spaces(tail[: min(end_positions)])


def compact_paragraphs(text: str) -> str:
    lines = [ln.rstrip() for ln in text.splitlines()]

    paragraphs: list[list[str]] = []
    current: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            if current:
                paragraphs.append(current)
                current = []
            continue
        current.append(stripped)
    if current:
        paragraphs.append(current)

    output: list[str] = []
    for paragraph in paragraphs:
        units: list[str] = []
        idx = 0
        while idx < len(paragraph):
            piece = paragraph[idx]
            if piece in {"-", "•"} and idx + 1 < len(paragraph):
                units.append(f"- {paragraph[idx + 1]}")
                idx += 2
                continue
            units.append(piece)
            idx += 1
        output.append(" ".join(units))

    return "\n\n".join(output).strip()


def fragmented_text_ratio(text: str) -> float:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        return 0.0
    short = sum(1 for ln in lines if len(ln) <= 12 and " " not in ln)
    return short / len(lines)


def detect_dialogue_token(text: str) -> Optional[str]:
    compact = normalize_for_compare(text)
    no_idx = compact.find("nohaydialogo")
    yes_idx = compact.find("haydialogo")
    if no_idx != -1 and (yes_idx == -1 or no_idx <= yes_idx):
        return "NO HAY DIÁLOGO"
    if yes_idx != -1:
        return "HAY DIÁLOGO"
    return None


START_ROW = re.compile(r"^\s*Tipo:\s*", re.IGNORECASE)
COL_HDR = re.compile(r"^\s*Descripción\s*$|^\s*Hechos\s+del\s+mes\s*$", re.IGNORECASE)
HEADER_BLEED_RE = re.compile(r"ADJUNT[ÍI]A PARA LA PREVENCI[ÓO]N|DEFENSOR[ÍI]A DEL PUEBLO\s*\d+", re.IGNORECASE)
SECTION_TABLE_RE = re.compile(
    r"(6\.3\s+CONFLICTOS\s+ACTIVOS\s+QUE\s+NO\s+REGISTRARON\s+HECHOS\s+DURANTE\s+EL\s+MES|Fecha\s+de\s+inicio\s+Departamento\s+Denominaci[oó]n\s+del\s+caso)",
    re.IGNORECASE,
)
CASE_TYPE_ROW_RE = re.compile(
    r"\b(Socioambiental|Comunal|Demarcaci[oó]n\s+territorial|Asuntos\s+de\s+gobierno\s+nacional|Asuntos\s+de\s+gobierno\s+regional|Otros\s+asuntos)\b",
    re.IGNORECASE,
)
TABLE_ROW_START_RE = re.compile(
    r"\b[A-ZÁÉÍÓÚÑ][a-záéíóúñ-]{2,}(?:\s+[A-ZÁÉÍÓÚÑ]?[a-záéíóúñ-]{2,})?\s+"
    r"(Socioambiental|Comunal|Demarcaci[oó]n\s+territorial|Asuntos\s+de\s+gobierno\s+nacional|Asuntos\s+de\s+gobierno\s+regional|Otros\s+asuntos)\b",
    re.IGNORECASE,
)

DEPARTAMENTOS = {
    "Amazonas", "Áncash", "Apurímac", "Arequipa", "Ayacucho", "Cajamarca", "Callao",
    "Cusco", "Huancavelica", "Huánuco", "Ica", "Junín", "La Libertad", "Lambayeque",
    "Lima", "Loreto", "Madre de Dios", "Moquegua", "Pasco", "Piura", "Puno",
    "San Martín", "Tacna", "Tumbes", "Ucayali",
}


@dataclass
class Block:
    x0: float
    y0: float
    x1: float
    y1: float
    text: str


TOP_CONTINUATION_LIMIT = 240


def clean_field_value(text: Optional[str], compact: bool = True) -> Optional[str]:
    if text is None:
        return None
    out = norm_spaces(drop_footer_noise(text))
    out = re.sub(r"^\s*[:\-.]+\s*", "", out)
    # Remove section/table spillover that can be captured at the end of a case.
    section_match = SECTION_TABLE_RE.search(out)
    if section_match:
        out = out[: section_match.start()].strip()
    out = re.sub(r"\s+[.;:,]\s*$", ".", out)
    out = re.sub(r"\s+\.\s*$", ".", out)
    out = compact_paragraphs(out) if compact else out
    out = re.sub(r"\n{3,}", "\n\n", out).strip()
    return out or None


def clean_hechos_tail(text: Optional[str]) -> Optional[str]:
    if text is None:
        return None
    out = text
    section_match = SECTION_TABLE_RE.search(out)
    if section_match:
        out = out[: section_match.start()].strip()

    # Detect appended case-list rows at the end, e.g. "Carboneras Socioambiental ...".
    matches = list(CASE_TYPE_ROW_RE.finditer(out))
    if len(matches) >= 2:
        first = matches[0].start()
        second = matches[1].start()
        if first > int(0.55 * len(out)) and (second - first) < 120:
            cut = out.rfind(".", 0, first)
            if cut != -1:
                out = out[: cut + 1].strip()

    row_match = TABLE_ROW_START_RE.search(out)
    if row_match and row_match.start() > int(0.60 * len(out)):
        cut = out.rfind(".", 0, row_match.start())
        if cut != -1:
            out = out[: cut + 1].strip()
        else:
            out = out[: row_match.start()].strip()

    return out or None


def keep_only_first_case_block(left_txt: str) -> str:
    starts = [m.start() for m in START_ROW.finditer(left_txt)]
    if len(starts) <= 1:
        return left_txt
    return left_txt[starts[0] : starts[1]].strip()


def trim_left_at_next_header(left_raw: str) -> str:
    lines = [ln.rstrip() for ln in left_raw.splitlines()]
    kept: list[str] = []
    prev_non_empty = ""
    saw_label = False

    label_probe = re.compile(
        r"^\s*(Tipo|Ingres[oó]\s+como\s+caso\s+nuevo|Caso|Ubicaci[oó]n|Actores?\s+primarios?|Actores?\s+secundarios?|Actores?\s+terciarios?)\s*:",
        re.IGNORECASE,
    )

    for line in lines:
        token = line.strip()
        if not token:
            kept.append(line)
            continue

        if label_probe.search(token):
            saw_label = True

        if token in DEPARTAMENTOS and saw_label:
            # Keep true location wraps: "... departamento de|del" + "<Departamento>"
            if prev_non_empty.lower().endswith("departamento de") or prev_non_empty.lower().endswith("departamento del"):
                kept.append(line)
                prev_non_empty = token
                continue
            break

        if COL_HDR.match(token):
            break
        if re.match(r"^\s*CASO\s+(NUEVO|REACTIVADO)\s*$", token, re.IGNORECASE):
            break
        if SECTION_TABLE_RE.search(token):
            break

        kept.append(line)
        prev_non_empty = token

    return "\n".join(kept).strip()


def parse_case_left(left_text: str) -> dict[str, Optional[str]]:
    left_text = norm_spaces(drop_footer_noise(left_text))
    left_text = keep_only_first_case_block(left_text)
    left_text = trim_left_at_next_header(left_text)

    # Labels usually start a line, but some reports omit ":" after Ubicación.
    label_patterns = [
        ("Tipo", re.compile(r"(?:^|\n)\s*Tipo\s*:?", re.IGNORECASE)),
        ("Ingreso como caso nuevo", re.compile(r"(?:^|\n)\s*Ingres[oó]\s+como\s+caso\s+nuevo\s*:?", re.IGNORECASE)),
        ("Caso", re.compile(r"(?:^|\n)\s*Caso\s*:?", re.IGNORECASE)),
        ("Ubicacion", re.compile(r"(?:^|\n)\s*Ubicaci[oó]n\s*:?", re.IGNORECASE)),
        ("Actores Primarios", re.compile(r"(?:^|\n)\s*Actores?\s+primarios?\s*:?", re.IGNORECASE)),
        ("Actores secundarios", re.compile(r"(?:^|\n)\s*Actores?\s+secundarios?\s*:?", re.IGNORECASE)),
        ("Actores terciarios", re.compile(r"(?:^|\n)\s*Actores?\s+terciarios?\s*:?", re.IGNORECASE)),
    ]

    found: list[tuple[int, int, str]] = []
    for field_name, pattern in label_patterns:
        for match in pattern.finditer(left_text):
            found.append((match.start(), match.end(), field_name))

    found.sort(key=lambda x: x[0])
    parsed = {
        "Tipo": None,
        "Ingreso como caso nuevo": None,
        "Caso": None,
        "Ubicacion": None,
        "Actores Primarios": None,
        "Actores secundarios": None,
        "Actores terciarios": None,
    }

    for idx, (start, end, field_name) in enumerate(found):
        if parsed[field_name] is not None:
            continue
        next_start = found[idx + 1][0] if idx + 1 < len(found) else len(left_text)
        parsed[field_name] = left_text[end:next_start]

    for key in list(parsed.keys()):
        parsed[key] = clean_field_value(parsed[key], compact=False)

    # Defensive split when OCR merges "Actores secundarios" into primarios field.
    ap = parsed.get("Actores Primarios")
    if ap:
        m = re.search(r"\bActores?\s+secundarios?\s*:\s*", ap, re.IGNORECASE)
        if m:
            prim = ap[: m.start()].strip()
            sec = ap[m.end() :].strip()
            parsed["Actores Primarios"] = clean_field_value(prim, compact=False)
            if not parsed.get("Actores secundarios"):
                parsed["Actores secundarios"] = clean_field_value(sec, compact=False)

    # Defensive split when Ubicación was merged into Caso due missing ":".
    caso = parsed.get("Caso")
    if caso and not parsed.get("Ubicacion"):
        m = re.search(r"\bUbicaci[oó]n\b\s*:?", caso, re.IGNORECASE)
        if m:
            parsed["Caso"] = clean_field_value(caso[: m.start()], compact=False)
            parsed["Ubicacion"] = clean_field_value(caso[m.end() :], compact=False)

    for key in list(parsed.keys()):
        parsed[key] = clean_field_value(parsed[key], compact=True)
        if parsed[key] is not None and key != "Caso":
            parsed[key] = re.sub(r"\s*\n+\s*", " ", parsed[key]).strip()

    return parsed


def detect_dialogue_status(header_text: str, full_text: str) -> tuple[Optional[str], str]:
    full = norm_spaces(drop_footer_noise(full_text))
    # Use only top-of-case body text; header snippet can include overlapping
    # blocks from the next case on dense pages.
    search_text = full[:900].strip()
    no_pat = re.compile(r"\bNO\s+HAY\s+DI[ÁA]LOGO\b", re.IGNORECASE)
    yes_pat = re.compile(r"\bHAY\s+DI[ÁA]LOGO\b", re.IGNORECASE)
    status_pat = re.compile(r"\b(NO\s+HAY\s+DI[ÁA]LOGO|HAY\s+DI[ÁA]LOGO)\b", re.IGNORECASE)

    status: Optional[str] = detect_dialogue_token(search_text)
    first = status_pat.search(search_text)
    if status is None and first:
        token = first.group(1).upper().replace("Á", "A")
        status = "NO HAY DIÁLOGO" if token.startswith("NO HAY DIALOGO") else "HAY DIÁLOGO"
    if status is None:
        full_probe = full[:2400]
        if re.search(r"\b(mesa\s+de\s+(di[áa]logo|trabajo)|se\s+realiz[oó]\s+una\s+reuni[oó]n|se\s+establecieron\s+los\s+siguientes\s+acuerdos|acordaron\s+que)\b", full_probe, re.IGNORECASE):
            status = "HAY DIÁLOGO"

    full = re.sub(no_pat, "", full).strip()
    full = re.sub(yes_pat, "", full).strip()
    full = re.sub(r"^\bNO\b\s*", "", full, flags=re.IGNORECASE).strip()

    return status, clean_field_value(full) or ""


def detect_dialogue_status_fallback(page_text: str) -> Optional[str]:
    normalized = norm_spaces(drop_footer_noise(page_text))
    status = detect_dialogue_token(normalized)
    if status is not None:
        return status
    status_pat = re.compile(r"\b(NO\s+HAY\s+DI[ÁA]LOGO|HAY\s+DI[ÁA]LOGO)\b", re.IGNORECASE)
    first = status_pat.search(normalized)
    if first:
        token = first.group(1).upper().replace("Á", "A")
        return "NO HAY DIÁLOGO" if token.startswith("NO HAY DIALOGO") else "HAY DIÁLOGO"
    return None


def page_blocks(page: fitz.Page) -> list[Block]:
    blocks: list[Block] = []
    for block in page.get_text("blocks"):
        x0, y0, x1, y1, text = block[:5]
        text = text.strip()
        if not text:
            continue
        blocks.append(Block(float(x0), float(y0), float(x1), float(y1), text))
    return blocks


def split_columns(blocks: list[Block], x_split: float) -> tuple[list[Block], list[Block]]:
    left = sorted((b for b in blocks if b.x0 < x_split), key=lambda b: (b.y0, b.x0))
    right = sorted((b for b in blocks if b.x0 >= x_split), key=lambda b: (b.y0, b.x0))
    return left, right


def looks_like_section_title(page_text: str, roman: str, keywords: list[str]) -> bool:
    normalized = normalize_heading(page_text)
    if f"{roman}." not in normalized and f" {roman} " not in f" {normalized} ":
        return False
    return all(keyword in normalized for keyword in keywords)


def find_section_page_ranges(doc: fitz.Document) -> dict[str, tuple[int, int]]:
    starts: dict[str, int] = {}

    for idx in range(doc.page_count):
        text = doc.load_page(idx).get_text("text")
        if "VI" not in starts and looks_like_section_title(text, "VI", ["DETALLE", "CONFLICTOS", "ACTIVOS"]):
            starts["VI"] = idx
        if "VII" not in starts and looks_like_section_title(text, "VII", ["DETALLE", "CONFLICTOS", "LATENTES"]):
            starts["VII"] = idx
        if "VIII" not in starts and looks_like_section_title(text, "VIII", ["CASOS", "SALIERON", "REPORTE"]):
            starts["VIII"] = idx

    if not starts:
        return {"VI": (0, doc.page_count - 1)}

    order = [key for key in ["VI", "VII", "VIII"] if key in starts]
    ranges: dict[str, tuple[int, int]] = {}
    for i, sec in enumerate(order):
        start = starts[sec]
        if i + 1 < len(order):
            end = starts[order[i + 1]] - 1
        else:
            end = doc.page_count - 1
        ranges[sec] = (start, end)

    return ranges


def next_page_starts_new_case(left_blocks: list[Block]) -> bool:
    for block in left_blocks:
        if block.y0 > TOP_CONTINUATION_LIMIT:
            break
        if re.match(r"^\s*Tipo:\s*", block.text):
            return True
    return False


def get_top_continuation(blocks: list[Block]) -> str:
    chunks: list[str] = []
    for block in blocks:
        if block.y0 > TOP_CONTINUATION_LIMIT:
            break

        text = block.text.strip()
        if not text:
            continue
        if COL_HDR.match(text):
            continue
        if text.startswith("Reporte Mensual de Conflictos Sociales"):
            continue
        if "ADJUNTÍA PARA LA PREVENCIÓN" in text:
            continue

        chunks.append(block.text)

    return "\n".join(chunks).strip()


def get_left_continuation_until_next_tipo(left_blocks: list[Block]) -> str:
    """
    Capture top-of-page continuation for a split row.

    This keeps text before the first next-case marker (Tipo:), allowing fields
    such as 'Actores secundarios/terciarios' to continue on the next page.
    """
    chunks: list[str] = []
    saw_content = False
    for block in left_blocks:
        if block.y0 > TOP_CONTINUATION_LIMIT:
            break

        text = block.text.strip()
        if not text:
            continue
        if text.startswith("Reporte Mensual de Conflictos Sociales"):
            continue
        if "ADJUNTÍA PARA LA PREVENCIÓN" in text:
            continue
        if COL_HDR.match(text):
            continue

        if re.match(r"^\s*Tipo:\s*", text):
            if saw_content:
                break
            return ""

        saw_content = True
        chunks.append(block.text)

    return "\n".join(chunks).strip()


def score_confidence(case: dict[str, Optional[str]]) -> float:
    score = 1.0
    required = ["Tipo", "Caso", "Ubicacion", "Hechos del Mes"]
    for field in required:
        if not case.get(field):
            score -= 0.2

    haystack = " ".join(str(case.get(field) or "") for field in ["Caso", "Actores secundarios", "Actores terciarios"])
    if HEADER_BLEED_RE.search(haystack):
        score -= 0.2

    if case.get("_dialogo") is None:
        score -= 0.1

    if fragmented_text_ratio(case.get("Hechos del Mes") or "") > 0.2:
        score -= 0.15

    return round(max(0.0, score), 3)


def infer_dialogue_from_hechos(hechos: Optional[str]) -> Optional[str]:
    if not hechos:
        return "NO HAY DIÁLOGO"
    neg_dialogue = re.search(
        r"\b(no\s+se\s+(pudo|ha\s+podido|logr[oó]|concret[oó]|estableci[oó])\s+(establecer\s+)?(un\s+)?(espacio|mesa|proceso|canal)?\s*de\s*di[áa]logo|no\s+hay\s+di[áa]logo|sin\s+di[áa]logo|no\s+fue\s+posible\s+.*di[áa]logo)\b",
        hechos,
        re.IGNORECASE,
    )
    if neg_dialogue:
        return "NO HAY DIÁLOGO"
    if re.search(
        r"\b(se\s+realiz[oó]\s+una\s+reuni[oó]n|convoc[oó]\s+a\s+una\s+reuni[oó]n|mesa\s+de\s+(trabajo|di[áa]logo)|se\s+establecieron\s+los\s+siguientes\s+acuerdos|acordaron\s+que)\b",
        hechos,
        re.IGNORECASE,
    ):
        return "HAY DIÁLOGO"
    if re.search(r"\breuniones?\s+t[eé]cnicas?\b", hechos, re.IGNORECASE) and re.search(
        r"\bse\s+acord[oó]\s+que\b", hechos, re.IGNORECASE
    ):
        return "HAY DIÁLOGO"
    # Conservative default to avoid null status.
    return "NO HAY DIÁLOGO"


def is_non_case_table_page(page_text: str) -> bool:
    return bool(SECTION_TABLE_RE.search(norm_spaces(page_text)))


def finalize_case(current_case: dict, doc: fitz.Document, results: list[dict]) -> None:
    parsed = parse_case_left(current_case["_left_raw"])
    status, hechos = detect_dialogue_status(current_case["_header_snippet"], current_case["_right_raw"])
    parsed["Hechos del Mes"] = hechos or None
    if status is None:
        status = infer_dialogue_from_hechos(parsed["Hechos del Mes"])
    parsed["_dialogo"] = status
    parsed["_page_start"] = current_case["_page_start"]
    parsed["_page_end"] = current_case["_page_end"]
    results.append(parsed)


def extract_cases_from_range(doc: fitz.Document, start: int, end: int, x_split: float = 300.0) -> list[dict]:
    results: list[dict] = []
    current_case: Optional[dict] = None

    for pno in range(start, end + 1):
        page = doc.load_page(pno)
        page_text = page.get_text("text")
        blocks = page_blocks(page)
        left, right = split_columns(blocks, x_split=x_split)

        left_texts = [(b.y0, b.y1, b.text) for b in left]
        tipo_indices = [i for i, (_, _, text) in enumerate(left_texts) if re.match(r"^\s*Tipo:\s*", text)]

        if not tipo_indices:
            if current_case is not None:
                # Stop continuation when next page is a section/table listing page.
                if is_non_case_table_page(page_text):
                    finalize_case(current_case, doc, results)
                    current_case = None
                    continue
                current_case["_left_raw"] += "\n" + "\n".join(t for _, _, t in left_texts)
                current_case["_right_raw"] += "\n" + get_top_continuation(right)
                current_case["_page_end"] = pno + 1
            continue

        for j, idx in enumerate(tipo_indices):
            y0 = left_texts[idx][0]
            y1 = left_texts[tipo_indices[j + 1]][0] if j + 1 < len(tipo_indices) else 99999.0

            if current_case is not None:
                finalize_case(current_case, doc, results)
                current_case = None

            win_left = [text for (yy0, _, text) in left_texts if yy0 >= y0 - 0.5 and yy0 < y1 - 0.5]
            left_raw = "\n".join(win_left)

            win_right_blocks = [b for b in right if not (b.y1 < y0 - 2 or b.y0 > y1 + 2)]
            win_right_blocks.sort(key=lambda b: (b.y0, b.x0))

            header_snippet = "\n".join(b.text for b in win_right_blocks[:8])
            right_raw = "\n".join(b.text for b in win_right_blocks)

            current_case = {
                "_left_raw": left_raw,
                "_right_raw": right_raw,
                "_header_snippet": header_snippet,
                "_page_start": pno + 1,
                "_page_end": pno + 1,
            }

        if current_case is not None and pno < end:
            next_page = doc.load_page(pno + 1)
            next_blocks = page_blocks(next_page)
            next_left, next_right = split_columns(next_blocks, x_split=x_split)

            left_cont = get_left_continuation_until_next_tipo(next_left)
            right_cont = get_top_continuation(next_right) if not next_page_starts_new_case(next_left) else ""

            if left_cont:
                current_case["_left_raw"] += "\n" + left_cont
                current_case["_page_end"] = pno + 2
            elif right_cont:
                current_case["_page_end"] = pno + 2

            if right_cont:
                current_case["_right_raw"] += "\n" + right_cont

    if current_case is not None:
        finalize_case(current_case, doc, results)

    for row in results:
        if row.get("Tipo"):
            row["Tipo"] = row["Tipo"].rstrip(".").strip()
        row["Hechos del Mes"] = clean_hechos_tail(clean_field_value(row.get("Hechos del Mes"), compact=True))
        row["_confidence"] = score_confidence(row)

    return results


def extract_report_number(path: Path) -> int:
    match = re.search(r"(\d{2,4})", path.name)
    if not match:
        return -1
    return int(match.group(1))


def choose_x_split(pdf_path: Path, default: float) -> float:
    report_number = extract_report_number(pdf_path)
    if report_number != -1 and report_number < 100:
        return 285.0
    return default


def extract_from_pdf(pdf_path: Path, x_split: float = 300.0) -> list[dict]:
    doc = fitz.open(pdf_path)
    ranges = find_section_page_ranges(doc)
    resolved_x_split = choose_x_split(pdf_path, x_split)

    all_results: list[dict] = []
    per_page_case_counter: dict[tuple[int, int], int] = {}

    for section in ["VI", "VII", "VIII"]:
        if section not in ranges:
            continue
        start, end = ranges[section]
        section_cases = extract_cases_from_range(doc, start, end, x_split=resolved_x_split)

        for case in section_cases:
            case["_seccion"] = section
            case["_source_pdf"] = pdf_path.name
            page_key = (case["_page_start"], case["_page_end"])
            per_page_case_counter[page_key] = per_page_case_counter.get(page_key, 0) + 1
            case["_case_id"] = (
                f"{pdf_path.stem}:{case['_page_start']}-{case['_page_end']}:"
                f"{per_page_case_counter[page_key]}"
            )
        all_results.extend(section_cases)

    doc.close()
    return all_results


def field_contains_source(field_value: str, source_text: str) -> bool:
    left = normalize_for_compare(field_value)
    if len(left) < 40:
        return True
    left = left[:260]
    right = normalize_for_compare(source_text)
    if left[:120] in right:
        return True
    chunks = [left[i : i + 50] for i in range(0, min(len(left), 220), 25)]
    hits = sum(1 for chunk in chunks if len(chunk) >= 35 and chunk in right)
    return hits >= 2


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
            file_hash = file_sha256(path)
        except OSError:
            continue
        if file_hash in seen_hashes:
            continue
        seen_hashes.add(file_hash)
        unique.append(path)
    return unique


def generate_qa_report(cases: list[dict], docs_dir: Path, previous_report: Optional[dict]) -> dict:
    per_type: dict[str, int] = {}
    per_case: list[dict] = []

    grouped: dict[str, list[dict]] = {}
    for case in cases:
        grouped.setdefault(case["_source_pdf"], []).append(case)

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
                if not value:
                    continue
                if HEADER_BLEED_RE.search(value):
                    issues.append("header_bleed_in_fields")
                    break

            if re.match(r"^\s*:\s*", str(case.get("Caso") or "")):
                issues.append("field_label_artifact")

            if fragmented_text_ratio(case.get("Hechos del Mes") or "") > 0.2:
                issues.append("fragmented_hechos")

            if not case.get("_dialogo"):
                issues.append("dialogue_status_missing")

            for field_name in ["Caso", "Hechos del Mes"]:
                value = case.get(field_name)
                if value and not field_contains_source(value, source_text):
                    issues.append(f"{field_name.lower().replace(' ', '_')}_not_in_source")

            if issues:
                unique_issues = sorted(set(issues))
                for issue in unique_issues:
                    per_type[issue] = per_type.get(issue, 0) + 1
                per_case.append(
                    {
                        "case_id": case.get("_case_id"),
                        "source_pdf": case.get("_source_pdf"),
                        "page_start": case.get("_page_start"),
                        "page_end": case.get("_page_end"),
                        "issues": unique_issues,
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

    cases = report.get("issues_by_case") or []
    if not cases:
        lines.append("- No flagged cases")
    else:
        for item in cases:
            lines.append(
                f"- {item['case_id']} | {item['source_pdf']} p{item['page_start']}-{item['page_end']} | "
                f"{', '.join(item['issues'])}"
            )

    lines.append("")
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Extract social conflict cases from PDF reports.")
    parser.add_argument(
        "--input-glob",
        default=str(directories.DOCUMENTS / "*.pdf*"),
        help="Glob pattern for input PDFs.",
    )
    parser.add_argument(
        "--output-json",
        type=Path,
        default=directories.ROOT_DIR / "conflictos_structured.json",
        help="Output JSON path.",
    )
    parser.add_argument(
        "--qa-report-json",
        type=Path,
        default=directories.LOGS / "extract_text_qa_report.json",
        help="QA report JSON path.",
    )
    parser.add_argument(
        "--qa-report-md",
        type=Path,
        default=directories.LOGS / "extract_text_qa_report.md",
        help="QA report Markdown path.",
    )
    parser.add_argument("--x-split", type=float, default=300.0, help="Column split x-coordinate.")
    parser.add_argument(
        "--fail-on-threshold",
        type=int,
        default=None,
        help="Exit with code 1 when cases_with_issues exceeds this number.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    pdf_paths = sorted((Path(p) for p in glob.glob(args.input_glob)), key=extract_report_number)
    pdf_paths = unique_pdf_paths(pdf_paths)
    if not pdf_paths:
        raise FileNotFoundError(f"No PDF files matched pattern: {args.input_glob}")

    all_results: list[dict] = []
    for pdf_path in pdf_paths:
        try:
            all_results.extend(extract_from_pdf(pdf_path=pdf_path, x_split=args.x_split))
        except Exception as exc:
            print(f"[WARN] Failed to parse {pdf_path.name}: {exc}")

    all_results.sort(key=lambda row: (str(row.get("_source_pdf")), int(row.get("_page_start") or 0), str(row.get("_case_id"))))

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

    print(f"Extracted {len(all_results)} cases into {args.output_json}")
    print(f"QA report JSON: {args.qa_report_json}")
    print(f"QA report Markdown: {args.qa_report_md}")

    if args.fail_on_threshold is not None and qa_report["summary"]["cases_with_issues"] > args.fail_on_threshold:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
