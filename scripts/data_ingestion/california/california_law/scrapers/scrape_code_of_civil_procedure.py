"""
Specialized wrapper for the Code of Civil Procedure using the base CodeScraper.
"""

from __future__ import annotations

import logging

from .code_scraper import CodeScraper

START_URL = "https://leginfo.legislature.ca.gov/faces/codesTOCSelected.xhtml?tocCode=CCP&tocTitle=+Code+of+Civil+Procedure+-+CCP"
STOP_PHRASE = ""
BLOB_PREFIX = "CCP_"
BLOB_NAME = f"{BLOB_PREFIX}code_of_civil_procedure.txt"

LOG = logging.getLogger(__name__)


def make_scraper(storage=None) -> CodeScraper:
    """Create the Code of Civil Procedure scraper."""
    return CodeScraper(
        start_url=START_URL,
        domain="code_of_civil_procedure",
        stop_phrase=STOP_PHRASE,
        stop_early=False,
        storage=storage,
        logger=LOG,
        include_headers=False,
        file_prefix=BLOB_PREFIX,
        extra_toc_texts=[
            "TITLE OF ACT",
            "THE CODE OF CIVIL",
            "PRELIMINARY PROVISIONS",
            "PART"
        ]
    )
