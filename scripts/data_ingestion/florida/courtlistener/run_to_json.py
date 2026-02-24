"""
Run CourtListener bulk filter (FL family law opinions) and produce IngestibleDocuments.

Downloads bulk data from shared location, filters by Florida courts and family law terms.
"""

from __future__ import annotations

import argparse
import bz2
import csv
import logging
import re
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[4]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))
if str(ROOT_DIR / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT_DIR / "scripts"))

from scripts.data_ingestion.shared.courtlistener_bulk import (
    SHARED_COURT,
    _find_latest_bz2,
    _get_raw_dataset_dir,
    download_dataset_for_court,
    load_state_clusters_and_metadata,
)
from scripts.data_ingestion.shared.family_law_terms import FAMILY_RE, MIN_TERMS
from scripts.data_ingestion.base.document_schema import IngestibleDocument, make_document_id

LOG = logging.getLogger(__name__)

STATE_CODE = "fla"
SOURCE = "courtlistener"
COURT_DOMAIN = "fla_supreme"
JURISDICTION = "FL"
STATE_NAME = "florida"
COURT_MATCHERS = ("fla", "Fla", "Florida Supreme", "FL Supreme", "Florida Appellate")
COURT_DISPLAY_NAME = "Florida Supreme Court"

csv.field_size_limit(sys.maxsize)


def _extract_searchable_text(row: dict) -> str:
    """Get text for keyword matching. Prefer plain_text; fallback to html/xml."""
    text = (row.get("plain_text") or "").strip()
    if text:
        return text
    for key in (
        "html",
        "html_lawbox",
        "html_columbia",
        "html_with_citations",
        "html_anon_2020",
        "xml_harvard",
    ):
        raw = (row.get(key) or "").strip()
        if not raw:
            continue
        text = re.sub(r"<[^>]+>", " ", raw)
        text = re.sub(r"\s+", " ", text).strip()
        if text:
            return text
    return ""


def _is_family_related(row: dict) -> bool:
    text = _extract_searchable_text(row)
    if not text:
        return False
    hits = FAMILY_RE.findall(text)
    if not hits:
        return False
    distinct = {h.lower() for h in hits}
    return len(distinct) >= MIN_TERMS


def run_courtlistener_to_json(
    out_dir: Path | None = None,
    limit_rows: int | None = None,
) -> list[IngestibleDocument]:
    """
    Download CourtListener bulk data, filter FL family law opinions, return IngestibleDocuments.
    """
    if out_dir is None:
        out_dir = Path(__file__).resolve().parent / "output"
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    raw_opinions = _get_raw_dataset_dir("opinions")
    raw_clusters = _get_raw_dataset_dir("clusters")
    raw_courts = _get_raw_dataset_dir("courts")
    raw_dockets = _get_raw_dataset_dir("dockets")

    for ds in ("opinions", "clusters", "courts", "dockets"):
        candidate = _get_raw_dataset_dir(ds)
        if not list(candidate.glob("*.bz2")):
            LOG.info("Downloading bulk %s if needed...", ds)
            download_dataset_for_court(court=SHARED_COURT, dataset=ds, limit_files=1)

    opinions_path = _find_latest_bz2(raw_opinions)
    clusters_path = _find_latest_bz2(raw_clusters)
    courts_path = _find_latest_bz2(raw_courts)
    dockets_path = _find_latest_bz2(raw_dockets)

    state_cluster_ids, cluster_meta = load_state_clusters_and_metadata(
        clusters_path, courts_path, dockets_path,
        court_matchers=COURT_MATCHERS,
        state_name=STATE_NAME,
        logger=LOG,
    )

    docs: list[IngestibleDocument] = []
    total = 0
    kept = 0

    with bz2.open(opinions_path, "rt", encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(f)
        for row in reader:
            total += 1
            if limit_rows and total > limit_rows:
                break
            cluster_id = (row.get("cluster_id") or "").strip()
            if cluster_id not in state_cluster_ids:
                continue
            if not _is_family_related(row):
                continue
            kept += 1

            opinion_id = (row.get("id") or "").strip()
            doc_id = make_document_id(STATE_CODE, SOURCE, f"op_{opinion_id}")

            content = _extract_searchable_text(row)
            if not content:
                LOG.warning("Opinion %s has no extractable text; skipping", opinion_id)
                continue

            cm = cluster_meta.get(cluster_id, {})
            meta = {
                "domain": COURT_DOMAIN,
                "source": SOURCE,
                "court": COURT_DISPLAY_NAME,
                "jurisdiction": JURISDICTION,
                "cluster_id": cluster_id,
                "case_name": cm.get("case_name", ""),
                "date_filed": cm.get("date_filed", ""),
                "doc_type": "case",
                "state": STATE_NAME,
            }
            docs.append(IngestibleDocument(id=doc_id, content=content, metadata=meta))

    LOG.info("Filtered: %d total rows -> %d kept", total, kept)
    return docs


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None, help="Limit opinion rows (for testing)")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    from scripts.data_ingestion.base.pipeline import save_docs_local, upload_json_to_blob
    from scripts.data_ingestion.base.state_config import get_state_config

    docs = run_courtlistener_to_json(limit_rows=args.limit)
    if docs:
        paths = save_docs_local(docs, Path(__file__).resolve().parent / "output", source="courtlistener")
        print(f"Saved {len(paths)} file(s) to {paths[0].parent}")
        from backend.storage.blob_storage import LegalDocsStorage
        storage = LegalDocsStorage()
        state = get_state_config("florida", root_dir=ROOT_DIR)
        n = upload_json_to_blob(docs, state, storage, source="courtlistener")
        print(f"Uploaded {n} document(s) to blob")
    print(f"Produced {len(docs)} document(s)")
