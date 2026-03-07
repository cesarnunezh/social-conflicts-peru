from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import fitz


# -----------------------------
# Constants
# -----------------------------

TOP_CONTINUATION_LIMIT = 240

TIPO_OPTIONS = [
    "Socioambiental",
    "Comunal",
    "Demarcación territorial",
    "Asuntos de gobierno nacional",
    "Asuntos de gobierno local",
    "Asuntos de gobierno regional",
    "Otros asuntos",
    "Cultivo ilegal de coca",
]

SECTION_PATTERNS = {
    "VI": ["VI", "DETALLE", "CONFLICTOS", "ACTIVOS"],
    "VII": ["VII", "DETALLE", "CONFLICTOS", "LATENTES"],
    "VIII": ["VIII", "CASOS", "SALIERON", "REPORTE"],
    "IX": ["IX", "OTROS", "INDICADORES"],
}

SUBSECTION_PATTERNS = {
    "6.3": ["6.3", "CONFLICTOS ACTIVOS", "NO REGISTRARON", "HECHOS DURANTE EL MES"],
    "6.4": ["6.4", "CONFLICTOS REACTIVADOS"],
    "7.1": ["7.1", "HAN PASADO", "ACTIVO", "LATENTE"],
    "8.1": ["8.1", "CONFLICTOS RESUELTOS"],
    "8.2": ["8.2", "LATENTE", "INACTIVIDAD PROLONGADA"],
}

COL_HDR = re.compile(r"^\s*Descripción\s*$|^\s*Hechos\s+del\s+mes\s*$", re.IGNORECASE)
START_ROW = re.compile(r"^\s*Tipo:\s*", re.IGNORECASE)
HEADER_BLEED_RE = re.compile(r"ADJUNT[ÍI]A PARA LA PREVENCI[ÓO]N|DEFENSOR[ÍI]A DEL PUEBLO\s*\d+", re.IGNORECASE)
SECTION_TABLE_RE = re.compile(
    r"(6\.3\s+CONFLICTOS\s+ACTIVOS\s+QUE\s+NO\s+REGISTRARON\s+HECHOS\s+DURANTE\s+EL\s+MES|"
    r"6\.4\s+CONFLICTOS\s+REACTIVADOS|"
    r"7\.1\s+CONFLICTOS\s+QUE\s+HAN\s+PASADO|"
    r"8\.1\s+CONFLICTOS\s+RESUELTOS|"
    r"8\.2\s+CONFLICTOS\s+EN\s+ESTADO\s+LATENTE|"
    r"Fecha\s+de\s+inicio\s+Departamento\s+Denominaci[oó]n\s+del\s+caso)",
    re.IGNORECASE,
)

TABLE_ROW_START_RE = re.compile(
    r"\b[A-ZÁÉÍÓÚÑ][a-záéíóúñ-]{2,}(?:\s+[A-ZÁÉÍÓÚÑ]?[a-záéíóúñ-]{2,})?\s+"
    r"(Socioambiental|Comunal|Demarcaci[oó]n\s+territorial|Asuntos\s+de\s+gobierno\s+nacional|"
    r"Asuntos\s+de\s+gobierno\s+regional|Otros\s+asuntos)\b",
    re.IGNORECASE,
)

DEPARTAMENTOS = {
    "Amazonas", "Áncash", "Apurímac", "Arequipa", "Ayacucho", "Cajamarca", "Callao",
    "Cusco", "Huancavelica", "Huánuco", "Ica", "Junín", "La Libertad", "Lambayeque",
    "Lima", "Loreto", "Madre de Dios", "Moquegua", "Pasco", "Piura", "Puno",
    "San Martín", "Tacna", "Tumbes", "Ucayali",
}

DEPARTAMENTOS_EXTENDED = sorted(
    DEPARTAMENTOS
    | {
        "Lima Metropolitana",
        "Multirregional",
    },
    key=len,
    reverse=True,
)


@dataclass
class Block:
    x0: float
    y0: float
    x1: float
    y1: float
    text: str


# -----------------------------
# Text normalization
# -----------------------------

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


def heading_contains(text: str, probe: str) -> bool:
    return normalize_heading(probe) in normalize_heading(text)


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


def clean_field_value(text: Optional[str], compact: bool = True) -> Optional[str]:
    if text is None:
        return None
    out = norm_spaces(drop_footer_noise(text))
    out = re.sub(r"^\s*[:\-.]+\s*", "", out)
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

    row_match = TABLE_ROW_START_RE.search(out)
    if row_match and row_match.start() > int(0.60 * len(out)):
        cut = out.rfind(".", 0, row_match.start())
        out = out[: cut + 1].strip() if cut != -1 else out[: row_match.start()].strip()

    return out or None


# -----------------------------
# PDF helpers
# -----------------------------

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


def looks_like_heading(page_text: str, tokens: list[str]) -> bool:
    normalized = normalize_heading(page_text)
    return all(token in normalized for token in tokens)


def find_section_page_ranges(doc: fitz.Document) -> dict[str, tuple[int, int]]:
    starts: dict[str, int] = {}
    for idx in range(doc.page_count):
        text = doc.load_page(idx).get_text("text")
        for sec, tokens in SECTION_PATTERNS.items():
            if sec not in starts and looks_like_heading(text, tokens):
                starts[sec] = idx

    if not starts:
        return {"VI": (0, doc.page_count - 1)}

    order = [sec for sec in ["VI", "VII", "VIII"] if sec in starts]
    ranges: dict[str, tuple[int, int]] = {}
    for i, sec in enumerate(order):
        start = starts[sec]
        # Keep boundary pages inclusive because section headings can appear mid-page.
        end = starts[order[i + 1]] if i + 1 < len(order) else doc.page_count - 1
        ranges[sec] = (start, end)

    # Do not parse beyond IX. OTROS INDICADORES when present.
    if "VIII" in ranges and "IX" in starts and starts["IX"] > ranges["VIII"][0]:
        vstart, vend = ranges["VIII"]
        ranges["VIII"] = (vstart, min(vend, starts["IX"] - 1))
    return ranges


def find_subsection_starts(doc: fitz.Document, start: int, end: int) -> dict[str, int]:
    out: dict[str, int] = {}
    for pno in range(start, end + 1):
        text = doc.load_page(pno).get_text("text")
        for key, tokens in SUBSECTION_PATTERNS.items():
            if key not in out and looks_like_heading(text, tokens):
                out[key] = pno
    return out


def range_between(start: int, end: int, next_start: Optional[int]) -> tuple[int, int]:
    if next_start is None:
        return start, end
    return start, max(start, next_start - 1)


