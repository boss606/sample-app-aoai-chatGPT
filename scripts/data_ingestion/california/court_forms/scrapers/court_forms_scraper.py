"""
Scraper for California self-help court forms.
Fetches form listings by category, extracts title/form number/PDF link, saves to TXT.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

DEFAULT_TIMEOUT = 90  # CA court sites can be slow; 30s was causing ReadTimeout
RETRIES = 5
BACKOFF = 3
SLEEP_BETWEEN_REQUESTS = 1
BASE_URL = "https://selfhelp.courts.ca.gov"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; court-forms-scraper/1.0)"}

log = logging.getLogger(__name__)


class CourtFormsScraper:
    """
    Scrape a single category of self-help forms.
    """

    def __init__(
        self,
        *,
        category: str,
        search_url: str,
        out_dir: str | Path | None = None,
        file_prefix: str = "",
        aggregate_file: str | Path | None = None,
        storage: Any = None,
        logger: logging.Logger | None = None,
    ):
        if not category:
            raise ValueError("category is required (e.g., child_custody_visitation)")
        if not search_url:
            raise ValueError("search_url is required")
        _dir = Path(__file__).resolve().parent.parent
        default_out = _dir / "output"
        self.category = category
        self.search_url = search_url
        self.out_dir = Path(out_dir) if out_dir else default_out
        self.file_prefix = file_prefix.lower()
        self.aggregate_file = Path(aggregate_file) if aggregate_file else self.out_dir / f"{self.file_prefix}{self.category}.txt"
        self.storage = storage
        self.log = logger or log

    def _fetch_html(self, url: str) -> BeautifulSoup:
        last_exc = None
        for attempt in range(1, RETRIES + 1):
            try:
                resp = requests.get(url, timeout=DEFAULT_TIMEOUT, headers=HEADERS)
                if resp.status_code in (429,) or 500 <= resp.status_code < 600:
                    wait = BACKOFF * attempt
                    self.log.warning("HTTP %s at %s (attempt %d/%d). Waiting %ss...",
                        resp.status_code, url, attempt, RETRIES, wait)
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
                self.log.warning("Connection error (%s) at %s, attempt %d/%d. Waiting %ss...",
                    type(exc).__name__, url, attempt, RETRIES, wait)
                time.sleep(wait)
        raise last_exc if last_exc else RuntimeError(f"Failed to fetch HTML: {url}")

    def _list_form_links(self) -> List[str]:
        soup = self._fetch_html(self.search_url)
        links: List[str] = []
        for a in soup.select("a.usa-button--outline[href]"):
            text = a.get_text(strip=True)
            href = a["href"].strip()
            if not href:
                continue
            if "see form info" not in text.lower():
                continue
            full = urljoin(BASE_URL, href)
            if full not in links:
                links.append(full)
        return links

    def _parse_form_page(self, url: str) -> Optional[Dict[str, str]]:
        soup = self._fetch_html(url)
        title_node = soup.select_one("h1.jcc-hero__title")
        if not title_node:
            self.log.warning("Title not found at %s", url)
            return None
        title = title_node.get_text(" ", strip=True)
        form_number = None
        span = title_node.select_one("span.text-no-wrap")
        if span:
            form_number = span.get_text(strip=True)
        pdf_link = None
        link_node = soup.select_one("div.jcc-form--form-links a[href]")
        if link_node:
            pdf_link = urljoin(BASE_URL, link_node["href"].strip())
        lead_text = ""
        lead_node = soup.select_one("div.jcc-hero__lead")
        if lead_node:
            lead_text = lead_node.get_text(" ", strip=True)
        return {
            "title": title,
            "form_number": form_number or "",
            "pdf_url": pdf_link or "",
            "info_url": url,
            "lead": lead_text,
        }

    def _ensure_out_dir(self) -> None:
        self.out_dir.mkdir(parents=True, exist_ok=True)

    def _save(self, items: List[Dict[str, str]]) -> Path:
        self._ensure_out_dir()
        lines: List[str] = []
        for item in items:
            lines.append(f"Title: {item.get('title','').strip()}")
            if item.get("form_number"):
                lines.append(f"Form Number: {item['form_number'].strip()}")
            if item.get("lead"):
                lines.append(f"Lead: {item['lead'].strip()}")
            lines.append(f"Category: {self.category}")
            if item.get("pdf_url"):
                lines.append(f"PDF: {item['pdf_url']}")
            lines.append(f"Info URL: {item.get('info_url','')}")
            lines.append("---")
        content = "\n".join(lines).strip() + "\n"
        self.aggregate_file.write_text(content, encoding="utf-8")
        self.log.info("Saved %d forms to %s", len(items), self.aggregate_file)
        return self.aggregate_file

    def upload(self, blob_name: str | None = None) -> str:
        if not self.storage:
            raise RuntimeError("storage not configured")
        chosen = (blob_name or self.aggregate_file.name).lower()
        with self.aggregate_file.open("r", encoding="utf-8") as f:
            content = f.read()
        ok = self.storage.upload_text(content, chosen)
        if not ok:
            raise RuntimeError(f"Failed to upload {chosen}")
        return chosen

    def scrape_all(self) -> List[Path]:
        links = self._list_form_links()
        results: List[Dict[str, str]] = []
        for link in links:
            try:
                item = self._parse_form_page(link)
                if item:
                    results.append(item)
            except Exception as exc:
                self.log.warning("Failed to parse %s: %s", link, exc)
            time.sleep(SLEEP_BETWEEN_REQUESTS)
        if not results:
            self.log.warning("No forms captured for category %s", self.category)
        path = self._save(results)
        return [path]

    def scrape_all_and_upload(self, blob_name: str | None = None) -> List[Path]:
        paths = self.scrape_all()
        self.upload(blob_name=blob_name)
        return paths
