"""
PDF scraper for California Rules of Court master page.
Fetches the master listing, downloads Title/Appendix PDFs, extracts text, and concatenates.
Includes gutter-based removal of line numbers and optional upload to Azure Blob.
"""
from __future__ import annotations

import logging
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Tuple
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from azure.ai.formrecognizer import DocumentAnalysisClient
from azure.core.credentials import AzureKeyCredential

from backend.storage.blob_storage import LegalDocsStorage

DEFAULT_TIMEOUT = 30
SLEEP_BETWEEN_REQUESTS = 1  # seconds
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; rules-pdf-scraper/1.0)"}
RETRIES = 3
BACKOFF = 2  # seconds base backoff for retryable errors
DEFAULT_CODE_PREFIX = "crc"
DEFAULT_START_URL = "https://courts.ca.gov/rules-forms/rules-court"

FORM_ENDPOINT = None  # resolved lazily
FORM_KEY = None  # resolved lazily


def _require(value: str | None, name: str) -> str:
    if value:
        return value
    env = os.getenv(name)
    if env:
        return env
    raise ValueError(f"Missing required environment variable: {name}")


@dataclass
class LayoutLine:
    text: str
    xmin: float
    xmax: float

    @property
    def width(self) -> float:
        return self.xmax - self.xmin


RESERVED = {
    "rule",
    "rules",
    "section",
    "sections",
    "sec",
    "art",
    "article",
    "articles",
    "appendix",
    "title",
    "titles",
    "chapter",
    "chapters",
    "division",
    "divisions",
    "subdivision",
    "subdivisions",
    "subsection",
    "subsections",
    "paragraph",
    "paragraphs",
    "code",
    "statute",
    "statutes",
    "act",
    "§",
    "form",
    "forms",
}


def _is_digitish(text: str) -> bool:
    """Match page/line numbers like '7', '28', '24.' etc."""
    return bool(re.fullmatch(r"\d+[\s\W]*", text.strip()))


def _short_leading_digits(text: str) -> bool:
    """True if the text begins with a short digit token (<=3 digits)."""
    tokens = text.strip().split()
    if not tokens:
        return False
    first = tokens[0].strip("().")
    return first.isdigit() and len(first) <= 3


def _reserved_neighbor(tokens: List[str], idx: int) -> bool:
    n = len(tokens)
    prev_tok = tokens[idx - 1].lower() if idx > 0 else ""
    next_tok = tokens[idx + 1].lower() if idx < n - 1 else ""
    return prev_tok in RESERVED or next_tok in RESERVED


def _percentile(values: List[float], p: float) -> float:
    if not values:
        return 0.05
    values_sorted = sorted(values)
    k = max(0, min(len(values_sorted) - 1, int(round(p * (len(values_sorted) - 1)))))
    return values_sorted[k]


def _detect_gutter(lines: Iterable[LayoutLine], p: float = 0.9) -> float:
    """Estimate left gutter (x_max) based on digit-only short lines."""
    xmins: List[float] = []
    for line in lines:
        if _is_digitish(line.text) and len(line.text.strip()) <= 4:
            xmins.append(line.xmin)
    gutter = _percentile(xmins, p) if xmins else 0.05
    return gutter + 0.02  # small margin


def _label_lines(lines: List[LayoutLine], gutter_max: float, max_width: float = 0.06) -> List[str]:
    """
    Label each line as 'line_number' or 'text' using gutter + width + digit heuristics.
    """
    labels: List[str] = []
    for line in lines:
        label = "text"
        if line.xmin <= gutter_max and line.width <= max_width:
            if _is_digitish(line.text) or _short_leading_digits(line.text):
                label = "line_number"
        labels.append(label)
    return labels


