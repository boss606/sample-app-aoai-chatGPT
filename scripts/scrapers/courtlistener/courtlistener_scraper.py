#!/usr/bin/env python3
"""
Reusable CourtListener scraper, modeled after the california_law CodeScraper.

Given a search URL, it will:
1) Download and paginate the CourtListener search JSON.
2) Keep only results that match the expected court name AND court_id.
3) Resolver o texto direto pela API `/opinions/{id}` (preferindo `plain_text`; fallback para html do próprio JSON).
4) Salvar cada opinião em `downloads/courtlistener/` com filenames iniciando pelo court_id (sem scraper de página HTML nem PDF no fluxo principal).
"""
from __future__ import annotations

import logging
import re
import time
from pathlib import Path
import os
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
import yaml

# HTTP defaults
# Increase timeout/backoff to better handle slow responses and avoid hammering.
DEFAULT_TIMEOUT = 60
TOKEN = os.getenv("COURTLISTENER_TOKEN")
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; courtlistener-scraper/1.0)",
    **({"Authorization": f"Token {TOKEN}"} if TOKEN else {}),
}
RETRIES = 6
BACKOFF = 3
REQUEST_PAUSE = 0.5
SCRAPE_PAUSE = 0.25

BASE_URL = "https://www.courtlistener.com"
API_BASE = f"{BASE_URL}/api/rest/v4"

# Default downloads folder: scripts/scrapers/downloads/courtlistener
# parents[1] -> .../scripts/scrapers
DEFAULT_OUT_DIR = Path(__file__).resolve().parents[1] / "downloads" / "courtlistener"

log = logging.getLogger(__name__)


