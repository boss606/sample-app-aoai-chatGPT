"""
Base scraper for legal code pages. Usage: instantiate CodeScraper with
start_url and domain, then call scrape_and_upload() or scrape().
"""
from __future__ import annotations

import logging
import os
import re
import time
from pathlib import Path
from typing import List, Tuple, Deque
from collections import deque
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

# Only rely on defaults for HTTP; storage/domain must be provided by caller.
DEFAULT_TIMEOUT = 30
SLEEP_BETWEEN_REQUESTS = 1  # seconds
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; link-scraper/1.0)"}
RETRIES = 3
BACKOFF = 2  # seconds base backoff for retryable errors
DEFAULT_CONTENT_SELECTOR = "div.tab_content"  # default scope to extract text
# Detect section lines in the form "1. Text" and also when the number is on one
# line and the text on the next (e.g., "1.\nThis act ..."). Reject pure numeric
# ranges like "116.110-116.950" by requiring the first non-space after the dot
# to be a letter or "(".
DEFAULT_SECTION_LINE_RE = re.compile(
    r"^\s*\d+\.\s*(?:\n\s*)?[A-Za-z(]", re.MULTILINE
)

PART_RE = re.compile(r"^part[\s_-]*\d+", re.IGNORECASE)
CHAPTER_RE = re.compile(r"^chapter[\s_-]*\d+", re.IGNORECASE)
ARTICLE_RE = re.compile(r"^article[\s_-]*\d+", re.IGNORECASE)
DIVISION_TEXT_RE = re.compile(r"division\s+\d", re.IGNORECASE)
# Include DIVISION here so we don't trim away Division headers from the top of pages
HEADING_RE = re.compile(r"^(PART|DIVISION|CHAPTER|ARTICLE)\s+\d", re.IGNORECASE)
PART_NUM_RE = re.compile(r"part\D*(\d+)", re.IGNORECASE)
DIV_NUM_RE = re.compile(r"division\D*([\d.]+)", re.IGNORECASE)


