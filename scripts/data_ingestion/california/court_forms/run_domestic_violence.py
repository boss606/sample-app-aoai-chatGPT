"""
Run Domestic Violence forms scraper, produce JSON, save locally and upload to blob.
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
from scripts.data_ingestion.california.court_forms.scrapers.court_forms_scraper import CourtFormsScraper

LOG = logging.getLogger(__name__)

STATE_CODE = "cal"
SOURCE = "court_forms"
JURISDICTION = "CA"
STATE_NAME = "california"
CATEGORY = "domestic_violence"
SEARCH_URL = "https://selfhelp.courts.ca.gov/find-forms?query=domestic%20violence"


def run_domestic_violence_to_json(out_dir: Path | None = None) -> list[IngestibleDocument]:
    out_dir = out_dir or Path(__file__).resolve().parent / "output"
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    doc_id = make_document_id(STATE_CODE, SOURCE, CATEGORY)
    scraper = CourtFormsScraper(
        category=CATEGORY,
        search_url=SEARCH_URL,
        out_dir=out_dir,
        aggregate_file=out_dir / f"{CATEGORY}.txt",
        storage=None,
    )

    LOG.info("Scraping %s -> %s", CATEGORY, doc_id)
    paths = scraper.scrape_all()
    if not paths or not paths[0].exists():
        LOG.warning("No output for %s", CATEGORY)
        return []

    content = paths[0].read_text(encoding="utf-8")
    meta = {
        "domain": "court_forms",
        "source": "court_forms",
        "jurisdiction": JURISDICTION,
        "doc_type": "court_form",
        "form_category": CATEGORY,
        "state": STATE_NAME,
    }
    return [IngestibleDocument(id=doc_id, content=content, metadata=meta)]


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    from scripts.data_ingestion.base.pipeline import save_docs_local, upload_json_to_blob
    from scripts.data_ingestion.base.state_config import get_state_config

    docs = run_domestic_violence_to_json()
    if docs:
        paths = save_docs_local(docs, Path(__file__).resolve().parent / "output", source="court_forms")
        print(f"Saved {len(paths)} file(s) to {paths[0].parent}")
        from backend.storage.blob_storage import LegalDocsStorage
        storage = LegalDocsStorage()
        state = get_state_config("california", root_dir=ROOT_DIR)
        n = upload_json_to_blob(docs, state, storage, source="court_forms")
        print(f"Uploaded {n} document(s) to blob")
    print(f"Produced {len(docs)} document(s)")
