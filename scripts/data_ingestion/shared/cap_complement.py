"""
Complement CourtListener bulk with Harvard CAP data via CourtListener Search API.

When CourtListener bulk returns no docs: fetch all CAP-backed cases for the state.
When CourtListener bulk has docs: fetch only from max(date_filed) onward.

Uses CourtListener Search API (hosts CAP data) with jurisdiction + family law search terms.
"""

from __future__ import annotations

import logging
import os

from dotenv import load_dotenv

load_dotenv(override=False)
import re
import time
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode

import requests

from scripts.data_ingestion.base.document_schema import IngestibleDocument, make_document_id

log = logging.getLogger(__name__)

API_BASE = "https://www.courtlistener.com/api/rest/v4"
DEFAULT_TIMEOUT = 60
RETRIES = 4
BACKOFF = 3
PAGE_PAUSE = 0.5

# Family law terms for search (CourtListener q= param; one term per search to maximize recall)
FAMILY_SEARCH_TERMS = [
    "divorce",
    "custody",
    "matrimonial",
    "child support",
    "visitation",
    "paternity",
    "adoption",
    "dissolution",
]


def _fetch_json(url: str, headers: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    hdrs = headers or {}
    if "Accept" not in hdrs:
        hdrs["Accept"] = "application/json"
    hdrs.setdefault("User-Agent", "Mozilla/5.0 (compatible; courtlistener-cap-complement/1.0)")
    token = os.environ.get("COURTLISTENER_TOKEN")
    if token:
        hdrs["Authorization"] = f"Token {token}"

    last_exc = None
    for attempt in range(1, RETRIES + 1):
        try:
            resp = requests.get(url, timeout=DEFAULT_TIMEOUT, headers=hdrs)
            if resp.status_code in (429,) or 500 <= resp.status_code < 600:
                time.sleep(BACKOFF * attempt)
                continue
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as exc:
            last_exc = exc
            if attempt < RETRIES:
                time.sleep(BACKOFF * attempt)
    raise last_exc or RuntimeError(f"Failed to fetch {url}")


def _extract_text_from_opinion(data: Dict[str, Any]) -> str:
    """Get searchable text from opinion API response."""
    for key in ("plain_text", "html", "html_lawbox", "html_with_citations"):
        val = data.get(key)
        if val:
            if key == "plain_text":
                return (val or "").strip()
            # Strip HTML
            text = re.sub(r"<[^>]+>", " ", val)
            text = re.sub(r"\s+", " ", text).strip()
            if text:
                return text
    return ""


def _opinion_id_from_item(item: Dict[str, Any]) -> Optional[str]:
    """Extract opinion id from search result."""
    uri = item.get("resource_uri")
    if uri:
        m = re.search(r"/opinions?/(\d+)/?", uri)
        if m:
            return m.group(1)
    url = item.get("absolute_url") or ""
    m = re.search(r"/opinion/(\d+)/", url)
    return m.group(1) if m else None


def fetch_cap_complement(
    *,
    state_code: str,
    state_name: str,
    court_ids: List[str],
    court_domain: str,
    court_display_name: str,
    jurisdiction: str,
    source: str = "cap",
    since_date: Optional[date] = None,
    max_docs: Optional[int] = None,
    logger: Optional[logging.Logger] = None,
) -> List[IngestibleDocument]:
    """
    Fetch family-law cases from CourtListener Search API (CAP data) for a state.

    Args:
        state_code: e.g. ny, cal
        state_name: e.g. new_york
        court_ids: CourtListener court slugs (e.g. ny, nysupct)
        court_domain: for metadata
        court_display_name: for metadata
        jurisdiction: e.g. NY
        source: metadata source label (default cap)
        since_date: if set, only cases with date_filed >= since_date
        max_docs: optional cap on documents to fetch
        logger: optional logger

    Returns:
        List of IngestibleDocument.
    """
    slog = logger or log
    seen_ids: set[str] = set()
    docs: List[IngestibleDocument] = []

    for court_id in court_ids:
        for term in FAMILY_SEARCH_TERMS:
            if max_docs and len(docs) >= max_docs:
                break
            params: Dict[str, Any] = {
                "type": "o",
                "court": court_id,
                "q": term,
                "page_size": 100,
            }
            if since_date:
                params["filed_after"] = since_date.isoformat()
            url = f"{API_BASE}/search/?{urlencode(params)}"
            slog.info("CAP complement: court=%s q=%s", court_id, term)
            try:
                payload = _fetch_json(url)
            except Exception as exc:
                slog.warning("CAP search failed %s: %s", url, exc)
                continue
            for item in payload.get("results", []):
                if max_docs and len(docs) >= max_docs:
                    break
                op_id = _opinion_id_from_item(item)
                if not op_id or op_id in seen_ids:
                    continue
                seen_ids.add(op_id)
                # Fetch full opinion for text
                op_url = f"{API_BASE}/opinions/{op_id}/"
                try:
                    op_data = _fetch_json(op_url)
                except Exception as exc:
                    slog.warning("CAP opinion fetch failed %s: %s", op_url, exc)
                    continue
                content = _extract_text_from_opinion(op_data)
                if not content:
                    continue
                date_filed = (item.get("dateFiled") or op_data.get("date_filed") or "").strip()
                if isinstance(date_filed, str) and len(date_filed) > 10:
                    date_filed = date_filed[:10]
                doc_id = make_document_id(state_code, source, f"op_{op_id}")
                meta = {
                    "domain": court_domain,
                    "source": source,
                    "court": court_display_name,
                    "jurisdiction": jurisdiction,
                    "case_name": (item.get("caseName") or "").strip(),
                    "date_filed": date_filed,
                    "doc_type": "case",
                    "state": state_name,
                }
                docs.append(IngestibleDocument(id=doc_id, content=content, metadata=meta))
                slog.info("CAP: added %s (%s)", doc_id, item.get("caseName", "")[:50])
                next_url = payload.get("next")
            while next_url and (not max_docs or len(docs) < max_docs):
                time.sleep(PAGE_PAUSE)
                try:
                    payload = _fetch_json(next_url)
                except Exception as exc:
                    slog.warning("CAP pagination failed: %s", exc)
                    break
                for item in payload.get("results", []):
                    if max_docs and len(docs) >= max_docs:
                        break
                    op_id = _opinion_id_from_item(item)
                    if not op_id or op_id in seen_ids:
                        continue
                    seen_ids.add(op_id)
                    op_url = f"{API_BASE}/opinions/{op_id}/"
                    try:
                        op_data = _fetch_json(op_url)
                    except Exception:
                        continue
                    content = _extract_text_from_opinion(op_data)
                    if not content:
                        continue
                    date_filed = (item.get("dateFiled") or "")[:10] if item.get("dateFiled") else ""
                    doc_id = make_document_id(state_code, source, f"op_{op_id}")
                    meta = {
                        "domain": court_domain,
                        "source": source,
                        "court": court_display_name,
                        "jurisdiction": jurisdiction,
                        "case_name": (item.get("caseName") or "").strip(),
                        "date_filed": date_filed,
                        "doc_type": "case",
                        "state": state_name,
                    }
                    docs.append(IngestibleDocument(id=doc_id, content=content, metadata=meta))
                next_url = payload.get("next")

    slog.info("CAP complement: fetched %d docs for %s", len(docs), state_name)
    return docs
