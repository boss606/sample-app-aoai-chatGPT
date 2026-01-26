from __future__ import annotations

"""
Specialized wrapper for the Code of Civil Procedure using the base CodeScraper.
"""

import logging
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[3]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from backend.storage.blob_storage import LegalDocsStorage
from scripts.scrapers.california_law.code_scraper import CodeScraper

START_URL = "https://leginfo.legislature.ca.gov/faces/codesTOCSelected.xhtml?tocCode=CCP&tocTitle=+Code+of+Civil+Procedure+-+CCP"
STOP_PHRASE = ""
BLOB_PREFIX = "CCP_"
BLOB_NAME = f"{BLOB_PREFIX}code_of_civil_procedure.txt"

LOG = logging.getLogger(__name__)


def make_scraper(
    storage: LegalDocsStorage | None = None,
) -> CodeScraper:
    """
    Create the Code of Civil Procedure scraper. Domain is set to code_of_civil_procedure.
    """
    storage = storage or LegalDocsStorage()
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


def run_family_code(
    storage: LegalDocsStorage | None = None,
    out_path: str | Path | None = None,
    blob_name: str | None = None,
) -> Path:
    """
    Run the Family Code scraper, save results locally, and upload to blob storage.
    """
    scraper = make_scraper(storage=storage)
    chosen = blob_name or BLOB_NAME
    if not chosen.startswith(BLOB_PREFIX):
        chosen = f"{BLOB_PREFIX}{chosen}"
    chosen = chosen.lower()
    return scraper.scrape_and_upload(out_path=out_path, blob_name=chosen)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    if not BLOB_NAME:
        raise SystemExit("BLOB_NAME is not defined; please configure it in the file.")
    run_family_code(blob_name=BLOB_NAME)