# -----------------------------
# Detailed case parser (VI/VII style)
# -----------------------------

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
        r"^\s*(Tipo|Ingres[oó]\s+como\s+caso\s+nuevo|Caso|Ubicaci[oó]n|Actores?\s+primarios?|Actores?\s+secundarios?|Actores?\s+terciarios?)\s*:?",
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

    for idx, (_, end, field_name) in enumerate(found):
        if parsed[field_name] is not None:
            continue
        next_start = found[idx + 1][0] if idx + 1 < len(found) else len(left_text)
        parsed[field_name] = left_text[end:next_start]

    for key in list(parsed.keys()):
        parsed[key] = clean_field_value(parsed[key], compact=False)

    ap = parsed.get("Actores Primarios")
    if ap:
        m = re.search(r"\bActores?\s+secundarios?\s*:\s*", ap, re.IGNORECASE)
        if m:
            prim = ap[: m.start()].strip()
            sec = ap[m.end() :].strip()
            parsed["Actores Primarios"] = clean_field_value(prim, compact=False)
            if not parsed.get("Actores secundarios"):
                parsed["Actores secundarios"] = clean_field_value(sec, compact=False)

    caso = parsed.get("Caso")
    if caso and not parsed.get("Ubicacion"):
        m = re.search(r"\bUbicaci[oó]n\b\s*:?,?", caso, re.IGNORECASE)
        if m:
            parsed["Caso"] = clean_field_value(caso[: m.start()], compact=False)
            parsed["Ubicacion"] = clean_field_value(caso[m.end() :], compact=False)

    for key in list(parsed.keys()):
        parsed[key] = clean_field_value(parsed[key], compact=True)
        if parsed[key] is not None and key != "Caso":
            parsed[key] = re.sub(r"\s*\n+\s*", " ", parsed[key]).strip()

    return parsed


def detect_dialogue_token(text: str) -> Optional[str]:
    compact = normalize_for_compare(text)
    no_idx = compact.find("nohaydialogo")
    yes_idx = compact.find("haydialogo")
    if no_idx != -1 and (yes_idx == -1 or no_idx <= yes_idx):
        return "NO HAY DIÁLOGO"
    if yes_idx != -1:
        return "HAY DIÁLOGO"
    return None


def infer_dialogue_from_hechos(hechos: Optional[str]) -> str:
    if not hechos:
        return "NO HAY DIÁLOGO"
    if re.search(
        r"\b(no\s+se\s+(pudo|ha\s+podido|logr[oó]|concret[oó]|estableci[oó]).*di[áa]logo|no\s+hay\s+di[áa]logo|sin\s+di[áa]logo)\b",
        hechos,
        re.IGNORECASE,
    ):
        return "NO HAY DIÁLOGO"
    if re.search(
        r"\b(se\s+realiz[oó]\s+una\s+reuni[oó]n|convoc[oó]\s+a\s+una\s+reuni[oó]n|mesa\s+de\s+(trabajo|di[áa]logo)|se\s+establecieron\s+los\s+siguientes\s+acuerdos|acordaron\s+que)\b",
        hechos,
        re.IGNORECASE,
    ):
        return "HAY DIÁLOGO"
    return "NO HAY DIÁLOGO"


def detect_dialogue_status(full_text: str) -> tuple[Optional[str], str]:
    full = norm_spaces(drop_footer_noise(full_text))
    search_text = full[:900].strip()
    no_pat = re.compile(r"\bNO\s+HAY\s+DI[ÁA]LOGO\b", re.IGNORECASE)
    yes_pat = re.compile(r"\bHAY\s+DI[ÁA]LOGO\b", re.IGNORECASE)
    status_pat = re.compile(r"\b(NO\s+HAY\s+DI[ÁA]LOGO|HAY\s+DI[ÁA]LOGO)\b", re.IGNORECASE)

    status: Optional[str] = detect_dialogue_token(search_text)
    first = status_pat.search(search_text)
    if status is None and first:
        token = first.group(1).upper().replace("Á", "A")
        status = "NO HAY DIÁLOGO" if token.startswith("NO HAY DIALOGO") else "HAY DIÁLOGO"

    full = re.sub(no_pat, "", full).strip()
    full = re.sub(yes_pat, "", full).strip()
    full = re.sub(r"^\bNO\b\s*", "", full, flags=re.IGNORECASE).strip()

    return status, clean_field_value(full) or ""


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


def is_non_case_table_page(page_text: str) -> bool:
    return bool(SECTION_TABLE_RE.search(norm_spaces(page_text)))


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

    return round(max(0.0, score), 3)


def finalize_case(current_case: dict, results: list[dict]) -> None:
    parsed = parse_case_left(current_case["_left_raw"])
    status, hechos = detect_dialogue_status(current_case["_right_raw"])
    parsed["Hechos del Mes"] = clean_hechos_tail(hechos or None)
    if status is None:
        status = infer_dialogue_from_hechos(parsed["Hechos del Mes"])
    parsed["_dialogo"] = status
    parsed["_page_start"] = current_case["_page_start"]
    parsed["_page_end"] = current_case["_page_end"]
    parsed["_confidence"] = score_confidence(parsed)
    results.append(parsed)