class CodeScraper:
    """
    Scrape a legal code TOC and save/upload cleaned text.
    Caller must provide:
      - start_url: TOC URL of the code
      - domain: logical name (e.g., "family_code", "evidence_code")
    Optional:
      - stop_phrase: phrase to detect early stop
      - stop_early: if True, truncate at first occurrence
      - out_dir: where to save local TXT
      - use_domain_subfolder: if True, blob path is <domain>/<file>
      - extra_toc_texts: optional list of texts/regex to open in addition to
        DIVISION (keeps the default if not provided)
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
        # Default downloads folder under shared scrapers/downloads/california_law
        default_out_dir = Path(__file__).resolve().parents[2] / "downloads" / "california_law"
        self.out_dir = Path(out_dir) if out_dir else default_out_dir
        self.storage = storage
        self.log = logger or logging.getLogger(__name__)
        self.include_headers = include_headers
        # Force lowercase prefix to keep filenames consistent across local and blob.
        self.file_prefix = (file_prefix or "").lower()

        # extra_toc_texts: optional list of patterns used to seed scraping from the TOC
        # in addition to DIVISION links. Defaults to empty (DIVISION-only behavior).
        self.extra_toc_patterns: List[re.Pattern] = []
        if extra_toc_texts:
            for p in extra_toc_texts:
                if isinstance(p, re.Pattern):
                    self.extra_toc_patterns.append(p)
                else:
                    self.extra_toc_patterns.append(re.compile(str(p), re.IGNORECASE))

        # Always enforce that pages contain numbered lines in the expected pattern.
        self.section_line_re = DEFAULT_SECTION_LINE_RE

    # ---------- HTTP helpers ----------
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

    # ---------- parsing helpers ----------
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
        """Inside a CHAPTER page, capture ARTICLE links."""
        results: List[Tuple[str, str]] = []
        for a in soup.find_all("a", href=True):
            text = a.get_text(strip=True) or ""
            href = a["href"].strip()
            full = urljoin(base_url, href)
            if ARTICLE_RE.match(text) or ARTICLE_RE.match(href):
                results.append((full, text))
        return self.dedup_links(results)

    def extract_text(self, soup: BeautifulSoup) -> str:
        # try to scope to the main content container; fall back to body if missing
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
        """
        Trim leading content up to the first meaningful heading. This is more
        conservative than a naive search: it will skip short TOC-like clusters at the
        top of a page (e.g., a block listing "CHAPTER 4 / ARTICLE 1 / ARTICLE 2"
        with numeric ranges) and attempt to start at the first heading that is
        followed by actual body content (a section number like '350.' or a
        parenthetical enactment line).
        """
        lines = text.splitlines()
        # collect heading line indices
        heading_idxs = [idx for idx, line in enumerate(lines) if HEADING_RE.match(line.strip())]
        if not heading_idxs:
            return text

        # prefer a heading that is followed by a content-like line within a few
        # lines. Heuristics: a content line may start with a section number (e.g., '350.'),
        # a parenthetical '( Chapter', or simply be a longer sentence (>60 chars).
        for idx in heading_idxs:
            lookahead = lines[idx + 1 : idx + 8]
            for la in lookahead:
                la_s = la.strip()
                if not la_s:
                    continue
                # explicit section number like '350.' or '350. Some text'
                if re.match(r"^\d+\.", la_s):
                    return "\n".join(lines[idx:])
                # a parenthetical enactment line '( Chapter ... )' or similar
                if la_s.startswith("(") and ("Chapter" in la_s or "enacted" in la_s):
                    return "\n".join(lines[idx:])
                # a long line is likely body text rather than a TOC entry
                if len(la_s) > 60:
                    return "\n".join(lines[idx:])

        # fallback: return from first detected heading
        return "\n".join(lines[heading_idxs[0]:])

    @staticmethod
    def extract_part_number(value: str) -> str | None:
        m = PART_NUM_RE.search(value)
        return m.group(1) if m else None

    @staticmethod
    def extract_division_number(value: str) -> str | None:
        m = DIV_NUM_RE.search(value)
        return m.group(1) if m else None

    def _strip_leading_toc_if_redundant(self, text: str, known_keys: set) -> str:
        """Remove a leading mini-TOC block (CHAPTER/ARTICLE lines and numeric ranges)
        when those headings are present in known_keys (they will be scraped later).
        This function is conservative: it only strips a block at the top of the page
        when multiple heading-like lines are detected and at least one matches a
        known key.
        """
        lines = text.splitlines()
        n = len(lines)
        if n == 0:
            return text

        toc_candidates = []  # list of (start_index, key)
        i = 0
        # only inspect the top of the page
        while i < min(n, 20):
            line = lines[i].strip()
            if not line:
                i += 1
                continue
            # heading like 'CHAPTER 4' or 'ARTICLE 1'
            m = re.match(r'^(PART|DIVISION|CHAPTER|ARTICLE)\b[^0-9\n]*?(\d+)', line, re.IGNORECASE)
            if m:
                key = f"{m.group(1).upper()} {m.group(2)}"
                # check if next line is a numeric range (e.g., '350-406')
                next_line = lines[i + 1].strip() if i + 1 < n else ""
                if re.match(r'^\d+\s*[-–]\s*\d+$', next_line) or re.search(r'\d+\s*[-–]\s*\d+', line):
                    toc_candidates.append((i, key))
                    # skip the range line if present
                    if re.match(r'^\d+\s*[-–]\s*\d+$', next_line):
                        i += 2
                        continue
                    i += 1
                    continue
            break

        if len(toc_candidates) < 2:
            return text

        # If at least one of the candidate keys is in known_keys, strip the block.
        matches = sum(1 for _, k in toc_candidates if k in known_keys)
        if matches == 0:
            return text

        # determine end index of TOC block
        last_idx = toc_candidates[-1][0]
        end = last_idx + 1
        if end < n and re.match(r'^\d+\s*[-–]\s*\d+$', lines[end].strip()):
            end += 1
        # skip following blank lines
        while end < n and not lines[end].strip():
            end += 1

        return "\n".join(lines[end:])

    # ---------- main scrape ----------
    def scrape_content(self) -> str:
        self.log.info("Fetching TOC: %s", self.start_url)
        links = self.fetch_links(self.start_url)
        unique_links = self.dedup_links(links)

        div_links = self.filter_divisions(unique_links)
        self.log.info("Initial links found (DIVISION + extras): %d", len(div_links))

        collected_sections: Deque[Tuple[str, str]] = deque()
        # For each division link, fetch PART/CHAPTER/ARTICLE links inside it.
        # If none are found, enqueue the division page itself for scraping so
        # we don't skip divisions that contain body text directly on the page.
        for div_url, div_text in div_links:
            self.log.info("Fetching PART/CHAPTER/ARTICLE in %s", div_url)
            sections = self.fetch_sections(div_url)
            if not sections:
                self.log.info(
                    "No PART/CHAPTER/ARTICLE found in %s — enqueuing the DIVISION page for scraping",
                    div_url,
                )
                collected_sections.append((div_url, div_text))
            else:
                for s in sections:
                    collected_sections.append(s)

        # dedup inicial preservando ordem
        collected_sections = deque(self.dedup_links(list(collected_sections)))
        # Build a set of known heading keys (e.g., 'CHAPTER 4', 'ARTICLE 1') that we
        # will scrape later. This is used to remove duplicated mini-TOC blocks from
        # division pages (the mini-TOC lists headings that are scraped separately).
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
            if is_part:
                kind = "PART"
            elif is_chapter:
                kind = "CHAPTER"
            elif is_article:
                kind = "ARTICLE"
            else:
                kind = "SECTION"

            self.log.info("Reading %s %s", kind, sec_url)
            soup = self.fetch_html(sec_url)

            # If CHAPTER, collect ARTICLEs within the page and enqueue
            if kind == "CHAPTER":
                inner_articles = self.fetch_articles_in_page(soup, sec_url)
                for art_url, art_text in inner_articles:
                    if art_url not in seen_urls:
                        collected_sections.append((art_url, art_text))

            text = self.extract_text(soup)
            # Remove a leading mini-TOC on division pages when those headings are
            # already scheduled for scraping (avoids duplicated heading clusters).
            text = self._strip_leading_toc_if_redundant(text, known_keys)
            text = self.trim_to_first_heading(text)
            text = self.normalize_text(text)
            # Enforce numbered-section format (e.g., "1. Text").
            if not self.section_line_re.search(text):
                self.log.info(
                    "Page %s does not contain a numbered line in the expected pattern; skipping.",
                    sec_url,
                )
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

    # ---------- persistence ----------
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
            raise RuntimeError("storage not configured; set storage in the constructor to enable upload.")
        local_default = f"{self.file_prefix}{self.domain}_excerpt.txt"
        chosen = (blob_name or local_default).lower()
        if not self.storage.upload_text(content, chosen):
            raise RuntimeError(f"Failed to upload {chosen} to blob {self.storage.raw_container}")
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

