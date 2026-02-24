"""
Base scraper for legal code pages. Usage: instantiate CodeScraper with
start_url and domain, then call scrape_and_upload() or scrape().
"""
from __future__ import annotations

import logging
import re
import time
from pathlib import Path
from typing import List, Tuple, Deque
from collections import deque
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

DEFAULT_TIMEOUT = 90  # leginfo.legislature.ca.gov can be slow; 30s was causing ReadTimeout
SLEEP_BETWEEN_REQUESTS = 1.5
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; link-scraper/1.0)"}
RETRIES = 5
BACKOFF = 3
DEFAULT_CONTENT_SELECTOR = "div.tab_content"
DEFAULT_SECTION_LINE_RE = re.compile(
    r"^\s*\d+\.\s*(?:\n\s*)?[A-Za-z(]", re.MULTILINE
)

PART_RE = re.compile(r"^part[\s_-]*\d+", re.IGNORECASE)
CHAPTER_RE = re.compile(r"^chapter[\s_-]*\d+", re.IGNORECASE)
ARTICLE_RE = re.compile(r"^article[\s_-]*\d+", re.IGNORECASE)
DIVISION_TEXT_RE = re.compile(r"division\s+\d", re.IGNORECASE)
HEADING_RE = re.compile(r"^(PART|DIVISION|CHAPTER|ARTICLE)\s+\d", re.IGNORECASE)
PART_NUM_RE = re.compile(r"part\D*(\d+)", re.IGNORECASE)
DIV_NUM_RE = re.compile(r"division\D*([\d.]+)", re.IGNORECASE)