def extract_detailed_cases_from_range(doc: fitz.Document, start: int, end: int, x_split: float = 300.0) -> list[dict]:
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
                if is_non_case_table_page(page_text):
                    finalize_case(current_case, results)
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
                finalize_case(current_case, results)
                current_case = None

            win_left = [text for (yy0, _, text) in left_texts if yy0 >= y0 - 0.5 and yy0 < y1 - 0.5]
            left_raw = "\n".join(win_left)

            win_right_blocks = [b for b in right if not (b.y1 < y0 - 2 or b.y0 > y1 + 2)]
            win_right_blocks.sort(key=lambda b: (b.y0, b.x0))
            right_raw = "\n".join(b.text for b in win_right_blocks)

            current_case = {
                "_left_raw": left_raw,
                "_right_raw": right_raw,
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
        finalize_case(current_case, results)

    for row in results:
        if row.get("Tipo"):
            row["Tipo"] = row["Tipo"].rstrip(".").strip()

    return results


# -----------------------------
# Table/list parser (6.3/6.4/7.1/8.1/8.2)
# -----------------------------

TABLE_TYPE_RE = re.compile(
    r"(Socioambiental|Comunal|Demarcaci[oó]n\s+territorial|Asuntos\s+de\s+gobierno\s+nacional|Asuntos\s+de\s+gobierno\s+regional|Otros\s+asuntos)$",
    re.IGNORECASE,
)
TABLE_START_RE = re.compile(r"^(?:[A-Za-z]{3,5}-\d{2}|\d{1,2}-[A-Za-z]{3}-\d{2}|\d{1,2}/\d{1,2}/\d{2,4})\b")


def clean_table_line(line: str) -> str:
    line = norm_spaces(drop_footer_noise(line))
    if not line:
        return ""
    if SECTION_TABLE_RE.search(line):
        return ""
    if re.search(r"^\s*(N\.?°?|Tipo|Fecha de inicio|Departamento|Denominaci[oó]n del caso)\b", line, re.IGNORECASE):
        return ""
    return line


def parse_table_row(row: str) -> Optional[dict]:
    row = norm_spaces(row)
    if not row:
        return None

    m_type = TABLE_TYPE_RE.search(row)
    tipo = m_type.group(1).strip() if m_type else None
    row_no_tipo = row[: m_type.start()].strip() if m_type else row

    inicio = None
    m_ini = TABLE_START_RE.search(row_no_tipo)
    if m_ini:
        inicio = m_ini.group(0)
        row_no_tipo = row_no_tipo[m_ini.end() :].strip()

    # Keep remaining text as case description. Department is often mixed, so we
    # store it in Ubicacion only when clearly detected at the start.
    ubic = None
    dep_match = re.match(r"^([A-ZÁÉÍÓÚÑa-záéíóúñ\- ]{3,30})\s+(.+)$", row_no_tipo)
    caso = row_no_tipo
    if dep_match:
        candidate_dep = dep_match.group(1).strip()
        if candidate_dep in DEPARTAMENTOS or any(candidate_dep.startswith(dep) for dep in DEPARTAMENTOS):
            ubic = candidate_dep
            caso = dep_match.group(2).strip()

    if len(caso) < 10:
        return None

    return {
        "Tipo": clean_field_value(tipo) or "No especificado",
        "Ingreso como caso nuevo": inicio,
        "Caso": clean_field_value(caso),
        "Ubicacion": clean_field_value(ubic),
        "Actores Primarios": None,
        "Actores secundarios": None,
        "Actores terciarios": None,
        "Hechos del Mes": None,
        "_dialogo": "NO HAY DIÁLOGO",
        "_confidence": 0.75,
    }


def extract_table_cases_from_range(
    doc: fitz.Document,
    start: int,
    end: int,
    section: str,
    subsection: str,
    pdf_name: str,
) -> list[dict]:
    out: list[dict] = []

    buffer = ""
    buffer_page_start = start + 1

    for pno in range(start, end + 1):
        page_text = doc.load_page(pno).get_text("text")
        lines = [clean_table_line(ln) for ln in page_text.splitlines()]
        lines = [ln for ln in lines if ln]

        for line in lines:
            if SECTION_TABLE_RE.search(line):
                continue
            if START_ROW.match(line):
                continue

            if not buffer:
                buffer = line
                buffer_page_start = pno + 1
            else:
                if TABLE_START_RE.search(line) and TABLE_TYPE_RE.search(buffer):
                    parsed = parse_table_row(buffer)
                    if parsed:
                        parsed["_section"] = section
                        parsed["_subsection"] = subsection
                        parsed["_source_pdf"] = pdf_name
                        parsed["_page_start"] = buffer_page_start
                        parsed["_page_end"] = pno + 1
                        out.append(parsed)
                    buffer = line
                    buffer_page_start = pno + 1
                else:
                    buffer = f"{buffer} {line}".strip()

            if TABLE_TYPE_RE.search(buffer):
                parsed = parse_table_row(buffer)
                if parsed:
                    parsed["_section"] = section
                    parsed["_subsection"] = subsection
                    parsed["_source_pdf"] = pdf_name
                    parsed["_page_start"] = buffer_page_start
                    parsed["_page_end"] = pno + 1
                    out.append(parsed)
                    buffer = ""

    if buffer:
        parsed = parse_table_row(buffer)
        if parsed:
            parsed["_section"] = section
            parsed["_subsection"] = subsection
            parsed["_source_pdf"] = pdf_name
            parsed["_page_start"] = buffer_page_start
            parsed["_page_end"] = end + 1
            out.append(parsed)

    # dedupe rows by core fields
    dedup: list[dict] = []
    seen: set[tuple[str, str, str]] = set()
    for row in out:
        key = (str(row.get("Tipo") or ""), str(row.get("Ingreso como caso nuevo") or ""), str(row.get("Caso") or ""))
        if key in seen:
            continue
        seen.add(key)
        dedup.append(row)
    return dedup


# -----------------------------
# Section-specific parsers (VI.3 / VII / VIII)
# -----------------------------

MONTH_START_RE = re.compile(r"^(Ene|Feb|Mar|Abr|May|Jun|Jul|Ago|Set|Oct|Nov|Dic)-\d{2}\b", re.IGNORECASE)
TYPE_AT_END_RE = re.compile(
    r"(Socioambiental|Comunal|Demarcaci[oó]n\s+territorial|Asuntos\s+de\s+gobierno\s+nacional|Asuntos\s+de\s+gobierno\s+local|Asuntos\s+de\s+gobierno\s+regional|Otros\s+asuntos)$",
    re.IGNORECASE,
)
ROW_NUMBER_RE = re.compile(r"^\s*\d+\.\s*$")
TYPE_IN_CASE_RE = re.compile(r"\bTipo\s+(?:por\s+)?(.+)$", re.IGNORECASE)
TYPE_PREFIX_RE = re.compile(
    r"^\s*Tipo\s+(?:por\s+)?(Socioambiental|Comunal|Demarcaci[oó]n\s+territorial|Asuntos\s+de\s+gobierno\s+nacional|Asuntos\s+de\s+gobierno\s+local|Asuntos\s+de\s+gobierno\s+regional|Otros\s+asuntos)\b[:\s.-]*",
    re.IGNORECASE,
)


def _normalize_tipo(tipo: Optional[str]) -> Optional[str]:
    if not tipo:
        return None
    t = clean_field_value(tipo)
    if not t:
        return None
    t = t.rstrip(".").strip()
    return t[0].upper() + t[1:] if t else None


def _clean_table_text_line(line: str) -> str:
    line = norm_spaces(drop_footer_noise(line))
    if not line:
        return ""
    if re.search(r"^\s*(N\.?°?|N\.º|Fecha de inicio|Departamento|Denominaci[oó]n del caso|Tipo|Lugar|Lugares|Caso|Forma de resoluci[oó]n|Motivo)\s*$", line, re.IGNORECASE):
        return ""
    if re.search(r"^\s*Durante el mes\b", line, re.IGNORECASE):
        return ""
    return line


def _parse_inicio_dep_den_tipo_row(text: str) -> Optional[dict]:
    raw = norm_spaces(text)
    if not raw:
        return None

    m_ini = MONTH_START_RE.search(raw)
    if not m_ini:
        return None
    ingreso = m_ini.group(0)
    rest = raw[m_ini.end() :].strip()

    m_tipo = TYPE_AT_END_RE.search(rest)
    if not m_tipo:
        return None
    tipo = _normalize_tipo(m_tipo.group(1))
    middle = rest[: m_tipo.start()].strip()
    if not middle:
        return None

    departamento = None
    for dep in DEPARTAMENTOS_EXTENDED:
        dep_comp = normalize_for_compare(dep)
        head = normalize_for_compare(middle[: len(dep) + 6])
        if head.startswith(dep_comp):
            departamento = dep
            middle = middle[len(dep) :].strip(" ,;-")
            break
    if not departamento:
        parts = middle.split(maxsplit=1)
        if not parts:
            return None
        departamento = parts[0]
        middle = parts[1].strip() if len(parts) > 1 else ""

    denominacion = clean_field_value(middle)
    if not denominacion:
        return None

    return {
        "Ingreso como caso nuevo": clean_field_value(ingreso),
        "Departamento": clean_field_value(departamento),
        "Denominacion del caso": denominacion,
        "Tipo": tipo,
    }


def extract_inicio_dep_den_tipo_from_range(
    doc: fitz.Document,
    start: int,
    end: int,
    section: str,
    subsection: str,
    pdf_name: str,
    begin_on_heading: Optional[str] = None,
    stop_on_heading: Optional[str] = None,
    numbered_rows: bool = False,
) -> list[dict]:
    out: list[dict] = []
    buffer = ""
    buffer_page_start = start + 1
    collecting = begin_on_heading is None

    for pno in range(start, end + 1):
        text = doc.load_page(pno).get_text("text")
        raw_lines = text.splitlines()
        for raw in raw_lines:
            if begin_on_heading and heading_contains(raw, begin_on_heading):
                collecting = True
                continue
            if stop_on_heading and heading_contains(raw, stop_on_heading):
                break
            line = _clean_table_text_line(raw)
            if not line:
                continue
            if re.search(r"^IX\.\s*OTROS\s+INDICADORES\b", normalize_heading(line), re.IGNORECASE):
                break
            if not collecting:
                continue

            if numbered_rows and ROW_NUMBER_RE.match(line):
                if buffer:
                    parsed = _parse_inicio_dep_den_tipo_row(buffer)
                    if parsed:
                        parsed["_section"] = section
                        parsed["_subsection"] = subsection
                        parsed["_source_pdf"] = pdf_name
                        parsed["_page_start"] = buffer_page_start
                        parsed["_page_end"] = pno + 1
                        out.append(parsed)
                buffer = ""
                buffer_page_start = pno + 1
                continue

            if not numbered_rows and MONTH_START_RE.search(line):
                if buffer:
                    parsed = _parse_inicio_dep_den_tipo_row(buffer)
                    if parsed:
                        parsed["_section"] = section
                        parsed["_subsection"] = subsection
                        parsed["_source_pdf"] = pdf_name
                        parsed["_page_start"] = buffer_page_start
                        parsed["_page_end"] = pno + 1
                        out.append(parsed)
                buffer = line
                buffer_page_start = pno + 1
                continue

            if buffer:
                buffer = f"{buffer} {line}".strip()
            else:
                buffer = line
                if not numbered_rows:
                    buffer_page_start = pno + 1

        ntext = normalize_heading(text)
        if begin_on_heading and heading_contains(text, begin_on_heading):
            collecting = True
        if stop_on_heading and heading_contains(text, stop_on_heading):
            break
        if "IX OTROS INDICADORES" in ntext:
            break

    if buffer:
        parsed = _parse_inicio_dep_den_tipo_row(buffer)
        if parsed:
            parsed["_section"] = section
            parsed["_subsection"] = subsection
            parsed["_source_pdf"] = pdf_name
            parsed["_page_start"] = buffer_page_start
            parsed["_page_end"] = end + 1
            out.append(parsed)

    return out


def extract_64_reactivated_from_range(
    doc: fitz.Document,
    start: int,
    end: int,
    section: str,
    subsection: str,
    pdf_name: str,
    stop_on_heading: Optional[str] = None,
) -> list[dict]:
    out: list[dict] = []
    dep_parts: list[str] = []
    caso_parts: list[str] = []
    collecting = False
    stop = False

    for pno in range(start, end + 1):
        page = doc.load_page(pno)
        for b in sorted(page_blocks(page), key=lambda x: (x.y0, x.x0)):
            txt = norm_spaces(b.text)
            if not txt:
                continue
            if heading_contains(txt, "6.4 CONFLICTOS REACTIVADOS"):
                collecting = True
                continue
            if stop_on_heading and heading_contains(txt, stop_on_heading):
                stop = True
                break
            if not collecting:
                continue
            if _is_header_or_noise_block(txt):
                continue
            if ROW_NUMBER_RE.match(txt):
                continue

            if 75 <= b.x0 < 205:
                dep_parts.append(txt)
            elif b.x0 >= 205:
                caso_parts.append(txt)

        if stop:
            break

    if dep_parts or caso_parts:
        tipo = None
        denom = _join_text(caso_parts)
        if denom:
            m_tipo = TYPE_PREFIX_RE.search(denom)
            if m_tipo:
                tipo = _normalize_tipo(m_tipo.group(1))
                denom = clean_field_value(denom[m_tipo.end() :])
        out.append(
            {
                "Ingreso como caso nuevo": None,
                "Departamento": _join_text(dep_parts),
                "Denominacion del caso": denom,
                "Tipo": tipo,
                "_section": section,
                "_subsection": subsection,
                "_source_pdf": pdf_name,
                "_page_start": start + 1,
                "_page_end": end + 1,
            }
        )

    dedup: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for row in out:
        key = (str(row.get("Departamento") or ""), str(row.get("Denominacion del caso") or ""))
        if not key[1] or key in seen:
            continue
        seen.add(key)
        dedup.append(row)
    return dedup


def _is_header_or_noise_block(text: str) -> bool:
    t = norm_spaces(text)
    if not t:
        return True
    if t.startswith("Reporte Mensual de Conflictos Sociales"):
        return True
    if "ADJUNTÍA PARA LA PREVENCIÓN DE CONFLICTOS SOCIALES" in t:
        return True
    if re.search(r"^(N\.?°?|N\.º)\s+(Lugar|Lugares)\s+Caso", t, re.IGNORECASE):
        return True
    if re.search(r"^A continuación, se presenta la frecuencia mensual", t, re.IGNORECASE):
        return True
    if re.search(r"^Durante el mes\b", t, re.IGNORECASE):
        return True
    if re.search(r"^La Defensor[ií]a del Pueblo considera", t, re.IGNORECASE):
        return True
    if re.search(r"^Cuadro N°", t, re.IGNORECASE):
        return True
    if re.search(r"^Fuente:\s*Defensor[ií]a del Pueblo", t, re.IGNORECASE):
        return True
    if re.search(r"^\(?N[uú]mero de casos\)?$", t, re.IGNORECASE):
        return True
    if re.search(r"^20\d{2}\s+20\d{2}\b", t):
        return True
    return False


def _join_text(parts: list[str]) -> Optional[str]:
    if not parts:
        return None
    return clean_field_value(" ".join(parts))


def extract_71_ubicacion_caso_from_range(
    doc: fitz.Document,
    start: int,
    end: int,
    section: str,
    subsection: str,
    pdf_name: str,
    begin_on_heading: str = "CONFLICTOS QUE HAN PASADO DE ESTADO ACTIVO A LATENTE",
    stop_on_heading: str = "CASOS QUE SALIERON DEL REPORTE DURANTE EL MES",
) -> list[dict]:
    out: list[dict] = []
    current: Optional[dict] = None
    stop = False
    collecting = False

    for pno in range(start, end + 1):
        page = doc.load_page(pno)
        ptxt_raw = page.get_text("text")
        ptxt = normalize_heading(ptxt_raw)
        if not collecting and heading_contains(ptxt, begin_on_heading):
            collecting = True
        if heading_contains(ptxt, stop_on_heading):
            break
        blocks = sorted(page_blocks(page), key=lambda b: (b.y0, b.x0))

        for b in blocks:
            txt = norm_spaces(b.text)
            if _is_header_or_noise_block(txt):
                continue
            if not collecting and heading_contains(normalize_heading(txt), begin_on_heading):
                collecting = True
                continue
            if not collecting:
                continue
            if heading_contains(txt, stop_on_heading):
                stop = True
                break
            if ROW_NUMBER_RE.match(txt) and b.x0 < 80:
                if current:
                    out.append(
                        {
                            "Ubicacion": _join_text(current["ubic"]),
                            "Caso": _join_text(current["caso"]),
                            "_section": section,
                            "_subsection": subsection,
                            "_source_pdf": pdf_name,
                            "_page_start": current["page_start"],
                            "_page_end": pno + 1,
                        }
                    )
                current = {"ubic": [], "caso": [], "page_start": pno + 1}
                continue

            if current is None:
                continue
            if 75 <= b.x0 < 205:
                current["ubic"].append(txt)
            elif b.x0 >= 205:
                current["caso"].append(txt)

        if stop:
            break

    if current:
        out.append(
            {
                "Ubicacion": _join_text(current["ubic"]),
                "Caso": _join_text(current["caso"]),
                "_section": section,
                "_subsection": subsection,
                "_source_pdf": pdf_name,
                "_page_start": current["page_start"],
                "_page_end": end + 1,
            }
        )

    dedup: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for row in out:
        key = (str(row.get("Ubicacion") or ""), str(row.get("Caso") or ""))
        if not key[1] or key in seen:
            continue
        seen.add(key)
        dedup.append(row)
    return dedup


def infer_report_month(pdf_name: str) -> Optional[str]:
    m = re.search(
        r"(enero|febrero|marzo|abril|mayo|junio|julio|agosto|setiembre|septiembre|octubre|noviembre|diciembre)[-_ ]+(20\d{2})",
        pdf_name,
        re.IGNORECASE,
    )
    if not m:
        return None
    month = m.group(1).lower()
    month = "setiembre" if month == "septiembre" else month
    month_title = month[0].upper() + month[1:]
    return f"{month_title} de {m.group(2)}"


def _split_lugar_candidates(text: Optional[str]) -> list[str]:
    if not text:
        return []
    deps = sorted(DEPARTAMENTOS_EXTENDED, key=len, reverse=True)
    dep_pat = "|".join(re.escape(dep) for dep in deps)
    matcher = re.compile(rf"(?i)\b(?:{dep_pat})\b")
    matches = list(matcher.finditer(text))
    if len(matches) <= 1:
        val = clean_field_value(text)
        return [val] if val else []

    parts: list[str] = []
    for idx, m in enumerate(matches):
        start = m.start()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        seg = clean_field_value(text[start:end])
        if seg:
            parts.append(seg)
    return parts if parts else ([clean_field_value(text)] if clean_field_value(text) else [])


def _split_case_candidates(text: Optional[str]) -> list[str]:
    if not text:
        return []
    parts = re.split(r"(?=\bTipo\b)", text, flags=re.IGNORECASE)
    out = [clean_field_value(p) for p in parts if clean_field_value(p)]
    return out if len(out) > 1 else [clean_field_value(text)] if clean_field_value(text) else []


def _split_resolution_candidates(text: Optional[str], expected: int) -> list[Optional[str]]:
    if not text:
        return [None] * max(1, expected)
    pats = [
        r"El caso no presenta nuevos hechos[^.]*\.",
        r"Atención de las demandas por parte de la autoridad",
    ]
    for pat in pats:
        matches = re.findall(pat, text, flags=re.IGNORECASE)
        if len(matches) >= 2:
            vals = [clean_field_value(m) for m in matches if clean_field_value(m)]
            if vals:
                return vals
    return [clean_field_value(text)] if clean_field_value(text) else [None]


def _keep_from_last_department_anchor(text: Optional[str]) -> Optional[str]:
    if not text:
        return None
    deps = sorted(DEPARTAMENTOS_EXTENDED, key=len, reverse=True)
    dep_pat = "|".join(re.escape(dep) for dep in deps)
    matcher = re.compile(rf"(?i)\b(?:{dep_pat})\b")
    matches = list(matcher.finditer(text))
    if not matches:
        return clean_field_value(text)

    if len(matches) == 1:
        m = matches[0]
        prefix = clean_field_value(text[: m.start()]) or ""
        if prefix and not re.search(r"\b(distrito|provincia|anexo|centro poblado|comunidad)\b", prefix, re.IGNORECASE):
            return clean_field_value(text[m.start() :])
        return clean_field_value(text)

    idx = len(matches) - 1
    last_txt = normalize_for_compare(matches[idx].group(0))
    prev_txt = normalize_for_compare(matches[idx - 1].group(0)) if idx > 0 else ""
    if last_txt == "lima" and prev_txt == "limametropolitana":
        idx -= 1
    return clean_field_value(text[matches[idx].start() :])


def split_merged_viii_records(rows: list[dict]) -> list[dict]:
    out: list[dict] = []
    for row in rows:
        row = dict(row)
        row["Lugar"] = _keep_from_last_department_anchor(row.get("Lugar"))
        case_parts = _split_case_candidates(row.get("Caso"))
        if len(case_parts) <= 1:
            out.append(row)
            continue

        lugar_parts = _split_lugar_candidates(row.get("Lugar"))
        resol_parts = _split_resolution_candidates(row.get("Forma de Resolucion"), len(case_parts))

        n = len(case_parts)
        # If segmentation is too uncertain, keep original row.
        if len(lugar_parts) not in {1, n} and len(resol_parts) not in {1, n}:
            out.append(row)
            continue

        for idx in range(n):
            new_row = dict(row)
            new_row["Caso"] = case_parts[idx]
            new_row["Lugar"] = lugar_parts[idx] if len(lugar_parts) == n else (lugar_parts[0] if lugar_parts else row.get("Lugar"))
            new_row["Forma de Resolucion"] = (
                resol_parts[idx] if len(resol_parts) == n else (resol_parts[0] if resol_parts else row.get("Forma de Resolucion"))
            )
            out.append(new_row)
    return out


def extract_viii_records_from_range(
    doc: fitz.Document,
    start: int,
    end: int,
    section: str,
    subsection: str,
    pdf_name: str,
    begin_on_heading: Optional[str] = None,
    stop_on_heading: Optional[str] = None,
) -> list[dict]:
    out: list[dict] = []
    current: Optional[dict] = None
    stop = False
    mes = infer_report_month(pdf_name)
    collecting = begin_on_heading is None
    pending: list[tuple[float, float, str]] = []
    skip_chart_until_heading = False

    def push_collector(row: dict, x0: float, txt: str) -> None:
        if 75 <= x0 < 158:
            row["lugar"].append(txt)
        elif 158 <= x0 < 357:
            row["caso"].append(txt)
        elif x0 >= 357:
            row["resol"].append(txt)

    def emit_current(row: dict, page_end: int) -> None:
        lugar = _join_text(row["lugar"])
        caso = _join_text(row["caso"])
        resol = _join_text(row["resol"])

        # OCR/blocks sometimes merge Lugar+Caso in the left column.
        if (not caso) and lugar:
            m_tipo = re.search(r"\bTipo\b", lugar, re.IGNORECASE)
            if m_tipo:
                caso = clean_field_value(lugar[m_tipo.start() :])
                lugar = clean_field_value(lugar[: m_tipo.start()])

        out.append(
            {
                "Lugar": lugar,
                "Caso": caso,
                "Forma de Resolucion": resol,
                "Mes del Informe": mes,
                "_section": section,
                "_subsection": subsection,
                "_source_pdf": pdf_name,
                "_page_start": row["page_start"],
                "_page_end": page_end,
            }
        )

    for pno in range(start, end + 1):
        page = doc.load_page(pno)
        blocks = sorted(page_blocks(page), key=lambda b: (b.y0, b.x0))
        row_markers = [b.y0 for b in blocks if b.x0 < 80 and ROW_NUMBER_RE.match(norm_spaces(b.text))]
        row_markers.sort()
        first_marker_y = row_markers[0] if row_markers else None
        early_continuation_cutoff = (first_marker_y - 60.0) if first_marker_y is not None else None

        for b in blocks:
            txt = norm_spaces(b.text)
            ntxt = normalize_heading(txt)
            if begin_on_heading and heading_contains(ntxt, begin_on_heading):
                collecting = True
                continue
            if stop_on_heading and heading_contains(ntxt, stop_on_heading):
                stop = True
                break
            if not collecting:
                continue
            if heading_contains(txt, "A continuación, se presenta la frecuencia mensual del último año"):
                skip_chart_until_heading = True
                continue
            if skip_chart_until_heading and not (
                heading_contains(txt, "8.2 CONFLICTOS EN ESTADO LATENTE")
                or heading_contains(txt, "8.3 CASOS FUSIONADOS")
                or heading_contains(txt, "IX. OTROS INDICADORES")
            ):
                continue
            if skip_chart_until_heading and (
                heading_contains(txt, "8.2 CONFLICTOS EN ESTADO LATENTE")
                or heading_contains(txt, "8.3 CASOS FUSIONADOS")
                or heading_contains(txt, "IX. OTROS INDICADORES")
            ):
                skip_chart_until_heading = False
            if _is_header_or_noise_block(txt):
                continue
            if "IX. OTROS INDICADORES" in txt:
                stop = True
                break
            if ROW_NUMBER_RE.match(txt) and b.x0 < 80:
                if current:
                    emit_current(current, pno + 1)
                current = {"lugar": [], "caso": [], "resol": [], "page_start": pno + 1}
                if pending:
                    for x0p, _, txtp in pending:
                        push_collector(current, x0p, txtp)
                    pending = []
                continue

            if current is None:
                pending.append((b.x0, b.y0, txt))
                continue
            # If a row marker comes below this block and we are close to it, this
            # block likely belongs to the next row (common in these PDFs).
            if first_marker_y is not None and b.y0 < first_marker_y:
                if early_continuation_cutoff is not None and b.y0 > early_continuation_cutoff:
                    pending.append((b.x0, b.y0, txt))
                    continue
            push_collector(current, b.x0, txt)

        if stop:
            break

    if current:
        emit_current(current, end + 1)

    dedup: list[dict] = []
    seen: set[tuple[str, str, str]] = set()
    for row in out:
        key = (
            str(row.get("Lugar") or ""),
            str(row.get("Caso") or ""),
            str(row.get("Forma de Resolucion") or ""),
        )
        if not key[1] or key in seen:
            continue
        seen.add(key)
        dedup.append(row)
    return split_merged_viii_records(dedup)


# -----------------------------
# Structure tracking
# -----------------------------

def detect_structure_profile(doc: fitz.Document, records: list[dict], section_ranges: dict[str, tuple[int, int]]) -> dict:
    sections_found = sorted(section_ranges.keys())
    subsection_counter: dict[str, int] = {}
    low_conf = 0
    for row in records:
        ss = row.get("_subsection")
        if ss:
            subsection_counter[ss] = subsection_counter.get(ss, 0) + 1
        if float(row.get("_confidence") or 0) < 0.8:
            low_conf += 1

    table_hits = sum(1 for row in records if row.get("Hechos del Mes") is None)
    drift_flags: list[str] = []
    drift_score = 0.0

    for expected in ["VI", "VII", "VIII"]:
        if expected not in sections_found:
            drift_flags.append(f"missing_section_{expected}")
            drift_score += 0.25

    if not subsection_counter:
        drift_flags.append("no_subsections_detected")
        drift_score += 0.2

    if records and low_conf / len(records) > 0.25:
        drift_flags.append("high_low_confidence_ratio")
        drift_score += 0.2

    if records and table_hits / len(records) > 0.75:
        drift_flags.append("mostly_table_like_records")
        drift_score += 0.1

    profile = "modern_2col_v1"
    if drift_score >= 0.55:
        profile = "unknown_or_shifted"
    elif table_hits > 0 and low_conf / max(1, len(records)) < 0.2:
        profile = "mixed_2col_table_v1"

    return {
        "detected_structure_profile": profile,
        "sections_found": sections_found,
        "subsections_found": dict(sorted(subsection_counter.items())),
        "drift_score": round(min(1.0, drift_score), 3),
        "drift_flags": drift_flags,
        "records_total": len(records),
        "records_low_confidence": low_conf,
    }


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


def assign_local_case_ids(records: list[dict], pdf_path: Path) -> None:
    counter: dict[tuple[int, int], int] = {}
    for row in records:
        key = (int(row.get("_page_start") or 0), int(row.get("_page_end") or 0))
        counter[key] = counter.get(key, 0) + 1
        row["_case_id_local"] = f"{pdf_path.stem}:{key[0]}-{key[1]}:{counter[key]}"


def sort_records(records: list[dict]) -> None:
    records.sort(
        key=lambda r: (
            str(r.get("_source_pdf") or ""),
            int(r.get("_page_start") or 0),
            str(r.get("_case_id_local") or ""),
        )
    )


# -----------------------------
# Generic heading/range helpers
# -----------------------------

ROMAN_HEADING_RE = re.compile(r"^\s*(?P<roman>[IVXLCDM]+)(?:\s*[\).:\-]\s*|\s+)(?P<title>.+?)\s*$", re.IGNORECASE)
DECIMAL_HEADING_RE = re.compile(
    r"^\s*(?P<section>\d{1,2}\.\d{1,2})(?:\s*[\).:\-]\s*|\s+)(?P<title>.+?)\s*$",
    re.IGNORECASE,
)


def iter_pdf_pages_text(doc: fitz.Document) -> list[dict[str, Any]]:
    pages: list[dict[str, Any]] = []
    for idx in range(doc.page_count):
        text = doc.load_page(idx).get_text("text")
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        pages.append({"page": idx + 1, "text": text, "lines": lines})
    return pages


def _clean_heading_title(title: str) -> str:
    title = norm_spaces(title)
    title = re.sub(r"\s+", " ", title).strip()
    return title


def _roman_to_int(value: str) -> Optional[int]:
    symbols = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}
    value = value.upper().strip()
    if not value or any(ch not in symbols for ch in value):
        return None

    total = 0
    prev = 0
    for ch in reversed(value):
        cur = symbols[ch]
        if cur < prev:
            total -= cur
        else:
            total += cur
            prev = cur
    # Canonical validation to reject malformed numerals (e.g., IIX).
    if total <= 0:
        return None
    return total


