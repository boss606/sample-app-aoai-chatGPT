from __future__ import annotations

"""
Scraper for the California Supreme Court using CourtListenerOpinionScraper.
"""

import logging
import sys
from pathlib import Path
from typing import List

ROOT_DIR = Path(__file__).resolve().parents[3]
BACKEND_DIR = ROOT_DIR / "backend"
for p in (ROOT_DIR, BACKEND_DIR):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from backend.storage.blob_storage import LegalDocsStorage

from scripts.scrapers.courtlistener.courtlistener_scraper import (
    CourtListenerOpinionScraper,
)

SEARCH_URL = (
    "https://www.courtlistener.com/api/rest/v4/search/?type=o&court=cal"
    "&q=marriage%20OR%20divorce%20OR%20custody%20OR%20visitation%20OR%20paternity"
    "%20OR%20%22child%20support%22%20OR%20%22spousal%20support%22%20OR%20%22Family%20Code%22"
    "%20OR%20%22Fam.%20Code%22"
)
COURT_ID = "cal"
COURT_NAME = "California Supreme Court"
FILE_PREFIX = "cal_"
OUT_DIR = Path(__file__).resolve().parents[1] / "downloads" / "courtlistener"
AGGREGATE_FILE = OUT_DIR / "cal_supreme_all.txt"

LOG = logging.getLogger(__name__)


def make_scraper(
    *,
    out_dir: str | Path | None = None,
    max_results: int | None = None,
    storage: LegalDocsStorage | None = None,
) -> CourtListenerOpinionScraper:
    return CourtListenerOpinionScraper(
        search_url=SEARCH_URL,
        court_id=COURT_ID,
        court_name=COURT_NAME,
        file_prefix=FILE_PREFIX,
        out_dir=out_dir or OUT_DIR,
        max_results=max_results,
        aggregate_file=AGGREGATE_FILE,
        storage=storage,
        logger=LOG,
    )


def run_cal_supreme(
    *,
    out_dir: str | Path | None = None,
    max_results: int | None = None,
    upload: bool = False,
    blob_name: str | None = None,
    storage: LegalDocsStorage | None = None,
) -> List[Path]:
    storage = storage or (LegalDocsStorage() if upload else None)
    scraper = make_scraper(out_dir=out_dir, max_results=max_results, storage=storage)
    if upload:
        return scraper.scrape_all_and_upload(blob_name=blob_name)
    return scraper.scrape_all()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    paths = run_cal_supreme(upload=True)
    LOG.info("Total saved (and uploaded aggregate): %d", len(paths))