class CourtListenerOpinionScraper:
    """
    Scrape opinions from the CourtListener search API for a specific court.

    Required:
      - search_url: complete search API URL (already parameterized)
      - court_id: e.g., "scotus"
      - court_name: e.g., "Supreme Court of the United States"

    Optional:
      - file_prefix: prepended to filenames (defaults to "<court_id>_")
      - out_dir: local output directory (defaults to downloads/courtlistener)
      - max_results: optional limit of opinions to fetch (pagination stops there)
      - storage: placeholder for future blob uploads (not used for now)
    """

    def __init__(
        self,
        *,
        search_url: str,
        court_id: str,
        court_name: str,
        file_prefix: str | None = None,
        out_dir: str | Path | None = None,
        max_results: Optional[int] = None,
        aggregate_file: str | Path | None = None,
        storage: Any = None,
        logger: logging.Logger | None = None,
    ):
        if not search_url:
            raise ValueError("search_url is required")
        if not court_id:
            raise ValueError("court_id is required (e.g., scotus)")
        if not court_name:
            raise ValueError("court_name is required (e.g., Supreme Court of the United States)")

        self.search_url = search_url
        self.court_id = court_id
        self.court_name = court_name
        self.file_prefix = (file_prefix or f"{court_id}_").lower()
        self.out_dir = Path(out_dir) if out_dir else DEFAULT_OUT_DIR
        self.max_results = max_results
        self.aggregate_file = Path(aggregate_file) if aggregate_file else None
        self.storage = storage  # reserved for future use
        self.log = logger or log

    # ---------- HTTP helpers ----------
    def _fetch_json(self, url: str) -> Dict[str, Any]:
        last_exc = None
        for attempt in range(1, RETRIES + 1):
            try:
                resp = requests.get(url, timeout=DEFAULT_TIMEOUT, headers=HEADERS)
                if resp.status_code in (429,) or 500 <= resp.status_code < 600:
                    wait = BACKOFF * attempt
                    self.log.warning(
                        "HTTP %s at %s (attempt %d/%d). Waiting %ss...",
                        resp.status_code,
                        url,
                        attempt,
                        RETRIES,
                        wait,
                    )
                    time.sleep(wait)
                    continue
                resp.raise_for_status()
                return resp.json()
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
                    "Connection error (%s) at %s, attempt %d/%d. Waiting %ss...",
                    type(exc).__name__,
                    url,
                    attempt,
                    RETRIES,
                    wait,
                )
                time.sleep(wait)
        raise last_exc if last_exc else RuntimeError(f"Failed to fetch JSON: {url}")

    def _fetch_html(self, url: str) -> BeautifulSoup:
        last_exc = None
        for attempt in range(1, RETRIES + 1):
            try:
                resp = requests.get(url, timeout=DEFAULT_TIMEOUT, headers=HEADERS)
                # CourtListener sometimes returns 202 with an empty body while the page is prepared.
                # In that case (or if the body is empty), retry with backoff.
                if resp.status_code == 202 or not resp.text.strip():
                    wait = BACKOFF * attempt
                    self.log.warning(
                        "Empty/202 HTML at %s (attempt %d/%d). Waiting %ss...",
                        url,
                        attempt,
                        RETRIES,
                        wait,
                    )
                    if attempt == RETRIES:
                        resp.raise_for_status()
                        return BeautifulSoup(resp.text, "html.parser")
                    time.sleep(wait)
                    continue
                if resp.status_code in (429,) or 500 <= resp.status_code < 600:
                    wait = BACKOFF * attempt
                    self.log.warning(
                        "HTTP %s at %s (attempt %d/%d). Waiting %ss...",
                        resp.status_code,
                        url,
                        attempt,
                        RETRIES,
                        wait,
                    )
                    time.sleep(wait)
                    continue
                resp.raise_for_status()
                return BeautifulSoup(resp.text, "html.parser")
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
                    "Connection error (%s) at %s, attempt %d/%d. Waiting %ss...",
                    type(exc).__name__,
                    url,
                    attempt,
                    RETRIES,
                    wait,
                )
                time.sleep(wait)
        raise last_exc if last_exc else RuntimeError(f"Failed to fetch HTML: {url}")

    def _fetch_bytes(self, url: str) -> bytes:
        last_exc = None
        for attempt in range(1, RETRIES + 1):
            try:
                resp = requests.get(url, timeout=DEFAULT_TIMEOUT, headers=HEADERS)
                if resp.status_code in (429,) or 500 <= resp.status_code < 600:
                    wait = BACKOFF * attempt
                    self.log.warning(
                        "HTTP %s at %s (attempt %d/%d). Waiting %ss...",
                        resp.status_code,
                        url,
                        attempt,
                        RETRIES,
                        wait,
                    )
                    time.sleep(wait)
                    continue
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
                    "Connection error (%s) at %s, attempt %d/%d. Waiting %ss...",
                    type(exc).__name__,
                    url,
                    attempt,
                    RETRIES,
                    wait,
                )
                time.sleep(wait)
        raise last_exc if last_exc else RuntimeError(f"Failed to fetch bytes: {url}")

    # ---------- parsing helpers ----------
    def _matches_court(self, item: Dict[str, Any]) -> bool:
        court_field = item.get("court")
        court_id_field = item.get("court_id") or item.get("court_id_slug")

        if court_field and court_field != self.court_name:
            return False
        if court_id_field and str(court_id_field) != self.court_id:
            return False
        return bool(item.get("absolute_url"))

    def _absolute_to_full(self, absolute_url: str) -> str:
        return urljoin(BASE_URL, absolute_url)

    def _slug_from_absolute(self, absolute_url: str) -> str:
        # example: /opinion/9346369/rhoe-v-montgomery-.../
        parts = [p for p in absolute_url.split("/") if p]
        slug_parts: List[str] = []
        if len(parts) >= 2 and parts[-2].isdigit():
            slug_parts.append(parts[-2])
        if parts:
            slug_parts.append(parts[-1])
        slug_raw = "_".join(slug_parts) or absolute_url.strip("/").replace("/", "_")
        return re.sub(r"[^a-zA-Z0-9_-]+", "-", slug_raw).strip("-").lower()

    def _normalize_text(self, text: str) -> str:
        """Normalize whitespace and separators for RAG-friendly text."""
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        text = text.replace("\u00a0", " ").replace("\ufeff", "")
        text = text.replace("\f", "\n")  # form feed to newline

        lines: List[str] = []
        last_blank = False
        for raw in text.splitlines():
            line = re.sub(r"\s+", " ", raw).strip()
            if not line:
                if last_blank:
                    continue
                last_blank = True
                lines.append("")
            else:
                last_blank = False
                lines.append(line)
        return "\n".join(lines).strip()

    def _extract_opinion_text(self, soup: BeautifulSoup) -> str:
        # prefer the main article inside #opinion
        node = soup.select_one("#opinion article")
        if not node:
            node = soup.select_one("#opinion")
        if not node:
            node = soup.select_one("article")
        if not node:
            node = soup.body or soup
        text = node.get_text(separator="\n", strip=True)
        return self._normalize_text(text)

    def _fetch_from_api_fallback(self, item: Dict[str, Any]) -> Tuple[Optional[str], Optional[str], Optional[str]]:
        """Fetch text from the CourtListener API and return (text, source_key, api_url)."""
        # Prefer the resource_uri field returned by the search API.
        api_url = item.get("resource_uri")
        if api_url:
            api_url = urljoin(BASE_URL, api_url)
        else:
            # Derive opinion id from the absolute_url, e.g. /opinion/2755877/...
            absolute_url = item.get("absolute_url") or ""
            m = re.search(r"/opinion/(\d+)/", absolute_url)
            if not m:
                return None, None, None
            api_url = f"{API_BASE}/opinions/{m.group(1)}/"

        try:
            data = self._fetch_json(api_url)
        except Exception as exc:  # noqa: BLE001
            self.log.warning("API fallback failed for %s: %s", api_url, exc)
            return None, None, api_url

        # Order of preference: plain text first, then HTML variants.
        for key in ("plain_text", "html", "html_lawbox", "html_with_citations"):
            val = data.get(key)
            if val:
                if key == "plain_text":
                    return self._normalize_text(val), key, api_url
                soup = BeautifulSoup(val, "html.parser")
                return self._extract_opinion_text(soup), key, api_url
        return None, None, api_url

    def _build_metadata(
        self,
        *,
        item: Dict[str, Any],
        slug: str,
        full_url: str,
        api_url: Optional[str],
        pdf_url: Optional[str],
        text_source: Optional[str],
    ) -> str:
        meta: Dict[str, Any] = {}
        meta["court_id"] = self.court_id
        meta["court_name"] = self.court_name
        meta["case_name"] = item.get("caseName") or item.get("caseNameFull")
        meta["case_name_full"] = item.get("caseNameFull")
        meta["citation"] = item.get("citation") or []
        meta["neutral_cite"] = item.get("neutralCite")
        meta["docket_number"] = item.get("docketNumber")
        meta["date_filed"] = item.get("dateFiled")
        meta["url"] = full_url
        meta["pdf_url"] = pdf_url
        meta["text_source"] = text_source
        meta["slug"] = slug
        meta["file_prefix"] = self.file_prefix
        cleaned: Dict[str, Any] = {}
        for key, val in meta.items():
            if val is None:
                continue
            if isinstance(val, str) and not val.strip():
                continue
            if isinstance(val, (list, tuple, dict)) and len(val) == 0:
                continue
            cleaned[key] = val
        return yaml.safe_dump(cleaned, sort_keys=False).strip()

    # ---------- persistence / upload ----------
    def upload(self, content: str, blob_name: str | None = None) -> str:
        if not self.storage:
            raise RuntimeError("storage not configured; set storage in the constructor to enable upload.")
        default_name = (
            self.aggregate_file.name
            if self.aggregate_file
            else f"{self.file_prefix}{self.court_id}_opinions.txt"
        )
        chosen = (blob_name or default_name).lower()
        if not self.storage.upload_text(content, chosen):
            raise RuntimeError(f"Failed to upload {chosen} to blob {self.storage.raw_container}")
        self.log.info("TXT uploaded to blob %s/%s", getattr(self.storage, "raw_container", "?"), chosen)
        return chosen

    def upload_aggregate(self, blob_name: str | None = None) -> str:
        if not self.aggregate_file:
            raise RuntimeError("aggregate_file not configured; cannot upload.")
        if not self.aggregate_file.exists():
            raise RuntimeError(f"aggregate_file does not exist: {self.aggregate_file}")
        content = self.aggregate_file.read_text(encoding="utf-8")
        return self.upload(content, blob_name=blob_name)

    def scrape_all_and_upload(self, blob_name: str | None = None) -> List[Path]:
        paths = self.scrape_all()
        self.upload_aggregate(blob_name=blob_name)
        return paths

    # ---------- main flow ----------
    def list_opinions(self) -> List[Dict[str, Any]]:
        """
        Legacy helper that returns all matched items. Prefer iter_opinions to
        process page-by-page (streams results, avoids holding everything in memory).
        """
        return list(self.iter_opinions())

    def iter_opinions(self):
        """Yield matched opinions page-by-page, pausing between pages."""
        url = self.search_url
        count = 0

        while url:
            self.log.info("Fetching search page: %s", url)
            payload = self._fetch_json(url)
            for item in payload.get("results", []):
                if self._matches_court(item):
                    count += 1
                    self.log.info("Matched %s (total %d)", item.get("absolute_url"), count)
                    yield item
                    if self.max_results and count >= self.max_results:
                        return
            next_url = payload.get("next")
            if next_url and (not self.max_results or count < self.max_results):
                self.log.debug("Sleeping %ss before next page", REQUEST_PAUSE)
                time.sleep(REQUEST_PAUSE)
            url = next_url if (not self.max_results or count < self.max_results) else None

    def scrape_item(self, item: Dict[str, Any]) -> Optional[Path]:
        absolute_url = item.get("absolute_url")
        if not absolute_url:
            raise ValueError("absolute_url missing in item")

        slug = self._slug_from_absolute(absolute_url)
        full_url = self._absolute_to_full(absolute_url)
        # Main path: fetch text directly from API; only if empty, try PDF.
        text = None
        text_source: Optional[str] = None
        api_url: Optional[str] = None
        pdf_url: Optional[str] = None

        api_text, api_source, api_url = self._fetch_from_api_fallback(item)
        if api_text:
            text = api_text
            text_source = f"api_{api_source}" if api_source else "api"
            self.log.info("Used API text for %s", absolute_url)

        if not text:
            self.log.warning("No text available for %s; skipping opinion", absolute_url)
            return None

        if self.aggregate_file:
            target = self.aggregate_file
            target.parent.mkdir(parents=True, exist_ok=True)
            with open(target, "a", encoding="utf-8") as f:
                metadata = self._build_metadata(
                    item=item,
                    slug=slug,
                    full_url=full_url,
                    api_url=api_url,
                    pdf_url=pdf_url,
                    text_source=text_source,
                )
                f.write("---\n")
                f.write(metadata)
                f.write("\n---\n")
                f.write(text or "[no text scraped]")
                f.write("\n\n---\n\n")
            self.log.info("Appended opinion %s -> %s", absolute_url, target)
            return target

        filename = f"{self.file_prefix}{self.court_id}_{slug}.txt".lower()
        target = self.out_dir / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        if not text:
            text = "[no HTML text scraped]"
        with open(target, "w", encoding="utf-8") as f:
            f.write(text)
        self.log.info("Saved opinion %s -> %s", absolute_url, target)
        return target

    def scrape_all(self) -> List[Path]:
        """Process items as they are paginated (streaming), with pauses between calls."""
        saved: List[Path] = []
        for idx, item in enumerate(self.iter_opinions()):
            result = self.scrape_item(item)
            if result:
                saved.append(result)
            if SCRAPE_PAUSE:
                self.log.debug("Sleeping %ss between scrape requests", SCRAPE_PAUSE)
                time.sleep(SCRAPE_PAUSE)
        if not saved:
            self.log.info("Nenhuma opinião encontrada para %s", self.court_name)
        return saved


def scrape_opinions(
    *,
    search_url: str,
    court_id: str,
    court_name: str,
    file_prefix: str | None = None,
    out_dir: str | Path | None = None,
    max_results: Optional[int] = None,
    aggregate_file: str | Path | None = None,
    logger: logging.Logger | None = None,
) -> List[Path]:
    """Helper for quick, direct invocation (no CLI)."""
    scraper = CourtListenerOpinionScraper(
        search_url=search_url,
        court_id=court_id,
        court_name=court_name,
        file_prefix=file_prefix,
        out_dir=out_dir,
        max_results=max_results,
        aggregate_file=aggregate_file,
        logger=logger,
    )
    return scraper.scrape_all()