def _uppercase_ratio(text: str) -> float:
    letters = [ch for ch in text if ch.isalpha()]
    if not letters:
        return 0.0
    upper = sum(1 for ch in letters if ch.isupper())
    return upper / len(letters)


def _is_heading_like_title(title: str) -> bool:
    title = _clean_heading_title(title)
    words = [w for w in re.split(r"\s+", title) if w]
    if len(words) < 2:
        return False

    first_alpha = next((ch for ch in title if ch.isalpha()), "")
    if first_alpha and first_alpha.islower():
        return False

    upper_ratio = _uppercase_ratio(title)
    if upper_ratio >= 0.7:
        return True

    heading_keywords = [
        "CONFLICT",
        "DETALLE",
        "INDICADOR",
        "ESTADO",
        "UBICAC",
        "CASOS",
        "ACCIONES",
        "FRECUENCIA",
        "COMPETENCIAS",
        "DEMANDAS",
    ]
    normalized = normalize_heading(title)
    return any(keyword in normalized for keyword in heading_keywords)


def detect_roman_headings_with_positions(page_text: str, max_probe_lines: int = 160) -> list[dict[str, Any]]:
    lines = [ln.strip() for ln in page_text.splitlines() if ln.strip()]
    hits: list[dict[str, Any]] = []
    for line_index, line in enumerate(lines[:max_probe_lines]):
        match = ROMAN_HEADING_RE.match(line)
        if not match:
            continue
        roman = match.group("roman").upper()
        title = _clean_heading_title(match.group("title"))
        roman_value = _roman_to_int(roman)
        if roman_value is None or roman_value > 20:
            continue
        if len(title) < 4:
            continue
        if not _is_heading_like_title(title):
            continue
        hits.append({"id": roman, "title": title, "line_index": line_index})
    return hits


