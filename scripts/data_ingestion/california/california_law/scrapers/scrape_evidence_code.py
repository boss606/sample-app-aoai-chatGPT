"""
Wrapper for Evidence Code using CodeScraper.
"""

from __future__ import annotations

import logging

from .code_scraper import CodeScraper

START_URL = "https://leginfo.legislature.ca.gov/faces/codesTOCSelected.xhtml?tocCode=EVID&tocTitle=+Evidence+Code+-+EVID"
STOP_PHRASE = ""
BLOB_PREFIX = "EVID_"
BLOB_NAME = f"{BLOB_PREFIX}evidence_code.txt"

LOG = logging.getLogger(__name__)


def make_scraper(storage=None) -> CodeScraper:
    """Create the Evidence Code scraper."""
    return CodeScraper(
        start_url=START_URL,
        domain="evidence_code",
        stop_phrase=STOP_PHRASE,
        stop_early=False,
        storage=storage,
        logger=LOG,
        include_headers=False,
        file_prefix=BLOB_PREFIX,
    )
