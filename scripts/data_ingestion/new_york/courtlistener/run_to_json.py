"""
Run CourtListener bulk filter (NY family law opinions) and produce IngestibleDocuments.

Downloads bulk data from shared location, filters by New York courts and family law terms.
"""

from __future__ import annotations

import argparse
import bz2
import csv
from datetime import date
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
from scripts.data_ingestion.shared.cap_complement import fetch_cap_complement
from scripts.data_ingestion.base.document_schema import IngestibleDocument, make_document_id

LOG = logging.getLogger(__name__)

STATE_CODE = "ny"
SOURCE = "courtlistener"
COURT_DOMAIN = "ny_supreme"
JURISDICTION = "NY"
STATE_NAME = "new_york"
COURT_MATCHERS = (
    "ny",
    "NY",
    "New York Supreme",
    "New York Appellate",
    "nysupct",
    "nyapp",
    "NY App",
    "NY Supreme",
    "Court of Appeals of New York",
)
COURT_DISPLAY_NAME = "New York Supreme Court"
# CourtListener court slugs for CAP Search API
CAP_COURT_IDS = ["ny", "nysupct", "nyapp"]

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
    Download CourtListener bulk data, filter NY family law opinions, return IngestibleDocuments.
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
    passed_cluster = 0
    failed_family = 0
    failed_family_no_text = 0
    failed_family_no_terms = 0
    empty_text = 0

    with bz2.open(opinions_path, "rt", encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(f)
        for row in reader:
            total += 1
            if limit_rows and total > limit_rows:
                break
            cluster_id = (row.get("cluster_id") or "").strip()
            if cluster_id not in state_cluster_ids:
                continue
            passed_cluster += 1
            text = _extract_searchable_text(row)
            if not text:
                failed_family_no_text += 1
                failed_family += 1
                continue
            hits = FAMILY_RE.findall(text)
            distinct = {h.lower() for h in hits}
            if len(distinct) < MIN_TERMS:
                failed_family_no_terms += 1
                failed_family += 1
                continue
            content = text  # already extracted above
            if not content:
                empty_text += 1
                LOG.warning("Opinion %s has no extractable text; skipping", row.get("id"))
                continue
            kept += 1

            opinion_id = (row.get("id") or "").strip()
            doc_id = make_document_id(STATE_CODE, SOURCE, f"op_{opinion_id}")

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

    LOG.info(
        "Filtered: total=%d passed_cluster=%d failed_family=%d (no_text=%d no_terms=%d) empty_text=%d kept=%d",
        total, passed_cluster, failed_family, failed_family_no_text, failed_family_no_terms, empty_text, kept,
    )
    return docs


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None, help="Limit opinion rows (for testing)")
    ap.add_argument("--skip-cap", action="store_true", help="Skip CAP complement (CourtListener bulk only)")
    ap.add_argument("--cap-only", action="store_true", help="[TEST] Skip bulk, fetch CAP only")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    from scripts.data_ingestion.base.pipeline import save_docs_local, upload_json_to_blob
    from scripts.data_ingestion.base.state_config import get_state_config

    docs = [] if args.cap_only else run_courtlistener_to_json(limit_rows=args.limit)

    # CAP complement: when bulk empty use all CAP; when bulk has docs use CAP from max_date only
    if not args.skip_cap:
        max_date: date | None = None
        for d in docs:
            df = d.metadata.get("date_filed", "")
            if df and len(df) >= 10:
                try:
                    dt = date.fromisoformat(df[:10])
                    if max_date is None or dt > max_date:
                        max_date = dt
                except ValueError:
                    pass
        if docs and max_date is None:
            LOG.warning("CourtListener has docs but no parseable date_filed; skipping CAP to avoid duplicates")
            cap_docs = []
        else:
            since = max_date if docs else None
            cap_docs = fetch_cap_complement(
                state_code=STATE_CODE,
                state_name=STATE_NAME,
                court_ids=CAP_COURT_IDS,
                court_domain=COURT_DOMAIN,
                court_display_name=COURT_DISPLAY_NAME,
                jurisdiction=JURISDICTION,
                source="cap",
                since_date=since,
                logger=LOG,
            )
        docs = list(docs) + cap_docs
        if cap_docs:
            LOG.info("CAP complement: added %d docs (total %d)", len(cap_docs), len(docs))

    if docs:
        # Save to courtlistener output (includes cap complement; ids distinguish ny_courtlistener_ vs ny_cap_)
        paths = save_docs_local(docs, Path(__file__).resolve().parent / "output", source="courtlistener")
        print(f"Saved {len(paths)} file(s) to {paths[0].parent}")
        from backend.storage.blob_storage import LegalDocsStorage
        storage = LegalDocsStorage()
        state = get_state_config("new_york", root_dir=ROOT_DIR)
        # Use per-doc metadata.source for blob path (courtlistener vs cap)
        n = upload_json_to_blob(docs, state, storage)
        print(f"Uploaded {n} document(s) to blob")
    print(f"Produced {len(docs)} document(s)")