def detect_roman_headings(page_text: str, max_probe_lines: int = 160) -> list[dict[str, str]]:
    hits_with_positions = detect_roman_headings_with_positions(page_text, max_probe_lines=max_probe_lines)
    hits: list[dict[str, str]] = [{"id": str(hit["id"]), "title": str(hit["title"])} for hit in hits_with_positions]
    return hits


def detect_decimal_headings_with_positions(page_text: str, max_probe_lines: int = 200) -> list[dict[str, Any]]:
    lines = [ln.strip() for ln in page_text.splitlines() if ln.strip()]
    hits: list[dict[str, Any]] = []
    for line_index, line in enumerate(lines[:max_probe_lines]):
        match = DECIMAL_HEADING_RE.match(line)
        if not match:
            continue
        title = _clean_heading_title(match.group("title"))
        if len(title) < 4:
            continue
        if not _is_heading_like_title(title):
            continue
        hits.append({"id": match.group("section"), "title": title, "line_index": line_index})
    return hits


def detect_decimal_headings(page_text: str, max_probe_lines: int = 200) -> list[dict[str, str]]:
    hits_with_positions = detect_decimal_headings_with_positions(page_text, max_probe_lines=max_probe_lines)
    hits: list[dict[str, str]] = [{"id": str(hit["id"]), "title": str(hit["title"])} for hit in hits_with_positions]
    return hits


