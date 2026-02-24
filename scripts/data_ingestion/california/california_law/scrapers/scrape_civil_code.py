"""
Specialized wrapper for the Civil Code using the base CodeScraper.
"""

from __future__ import annotations

import logging

from .code_scraper import CodeScraper

START_URL = "https://leginfo.legislature.ca.gov/faces/codesTOCSelected.xhtml?tocCode=CIV&tocTitle=+Civil+Code+-+CIV"
STOP_PHRASE = ""
BLOB_PREFIX = "CIV_"
BLOB_NAME = f"{BLOB_PREFIX}civil_code.txt"

LOG = logging.getLogger(__name__)


def make_scraper(storage=None) -> CodeScraper:
    """Create the Civil Code scraper. Domain is set to civil_code."""
    return CodeScraper(
        start_url=START_URL,
        domain="civil_code",
        stop_phrase=STOP_PHRASE,
        stop_early=False,
        storage=storage,
        logger=LOG,
        include_headers=False,
        file_prefix=BLOB_PREFIX,
        extra_toc_texts=[
            "TITLE OF ACT",
            "THE CIVIL CODE OF THE STATE OF CALIFORNIA",
            "PRELIMINARY PROVISIONS",
            "DEFINITIONS AND SOURCES OF LAW",
            "EFFECT OF THE 1872 CODES"
        ]
    )