class CodeScraper:
    """
    Scrape a legal code TOC and save/upload cleaned text.
    """

    def __init__(
        self,
        start_url: str,
        domain: str,
        stop_phrase: str,
        stop_early: bool = False,
        out_dir: str | Path | None = None,
        storage=None,
        logger: logging.Logger | None = None,
        include_headers: bool = False,
        extra_toc_texts: List[str] | List[re.Pattern] | None = None,
        file_prefix: str = "",
    ):
        if not domain:
            raise ValueError("domain is required (e.g., family_code, evidence_code)")
        self.start_url = start_url
        self.domain = domain
        self.stop_phrase = stop_phrase
        self.stop_early = stop_early
        _root = Path(__file__).resolve().parents[5]
        default_out_dir = _root / "downloads" / "california_law"
        self.out_dir = Path(out_dir) if out_dir else default_out_dir
        self.storage = storage
        self.log = logger or logging.getLogger(__name__)
        self.include_headers = include_headers
        self.file_prefix = (file_prefix or "").lower()

        self.extra_toc_patterns: List[re.Pattern] = []
        if extra_toc_texts:
            for p in extra_toc_texts:
                if isinstance(p, re.Pattern):
                    self.extra_toc_patterns.append(p)
                else:
                    self.extra_toc_patterns.append(re.compile(str(p), re.IGNORECASE))

        self.section_line_re = DEFAULT_SECTION_LINE_RE

    def fetch_html(self, url: str) -> BeautifulSoup:
        time.sleep(SLEEP_BETWEEN_REQUESTS)
        last_exc = None
        for attempt in range(1, RETRIES + 1):
            try:
                resp = requests.get(url, timeout=DEFAULT_TIMEOUT, headers=HEADERS)
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
                    "Connection error (%s) at %s, attempt %d/%d. Waiting %ss and retrying...",
                    type(exc).__name__,
                    url,
                    attempt,
                    RETRIES,
                    wait,
                )
                time.sleep(wait)
        raise last_exc if last_exc else RuntimeError(f"Failed to fetch {url}")

    def fetch_links(self, url: str) -> List[Tuple[str, str]]:
        soup = self.fetch_html(url)
        links: List[Tuple[str, str]] = []
        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            full = urljoin(url, href)
            text = a.get_text(strip=True) or ""
            links.append((full, text))
        return links

    def filter_divisions(self, unique_links: List[Tuple[str, str]]) -> List[Tuple[str, str]]:
        patterns = [DIVISION_TEXT_RE, *self.extra_toc_patterns]
        return [(u, t) for (u, t) in unique_links if any(p.search(t) for p in patterns)]

    def dedup_links(self, links: List[Tuple[str, str]]) -> List[Tuple[str, str]]:
        seen = {}
        for url, text in links:
            if url not in seen:
                seen[url] = text
        return list(seen.items())

    def fetch_sections(self, div_url: str) -> List[Tuple[str, str]]:
        soup = self.fetch_html(div_url)
        results: List[Tuple[str, str]] = []
        for a in soup.find_all("a", href=True):
            text = a.get_text(strip=True) or ""
            href = a["href"].strip()
            full = urljoin(div_url, href)
            if (
                PART_RE.match(text)
                or PART_RE.match(href)
                or CHAPTER_RE.match(text)
                or CHAPTER_RE.match(href)
                or ARTICLE_RE.match(text)
                or ARTICLE_RE.match(href)
            ):
                results.append((full, text))
        return self.dedup_links(results)

    def fetch_articles_in_page(self, soup: BeautifulSoup, base_url: str) -> List[Tuple[str, str]]:
        results: List[Tuple[str, str]] = []
        for a in soup.find_all("a", href=True):
            text = a.get_text(strip=True) or ""
            href = a["href"].strip()
            full = urljoin(base_url, href)
            if ARTICLE_RE.match(text) or ARTICLE_RE.match(href):
                results.append((full, text))
        return self.dedup_links(results)

    def extract_text(self, soup: BeautifulSoup) -> str:
        node = soup.select_one(DEFAULT_CONTENT_SELECTOR)
        if node:
            return node.get_text(separator="\n", strip=True)
        body = soup.body
        if body:
            return body.get_text(separator="\n", strip=True)
        return soup.get_text(separator="\n", strip=True)

    @staticmethod
    def normalize_text(text: str) -> str:
        text = text.replace("\u00a0", " ")
        lines = []
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
        return "\n".join(lines)

    @staticmethod
    def trim_to_first_heading(text: str) -> str:
        lines = text.splitlines()
        heading_idxs = [idx for idx, line in enumerate(lines) if HEADING_RE.match(line.strip())]
        if not heading_idxs:
            return text
        for idx in heading_idxs:
            lookahead = lines[idx + 1 : idx + 8]
            for la in lookahead:
                la_s = la.strip()
                if not la_s:
                    continue
                if re.match(r"^\d+\.", la_s):
                    return "\n".join(lines[idx:])
                if la_s.startswith("(") and ("Chapter" in la_s or "enacted" in la_s):
                    return "\n".join(lines[idx:])
                if len(la_s) > 60:
                    return "\n".join(lines[idx:])
        return "\n".join(lines[heading_idxs[0]:])

    def _strip_leading_toc_if_redundant(self, text: str, known_keys: set) -> str:
        lines = text.splitlines()
        n = len(lines)
        if n == 0:
            return text
        toc_candidates = []
        i = 0
        while i < min(n, 20):
            line = lines[i].strip()
            if not line:
                i += 1
                continue
            m = re.match(r'^(PART|DIVISION|CHAPTER|ARTICLE)\b[^0-9\n]*?(\d+)', line, re.IGNORECASE)
            if m:
                key = f"{m.group(1).upper()} {m.group(2)}"
                next_line = lines[i + 1].strip() if i + 1 < n else ""
                if re.match(r'^\d+\s*[-–]\s*\d+$', next_line) or re.search(r'\d+\s*[-–]\s*\d+', line):
                    toc_candidates.append((i, key))
                    if re.match(r'^\d+\s*[-–]\s*\d+$', next_line):
                        i += 2
                        continue
                    i += 1
                    continue
            break
        if len(toc_candidates) < 2:
            return text
        matches = sum(1 for _, k in toc_candidates if k in known_keys)
        if matches == 0:
            return text
        last_idx = toc_candidates[-1][0]
        end = last_idx + 1
        if end < n and re.match(r'^\d+\s*[-–]\s*\d+$', lines[end].strip()):
            end += 1
        while end < n and not lines[end].strip():
            end += 1
        return "\n".join(lines[end:])

    def scrape_content(self) -> str:
        self.log.info("Fetching TOC: %s", self.start_url)
        links = self.fetch_links(self.start_url)
        unique_links = self.dedup_links(links)
        div_links = self.filter_divisions(unique_links)
        self.log.info("Initial links found (DIVISION + extras): %d", len(div_links))

        collected_sections: Deque[Tuple[str, str]] = deque()
        for div_url, div_text in div_links:
            self.log.info("Fetching PART/CHAPTER/ARTICLE in %s", div_url)
            sections = self.fetch_sections(div_url)
            if not sections:
                self.log.info("No PART/CHAPTER/ARTICLE found in %s — enqueuing the DIVISION page", div_url)
                collected_sections.append((div_url, div_text))
            else:
                for s in sections:
                    collected_sections.append(s)

        collected_sections = deque(self.dedup_links(list(collected_sections)))
        known_keys: set = set()
        for _url, txt in collected_sections:
            m = re.match(r'^(PART|DIVISION|CHAPTER|ARTICLE)\b[^0-9\n]*?(\d+)', txt or "", re.IGNORECASE)
            if m:
                known_keys.add(f"{m.group(1).upper()} {m.group(2)}")

        self.log.info("Total PART/CHAPTER/ARTICLE collected: %d", len(collected_sections))

        buffer: List[str] = []
        stop_hit = False
        stop_url = None
        seen_urls = set()

        while collected_sections:
            sec_url, sec_text = collected_sections.popleft()
            if sec_url in seen_urls:
                continue
            seen_urls.add(sec_url)

            is_part = PART_RE.match(sec_text) or PART_RE.match(sec_url)
            is_chapter = CHAPTER_RE.match(sec_text) or CHAPTER_RE.match(sec_url)
            is_article = ARTICLE_RE.match(sec_text) or ARTICLE_RE.match(sec_url)
            kind = "PART" if is_part else "CHAPTER" if is_chapter else "ARTICLE" if is_article else "SECTION"

            self.log.info("Reading %s %s", kind, sec_url)
            soup = self.fetch_html(sec_url)

            if kind == "CHAPTER":
                inner_articles = self.fetch_articles_in_page(soup, sec_url)
                for art_url, art_text in inner_articles:
                    if art_url not in seen_urls:
                        collected_sections.append((art_url, art_text))

            text = self.extract_text(soup)
            text = self._strip_leading_toc_if_redundant(text, known_keys)
            text = self.trim_to_first_heading(text)
            text = self.normalize_text(text)
            if not self.section_line_re.search(text):
                self.log.info("Page %s does not contain numbered line; skipping.", sec_url)
                continue
            if self.include_headers:
                buffer.append(f"--- {kind} URL: {sec_url} | text='{sec_text}' ---")
            buffer.append(text)

            if self.stop_phrase in text:
                stop_hit = True
                stop_url = sec_url
                if self.stop_early:
                    prefix, _, _ = text.partition(self.stop_phrase)
                    buffer[-1] = prefix + self.stop_phrase
                    break

        final_text = "\n\n".join(buffer)
        if stop_hit:
            self.log.info("Stop phrase found in %s (STOP_EARLY=%s)", stop_url, self.stop_early)
        else:
            self.log.info("Stop phrase not found; full content collected.")
        return final_text

    def save_local(self, content: str, out_path: str | Path | None = None) -> Path:
        filename = f"{self.file_prefix}{self.domain}_excerpt.txt".lower()
        target = Path(out_path) if out_path else self.out_dir / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "w", encoding="utf-8") as f:
            f.write(content)
        self.log.info("Content saved to %s", target)
        return target

    def upload(self, content: str, blob_name: str | None = None) -> str:
        if not self.storage:
            raise RuntimeError("storage not configured")
        local_default = f"{self.file_prefix}{self.domain}_excerpt.txt"
        chosen = (blob_name or local_default).lower()
        if not self.storage.upload_text(content, chosen):
            raise RuntimeError(f"Failed to upload {chosen}")
        self.log.info("TXT uploaded to blob %s/%s", self.storage.raw_container, chosen)
        return chosen

    def scrape(self, out_path: str | Path | None = None) -> Path:
        content = self.scrape_content()
        return self.save_local(content, out_path=out_path)

    def scrape_and_upload(self, out_path: str | Path | None = None, blob_name: str | None = None) -> Path:
        content = self.scrape_content()
        local_path = self.save_local(content, out_path=out_path)
        self.upload(content, blob_name=blob_name)
        return local_path