def build_heading_ranges(headings: list[dict[str, Any]], page_count: int) -> list[dict[str, Any]]:
    if not headings:
        return []
    sorted_headings = sorted(
        headings,
        key=lambda h: (
            int(h.get("start_page") or 0),
            str(h.get("id") or ""),
            str(h.get("title") or ""),
        ),
    )
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, int]] = set()
    for heading in sorted_headings:
        key = (str(heading.get("id") or ""), int(heading.get("start_page") or 0))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(heading)

    out: list[dict[str, Any]] = []
    for idx, current in enumerate(deduped):
        next_start = int(deduped[idx + 1]["start_page"]) if idx + 1 < len(deduped) else page_count + 1
        start_page = int(current["start_page"])
        end_page = min(page_count, max(start_page, next_start - 1))
        out.append(
            {
                "id": str(current["id"]),
                "title": str(current.get("title") or ""),
                "start_page": start_page,
                "end_page": end_page,
            }
        )
    return out


def nest_subsections_within_roman(
    roman_ranges: list[dict[str, Any]], subsection_ranges: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    if not subsection_ranges:
        return []

    out: list[dict[str, Any]] = []
    roman_by_id: dict[str, dict[str, Any]] = {str(row["id"]): row for row in roman_ranges}
    for subsection in subsection_ranges:
        s_start = int(subsection["start_page"])
        s_end = int(subsection["end_page"])
        parent = subsection.get("parent_roman")
        parent_end: Optional[int] = None
        if parent is not None and str(parent) in roman_by_id:
            parent_end = int(roman_by_id[str(parent)]["end_page"])
        else:
            parent = None
            for roman in roman_ranges:
                r_start = int(roman["start_page"])
                r_end = int(roman["end_page"])
                if r_start <= s_start <= r_end:
                    parent = roman["id"]
                    parent_end = r_end
                    break
        row = dict(subsection)
        if parent_end is not None:
            if parent_end >= s_start:
                row["end_page"] = min(s_end, parent_end)
            else:
                # Same-page boundary collision: subsection starts where the next
                # Roman heading is also detected. Keep a safe, non-negative span.
                row["end_page"] = s_start
        else:
            row["end_page"] = max(s_start, s_end)
        row["parent_roman"] = parent
        out.append(row)
    return out


def assign_parent_roman_by_heading_order(
    roman_markers: list[dict[str, Any]],
    subsection_markers: list[dict[str, Any]],
    subsection_ranges: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not subsection_ranges:
        return []

    roman_sorted = sorted(
        roman_markers,
        key=lambda row: (
            int(row.get("start_page") or 0),
            int(row.get("line_index") or 0),
        ),
    )
    subsection_sorted = sorted(
        subsection_markers,
        key=lambda row: (
            int(row.get("start_page") or 0),
            int(row.get("line_index") or 0),
        ),
    )

    subsection_first_marker: dict[tuple[str, int], dict[str, Any]] = {}
    for marker in subsection_sorted:
        key = (str(marker.get("id") or ""), int(marker.get("start_page") or 0))
        if key in subsection_first_marker:
            continue
        subsection_first_marker[key] = marker

    parent_by_subsection_key: dict[tuple[str, int], Optional[str]] = {}
    ridx = 0
    current_parent: Optional[str] = None
    for marker in subsection_sorted:
        m_page = int(marker.get("start_page") or 0)
        m_line = int(marker.get("line_index") or 0)
        while ridx < len(roman_sorted):
            r_page = int(roman_sorted[ridx].get("start_page") or 0)
            r_line = int(roman_sorted[ridx].get("line_index") or 0)
            if (r_page < m_page) or (r_page == m_page and r_line <= m_line):
                current_parent = str(roman_sorted[ridx].get("id") or "")
                ridx += 1
                continue
            break
        key = (str(marker.get("id") or ""), int(marker.get("start_page") or 0))
        if key not in parent_by_subsection_key:
            parent_by_subsection_key[key] = current_parent

    out: list[dict[str, Any]] = []
    for row in subsection_ranges:
        key = (str(row.get("id") or ""), int(row.get("start_page") or 0))
        assigned = parent_by_subsection_key.get(key)
        updated = dict(row)
        updated["parent_roman"] = assigned
        out.append(updated)
    return out


# -----------------------------
# Map-first resolver (Active/Latent only)
# -----------------------------

ACTIVE_TITLE_TOKENS = ["DETALLE", "CONFLICTOS", "ACTIVOS"]
LATENT_TITLE_TOKENS = ["DETALLE", "CONFLICTOS", "LATENTES"]
INACTIVE_TITLE_TOKENS = ["CASOS", "SALIERON", "REPORTE"]


def _title_match_score(title: str, tokens: list[str]) -> float:
    normalized = normalize_heading(title)
    if not normalized:
        return 0.0
    hits = sum(1 for token in tokens if token in normalized)
    return hits / max(1, len(tokens))


def _best_section_by_tokens(roman_sections: list[dict[str, Any]], tokens: list[str], threshold: float = 0.66) -> Optional[dict[str, Any]]:
    scored: list[tuple[float, dict[str, Any]]] = []
    for section in roman_sections:
        score = _title_match_score(str(section.get("title") or ""), tokens)
        if score >= threshold:
            scored.append((score, section))
    if not scored:
        return None
    scored.sort(key=lambda item: (item[0], int(item[1].get("start_page") or 0)), reverse=True)
    return scored[0][1]


def _best_active_section(roman_sections: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for row in roman_sections:
        title = normalize_heading(str(row.get("title") or ""))
        if "ACTIV" in title and "LATENT" not in title:
            candidates.append(row)
    if not candidates:
        return None
    candidates.sort(key=lambda row: int(row.get("start_page") or 0), reverse=True)
    return candidates[0]


def _best_latent_section(roman_sections: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for row in roman_sections:
        title = normalize_heading(str(row.get("title") or ""))
        if "LATENT" in title:
            candidates.append(row)
    if not candidates:
        return None
    candidates.sort(key=lambda row: int(row.get("start_page") or 0), reverse=True)
    return candidates[0]


def _best_inactive_section(roman_sections: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for row in roman_sections:
        title = normalize_heading(str(row.get("title") or ""))
        if "SALIERON" in title and "REPORTE" in title:
            candidates.append(row)
    if not candidates:
        return None
    candidates.sort(key=lambda row: int(row.get("start_page") or 0), reverse=True)
    return candidates[0]


def _safe_page_span(start_page: Any, end_page: Any, page_count: int) -> Optional[tuple[int, int]]:
    try:
        start = int(start_page)
        end = int(end_page)
    except (TypeError, ValueError):
        return None
    start = max(1, min(page_count, start))
    end = max(1, min(page_count, end))
    if end < start:
        end = start
    return start, end


def _build_structure_from_doc(doc: fitz.Document) -> dict[str, Any]:
    pages = iter_pdf_pages_text(doc)
    page_count = doc.page_count

    roman_hits: list[dict[str, Any]] = []
    decimal_hits: list[dict[str, Any]] = []
    for page in pages:
        page_no = int(page["page"])
        text = str(page["text"])
        for hit in detect_roman_headings_with_positions(text):
            roman_hits.append(
                {
                    "id": hit["id"],
                    "title": hit["title"],
                    "start_page": page_no,
                    "line_index": hit["line_index"],
                }
            )
        for hit in detect_decimal_headings_with_positions(text):
            decimal_hits.append(
                {
                    "id": hit["id"],
                    "title": hit["title"],
                    "start_page": page_no,
                    "line_index": hit["line_index"],
                }
            )

    roman_sections = build_heading_ranges(roman_hits, page_count)
    subsections = build_heading_ranges(decimal_hits, page_count)
    subsections = assign_parent_roman_by_heading_order(roman_hits, decimal_hits, subsections)
    subsections = nest_subsections_within_roman(roman_sections, subsections)
    return {"roman_sections": roman_sections, "subsections": subsections}


def _load_report_from_section_map(section_map_path: Path, pdf_name: str) -> Optional[dict[str, Any]]:
    if not section_map_path.exists():
        return None
    try:
        payload = json.loads(section_map_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    reports = payload.get("reports")
    if not isinstance(reports, list):
        return None
    return next((row for row in reports if str(row.get("pdf_name") or "") == pdf_name), None)


def _subsection_starts_in_range(subsections: list[dict[str, Any]], start_page: int, end_page: int, page_count: int) -> dict[str, int]:
    out: dict[str, int] = {}
    for row in subsections:
        sid = str(row.get("id") or "")
        span = _safe_page_span(row.get("start_page"), row.get("end_page"), page_count)
        if not sid or span is None:
            continue
        s_start, _ = span
        if s_start < start_page or s_start > end_page:
            continue
        page_idx = s_start - 1
        if sid not in out or page_idx < out[sid]:
            out[sid] = page_idx
    return out


def _context_from_structure(structure: dict[str, Any], page_count: int, source: str) -> Optional[dict[str, Any]]:
    roman_sections = list(structure.get("roman_sections") or [])
    subsections = list(structure.get("subsections") or [])
    active = _best_active_section(roman_sections) or _best_section_by_tokens(roman_sections, ACTIVE_TITLE_TOKENS)
    latent = _best_latent_section(roman_sections) or _best_section_by_tokens(roman_sections, LATENT_TITLE_TOKENS)
    inactive = _best_inactive_section(roman_sections) or _best_section_by_tokens(roman_sections, INACTIVE_TITLE_TOKENS)
    if active is None or latent is None:
        return None

    active_span = _safe_page_span(active.get("start_page"), active.get("end_page"), page_count)
    latent_span = _safe_page_span(latent.get("start_page"), latent.get("end_page"), page_count)
    inactive_span = _safe_page_span(inactive.get("start_page"), inactive.get("end_page"), page_count) if inactive else None
    if active_span is None or latent_span is None:
        return None

    a_start, a_end = active_span
    l_start, l_end = latent_span
    return {
        "source": source,
        "active_section_id": str(active.get("id") or ""),
        "latent_section_id": str(latent.get("id") or ""),
        "active_range": (a_start - 1, a_end - 1),
        "latent_range": (l_start - 1, l_end - 1),
        "inactive_range": (inactive_span[0] - 1, inactive_span[1] - 1) if inactive_span else None,
        "active_subsections": _subsection_starts_in_range(subsections, a_start, a_end, page_count),
        "latent_subsections": _subsection_starts_in_range(subsections, l_start, l_end, page_count),
        "inactive_subsections": (
            _subsection_starts_in_range(subsections, inactive_span[0], inactive_span[1], page_count) if inactive_span else {}
        ),
        "active_title": str(active.get("title") or ""),
        "latent_title": str(latent.get("title") or ""),
        "inactive_section_id": str(inactive.get("id") or "") if inactive else "",
        "inactive_title": str(inactive.get("title") or "") if inactive else "",
        "flags": [],
    }


def resolve_active_latent_context(
    doc: fitz.Document,
    pdf_path: Path,
    section_map_path: Optional[Path],
    mode: str = "map-first",
) -> dict[str, Any]:
    page_count = doc.page_count
    if section_map_path is not None and mode in {"map-first", "map-only"}:
        report = _load_report_from_section_map(section_map_path, pdf_path.name)
        if report is not None:
            ctx = _context_from_structure(report, page_count, source="map")
            if ctx is not None:
                return ctx

    if mode == "map-only":
        return {
            "source": "none",
            "active_section_id": "",
            "latent_section_id": "",
            "inactive_section_id": "",
            "active_range": None,
            "latent_range": None,
            "inactive_range": None,
            "active_subsections": {},
            "latent_subsections": {},
            "inactive_subsections": {},
            "active_title": "",
            "latent_title": "",
            "inactive_title": "",
            "flags": ["map_unusable"],
        }

    built = _build_structure_from_doc(doc)
    ctx = _context_from_structure(built, page_count, source="builder")
    if ctx is not None:
        if section_map_path is not None and section_map_path.exists():
            ctx["flags"].append("map_fallback_used")
        return ctx

    return {
        "source": "none",
        "active_section_id": "",
        "latent_section_id": "",
        "inactive_section_id": "",
        "active_range": None,
        "latent_range": None,
        "inactive_range": None,
        "active_subsections": {},
        "latent_subsections": {},
        "inactive_subsections": {},
        "active_title": "",
        "latent_title": "",
        "inactive_title": "",
        "flags": ["no_target_sections_detected"],
    }
