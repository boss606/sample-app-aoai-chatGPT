"""
Run California Rules of Court scraper and produce IngestibleDocument.

Uses local rules_scraper (PDF-based).
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[4]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))
if str(ROOT_DIR / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT_DIR / "scripts"))

from scripts.data_ingestion.base.document_schema import IngestibleDocument, make_document_id
from scripts.data_ingestion.california.california_rules_of_court.scrapers.rules_scraper import RulesPdfScraper

LOG = logging.getLogger(__name__)

STATE_CODE = "cal"
SOURCE = "crc"
JURISDICTION = "CA"
STATE_NAME = "california"


def run_california_rules_of_court_to_json(
    out_dir: Path | None = None,
) -> list[IngestibleDocument]:
    """
    Run California Rules of Court scraper and return IngestibleDocument.

    Args:
        out_dir: Directory for TXT output. Defaults to scripts/data_ingestion/california/california_rules_of_court/output.

    Returns:
        List with one IngestibleDocument for the rules.
    """
    if out_dir is None:
        out_dir = Path(__file__).resolve().parent / "output"
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    doc_id = make_document_id(STATE_CODE, SOURCE, "rules_of_court")

    scraper = RulesPdfScraper(
        out_dir=out_dir,
        code_prefix="crc",
        storage=None,
    )

    LOG.info("Scraping California Rules of Court -> %s", doc_id)
    local_path = scraper.scrape()

    if not local_path or not local_path.exists():
        LOG.warning("No output for rules of court; skipping")
        return []

    content = local_path.read_text(encoding="utf-8")
    meta = {
        "domain": "rules_of_court",
        "source": "california_rules_of_court",
        "jurisdiction": JURISDICTION,
        "doc_type": "rule",
        "state": STATE_NAME,
    }
    doc = IngestibleDocument(id=doc_id, content=content, metadata=meta)
    LOG.info("Produced %s (%d chars)", doc_id, len(content))
    return [doc]


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    from scripts.data_ingestion.base.pipeline import save_docs_local, upload_json_to_blob
    from scripts.data_ingestion.base.state_config import get_state_config

    docs = run_california_rules_of_court_to_json()
    if docs:
        paths = save_docs_local(docs, Path(__file__).resolve().parent / "output", source="california_rules_of_court")
        print(f"Saved {len(paths)} file(s) to {paths[0].parent}")
        from backend.storage.blob_storage import LegalDocsStorage
        storage = LegalDocsStorage()
        state = get_state_config("california", root_dir=ROOT_DIR)
        n = upload_json_to_blob(docs, state, storage, source="california_rules_of_court")
        print(f"Uploaded {n} document(s) to blob")
    print(f"Produced {len(docs)} document(s)")
