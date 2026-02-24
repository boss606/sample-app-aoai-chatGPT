"""
Specialized wrapper for the Family Code using the base CodeScraper.
"""

from __future__ import annotations

import logging

from .code_scraper import CodeScraper

START_URL = "https://leginfo.legislature.ca.gov/faces/codesTOCSelected.xhtml?tocCode=FAM&tocTitle=+Family+Code+-+FAM"
STOP_PHRASE = ""
BLOB_PREFIX = "FAM_"
BLOB_NAME = f"{BLOB_PREFIX}family_code.txt"

LOG = logging.getLogger(__name__)


def make_scraper(storage=None) -> CodeScraper:
    """Create the Family Code scraper. Domain is set to family_code."""
    return CodeScraper(
        start_url=START_URL,
        domain="family_code",
        stop_phrase=STOP_PHRASE,
        stop_early=False,
        storage=storage,
        logger=LOG,
        include_headers=False,
        file_prefix=BLOB_PREFIX,
    )