def _clean_lines(lines: List[LayoutLine], labels: List[str]) -> List[str]:
    """
    Drop lines labeled as line numbers; strip leading short-digit tokens unless adjacent to reserved terms.
    """
    out: List[str] = []
    for line, label in zip(lines, labels):
        if label == "line_number":
            continue
        tokens = line.text.replace("\u00a0", " ").strip().split()
        filtered: List[str] = []
        for idx, tok in enumerate(tokens):
            stripped_tok = tok.strip("().")
            if stripped_tok.isdigit() and len(stripped_tok) <= 3 and idx == 0:
                # keep sub-item markers like "(1)" explicitly
                if re.fullmatch(r"\(\d+\)", tok.strip()):
                    filtered.append(tok)
                    continue
                if _reserved_neighbor(tokens, idx):
                    filtered.append(tok)
                else:
                    continue
            else:
                filtered.append(tok)
        reconstructed = " ".join(filtered).strip()
        if reconstructed:
            out.append(reconstructed)
    return out


def _collapse_blanks(lines: List[str]) -> str:
    """Collapse multiple blank lines and join."""
    result: List[str] = []
    last_blank = False
    for line in lines:
        if line == "":
            if last_blank:
                continue
            last_blank = True
            result.append("")
        else:
            last_blank = False
            result.append(line)
    return "\n".join(result)


def pdf_bytes_to_text(data: bytes, client: DocumentAnalysisClient) -> str:
    """
    Extract text using layout lines and heuristics that drop line-number gutters.
    """
    poller = client.begin_analyze_document("prebuilt-layout", document=data)
    result = poller.result()

    layout_lines: List[LayoutLine] = []
    for page in result.pages:
        if not page.width or not page.height:
            continue
        pw, ph = float(page.width), float(page.height)
        for ln in page.lines:
            if not ln.polygon:
                continue
            xs = [pt.x / pw for pt in ln.polygon]
            xmin = min(xs)
            xmax = max(xs)
            layout_lines.append(LayoutLine(text=ln.content, xmin=xmin, xmax=xmax))

    if not layout_lines:
        # Fallback to paragraph text if no line geometry
        return "\n".join([p.content for p in result.paragraphs])

    gutter = _detect_gutter(layout_lines)
    labels = _label_lines(layout_lines, gutter_max=gutter)
    cleaned = _clean_lines(layout_lines, labels)
    return _collapse_blanks(cleaned)


def _normalize_pdf_text(raw: str) -> str:
    """Collapse repeated blank lines and trim."""
    lines = [ln.strip() for ln in raw.splitlines()]
    return _collapse_blanks(lines)


