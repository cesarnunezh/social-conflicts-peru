from __future__ import annotations

import argparse
import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import httpx
import lxml.html

from ..config import directories

BASE_URL = "https://www.defensoria.gob.pe/categorias_de_documentos/reportes/"
CARD_SELECTOR = '//*[contains(@class, "card-body")]'


@dataclass
class ReportLink:
    report_id: int | None
    title: str
    pdf_url: str


def strip_accents(text: str) -> str:
    normalized = unicodedata.normalize("NFD", text)
    return "".join(ch for ch in normalized if unicodedata.category(ch) != "Mn")


def slugify(text: str) -> str:
    text = strip_accents(text).lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    text = re.sub(r"-{2,}", "-", text).strip("-")
    return text or "reporte"


def parse_html(url: str, client: httpx.Client) -> lxml.html.HtmlElement:
    response = client.get(url, follow_redirects=True, timeout=30.0)
    response.raise_for_status()
    return lxml.html.fromstring(response.text)


def parse_report_id(title: str) -> int | None:
    # Matches patterns like: "N.° 262", "Nº 262", "No 262"
    patterns = [
        r"\bN\s*[.°ºo]*\s*(\d{1,4})\b",
        r"\breporte\s*de\s*conflictos\s*sociales\s*[-–]?\s*(\d{1,4})\b",
        r"\b(\d{2,4})\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, title, re.IGNORECASE)
        if match:
            try:
                return int(match.group(1))
            except ValueError:
                continue
    return None


def get_pdf_links(page_url: str, client: httpx.Client) -> list[ReportLink]:
    tree = parse_html(page_url, client)
    links: list[ReportLink] = []

    for card in tree.xpath(CARD_SELECTOR):
        anchors = card.xpath('.//a[@href]')
        if not anchors:
            continue

        anchor = anchors[0]
        href = (anchor.get("href") or "").strip()
        if not href:
            continue

        title = " ".join(t.strip() for t in anchor.xpath('.//text()') if t and t.strip())
        normalized_title = strip_accents(title).lower()
        if "conflicto" not in normalized_title:
            continue

        pdf_url = urljoin(page_url, href)
        if ".pdf" not in pdf_url.lower():
            continue

        links.append(ReportLink(report_id=parse_report_id(title), title=title, pdf_url=pdf_url))

    return links


def build_page_url(base_url: str, page: int) -> str:
    if "{page}" in base_url:
        return base_url.format(page=page)

    if page == 1:
        return base_url

    base = base_url.rstrip("/") + "/"
    return urljoin(base, f"page/{page}/")


def get_all_links(base_url: str, client: httpx.Client) -> list[ReportLink]:
    page_num = 1
    output: list[ReportLink] = []
    seen_urls: set[str] = set()

    while True:
        page_url = build_page_url(base_url, page_num)
        response = client.get(page_url, follow_redirects=True, timeout=30.0)
        if response.status_code != 200:
            break

        page_links = get_pdf_links(page_url, client)
        if not page_links:
            break

        for item in page_links:
            if item.pdf_url in seen_urls:
                continue
            seen_urls.add(item.pdf_url)
            output.append(item)

        page_num += 1

    output.sort(key=lambda x: x.report_id if x.report_id is not None else -1, reverse=True)
    return output


def load_cache(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}

    if not isinstance(data, dict):
        return {}
    return {str(k): v for k, v in data.items() if isinstance(v, dict)}


def save_cache(path: Path, cache: dict[str, dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def choose_filename(report: ReportLink) -> str:
    report_id = str(report.report_id) if report.report_id is not None else "unknown"
    name = slugify(report.title)
    return f"{report_id}_{name}.pdf"


def fetch_reports(
    base_url: str,
    out_dir: Path,
    cache_file: Path,
    limit: int | None,
    force: bool,
) -> dict[str, int]:
    out_dir.mkdir(parents=True, exist_ok=True)
    cache = load_cache(cache_file)

    summary = {
        "discovered": 0,
        "processed": 0,
        "downloaded": 0,
        "updated": 0,
        "unchanged": 0,
        "failed": 0,
    }

    with httpx.Client(headers={"User-Agent": "social-conflicts-peru-bot/1.0"}) as client:
        links = get_all_links(base_url, client)
        summary["discovered"] = len(links)

        for report in links:
            if limit is not None and summary["processed"] >= limit:
                break

            filename = choose_filename(report)
            destination = out_dir / filename
            cache_key = report.pdf_url
            previous = cache.get(cache_key, {})

            headers: dict[str, str] = {}
            if not force:
                etag = previous.get("etag")
                last_modified = previous.get("last_modified")
                if etag:
                    headers["If-None-Match"] = str(etag)
                if last_modified:
                    headers["If-Modified-Since"] = str(last_modified)

            summary["processed"] += 1

            try:
                response = client.get(report.pdf_url, headers=headers, follow_redirects=True, timeout=90.0)
                if response.status_code == 304:
                    summary["unchanged"] += 1
                    continue

                response.raise_for_status()
                if "pdf" not in (response.headers.get("Content-Type", "").lower() + report.pdf_url.lower()):
                    summary["failed"] += 1
                    continue

                temp_path = destination.with_suffix(destination.suffix + ".part")
                temp_path.write_bytes(response.content)
                file_hash = sha256_file(temp_path)
                was_existing = destination.exists()
                temp_path.replace(destination)

                cache[cache_key] = {
                    "report_id": report.report_id,
                    "title": report.title,
                    "url": report.pdf_url,
                    "local_path": str(destination),
                    "etag": response.headers.get("ETag"),
                    "last_modified": response.headers.get("Last-Modified"),
                    "content_length": response.headers.get("Content-Length"),
                    "sha256": file_hash,
                    "downloaded_at": datetime.now(timezone.utc).isoformat(),
                }

                if was_existing:
                    summary["updated"] += 1
                else:
                    summary["downloaded"] += 1
            except Exception:
                summary["failed"] += 1

    save_cache(cache_file, cache)
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Download Defensoria conflict reports with HTTP cache.")
    parser.add_argument("--base-url", default=BASE_URL, help="Reports listing URL.")
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=directories.DOCUMENTS,
        help="Directory to store downloaded PDFs.",
    )
    parser.add_argument(
        "--cache-file",
        type=Path,
        default=directories.DOCUMENTS / ".fetch_reports_cache.json",
        help="Path to JSON cache manifest.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Maximum number of report URLs to process in this run.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Ignore cache validators and force redownload.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    summary = fetch_reports(
        base_url=args.base_url,
        out_dir=args.out_dir,
        cache_file=args.cache_file,
        limit=args.limit,
        force=args.force,
    )

    print(
        " | ".join(
            [
                f"discovered={summary['discovered']}",
                f"processed={summary['processed']}",
                f"downloaded={summary['downloaded']}",
                f"updated={summary['updated']}",
                f"unchanged={summary['unchanged']}",
                f"failed={summary['failed']}",
            ]
        )
    )


if __name__ == "__main__":
    main()
