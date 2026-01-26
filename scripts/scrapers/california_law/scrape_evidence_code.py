from __future__ import annotations

"""
Wrapper for Evidence Code using CodeScraper.

No CLI; to run import and call run_evidence_code() or instantiate make_scraper().
"""

import logging
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[3]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from backend.storage.blob_storage import LegalDocsStorage
from scripts.scrapers.california_law.code_scraper import CodeScraper

START_URL = "https://leginfo.legislature.ca.gov/faces/codesTOCSelected.xhtml?tocCode=EVID&tocTitle=+Evidence+Code+-+EVID"
# Adjust the stop phrase as needed for the Evidence Code; leave empty if none.
STOP_PHRASE = ""
BLOB_PREFIX = "EVID_"
BLOB_NAME = f"{BLOB_PREFIX}evidence_code.txt"

LOG = logging.getLogger(__name__)


def make_scraper(
    storage: LegalDocsStorage | None = None,
) -> CodeScraper:
    """
    Create the Evidence Code scraper. Domain set to 'evidence_code'.
    """
    storage = storage or LegalDocsStorage()
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


def run_evidence_code(
    storage: LegalDocsStorage | None = None,
    out_path: str | Path | None = None,
    blob_name: str | None = None,
) -> Path:
    """
    Run the Evidence Code scrape, save locally and upload to blob.
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
        raise SystemExit("BLOB_NAME not defined; configure in the file.")
    run_evidence_code(blob_name=BLOB_NAME)