class RulesPdfScraper:
    def __init__(
        self,
        start_url: str = DEFAULT_START_URL,
        out_dir: str | Path | None = None,
        logger: logging.Logger | None = None,
        code_prefix: str = DEFAULT_CODE_PREFIX,
        storage: LegalDocsStorage | None = None,
    ):
        if not start_url:
            raise ValueError("start_url is required")
        self.start_url = start_url
        default_out_dir = Path(__file__).resolve().parents[1] / "downloads" / "rules_of_court"
        self.out_dir = Path(out_dir) if out_dir else default_out_dir
        self.log = logger or logging.getLogger(__name__)
        self.code_prefix = code_prefix
        self.storage = storage
        # Form Recognizer client will be initialized lazily
        self._form_client: DocumentAnalysisClient | None = None

    # ---------- HTTP helpers ----------
    def fetch_bytes(self, url: str) -> bytes:
        time.sleep(SLEEP_BETWEEN_REQUESTS)
        last_exc = None
        for attempt in range(1, RETRIES + 1):
            try:
                resp = requests.get(url, timeout=DEFAULT_TIMEOUT, headers=HEADERS)
                resp.raise_for_status()
                return resp.content
            except (
                requests.exceptions.ReadTimeout,
                requests.exceptions.ConnectTimeout,
                requests.exceptions.SSLError,
                requests.exceptions.ConnectionError,
            ) as exc:
                last_exc = exc
                if attempt == RETRIES:
                    break
                wait = BACKOFF * attempt
                self.log.warning(
                    "Connection error (%s) at %s, attempt %d/%d. Waiting %ss and retrying...",
                    type(exc).__name__,
                    url,
                    attempt,
                    RETRIES,
                    wait,
                )
                time.sleep(wait)
        raise last_exc if last_exc else RuntimeError(f"Failed to fetch bytes from {url}")

    def fetch_html(self, url: str) -> BeautifulSoup:
        html = self.fetch_bytes(url).decode("utf-8", errors="replace")
        return BeautifulSoup(html, "html.parser")

    def get_form_client(self) -> DocumentAnalysisClient:
        if self._form_client:
            return self._form_client
        endpoint = _require(FORM_ENDPOINT or None, "FORM_RECOGNIZER_ENDPOINT")
        key = _require(FORM_KEY or None, "FORM_RECOGNIZER_KEY")
        self._form_client = DocumentAnalysisClient(endpoint, AzureKeyCredential(key))
        return self._form_client

    # ---------- parsing ----------
    def parse_pdf_links(self, soup: BeautifulSoup) -> List[Tuple[str, str]]:
        """
        Return list of (title_text, pdf_url).
        Robust scan: any <a> with class 'file' or href ending with .pdf under .item-list (or globally as fallback).
        Title is taken from the enclosing <li> if present; else link text.
        """
        results: List[Tuple[str, str]] = []
        anchors = soup.select(".item-list a.file[href], .item-list a[href$='.pdf']")
        if not anchors:
            anchors = soup.select("a.file[href], a[href$='.pdf']")

        for a in anchors:
            href = a.get("href", "").strip()
            if not href:
                continue
            full = urljoin(self.start_url, href)
            li = a.find_parent("li")
            if li:
                title_text = li.get_text(separator=" ", strip=True)
            else:
                title_text = a.get_text(strip=True) or href
            results.append((title_text, full))
        return results

    # ---------- extraction ----------
    def extract_pdf_text(self, url: str) -> str:
        data = self.fetch_bytes(url)
        client = self.get_form_client()
        raw = pdf_bytes_to_text(data, client)
        return _normalize_pdf_text(raw)

    # ---------- main ----------
    def scrape_content(self) -> str:
        self.log.info("Fetching master page: %s", self.start_url)
        soup = self.fetch_html(self.start_url)
        pdf_links = self.parse_pdf_links(soup)
        if not pdf_links:
            raise RuntimeError("No PDF links found on master page.")

        buffer: List[str] = ["The California Rules of Court"]
        seen_urls = set()
        for title, pdf_url in pdf_links:
            if pdf_url in seen_urls:
                continue
            seen_urls.add(pdf_url)
            self.log.info("Downloading PDF: %s", pdf_url)
            try:
                text = self.extract_pdf_text(pdf_url)
            except Exception as exc:
                self.log.error("Failed to extract PDF %s (%s): %s", title, pdf_url, exc)
                continue
            buffer.append(f"=== {title} ===")
            buffer.append(text)

        return "\n\n".join(buffer)

    # ---------- persistence ----------
    def save_local(self, content: str, out_path: str | Path | None = None) -> Path:
        default_name = f"{self.code_prefix}_rules_of_court.txt"
        target = Path(out_path) if out_path else self.out_dir / default_name
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "w", encoding="utf-8") as f:
            f.write(content)
        self.log.info("Content saved to %s", target)
        return target

    def upload(self, content: str, blob_name: str | None = None) -> str:
        if not self.storage:
            raise RuntimeError("storage not configured; set storage in the constructor to enable upload.")
        default_name = f"{self.code_prefix}_rules_of_court.txt"
        chosen = blob_name or default_name
        if not self.storage.upload_text(content, chosen):
            raise RuntimeError(f"Failed to upload {chosen} to blob {self.storage.raw_container}")
        self.log.info("TXT uploaded to blob %s/%s", self.storage.raw_container, chosen)
        return chosen

    def scrape(self, out_path: str | Path | None = None) -> Path:
        content = self.scrape_content()
        return self.save_local(content, out_path=out_path)

    def scrape_and_upload(
        self, out_path: str | Path | None = None, blob_name: str | None = None
    ) -> Path:
        content = self.scrape_content()
        local_path = self.save_local(content, out_path=out_path)
        self.upload(content, blob_name=blob_name)
        return local_path

