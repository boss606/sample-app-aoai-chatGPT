from __future__ import annotations

"""
Runner for PDF-based scraping of California Rules of Court.
Outputs to downloads/rules_of_court/<prefix>_rules_of_court.txt and can upload to Azure Blob.
"""

import logging
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[3]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from backend.storage.blob_storage import LegalDocsStorage
from scripts.scrapers.california_rules_of_court.rules_scraper import (
    RulesPdfScraper,
    DEFAULT_CODE_PREFIX,
    DEFAULT_START_URL,
)

DEFAULT_BLOB_NAME = f"{DEFAULT_CODE_PREFIX}_rules_of_court.txt"


def run_rules_pdf(
    start_url: str = DEFAULT_START_URL,
    out_path: str | Path | None = None,
    code_prefix: str = DEFAULT_CODE_PREFIX,
    upload: bool = True,
    blob_name: str | None = None,
    storage: LegalDocsStorage | None = None,
) -> Path:
    scraper = RulesPdfScraper(
        start_url=start_url,
        code_prefix=code_prefix,
        storage=storage or (LegalDocsStorage() if upload else None),
    )
    target_path = out_path or scraper.out_dir / f"{code_prefix}_rules_of_court.txt"
    if upload:
        return scraper.scrape_and_upload(out_path=target_path, blob_name=blob_name or DEFAULT_BLOB_NAME)
    return scraper.scrape(out_path=target_path)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    run_rules_pdf()

