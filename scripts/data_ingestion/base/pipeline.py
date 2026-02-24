"""
Base pipeline functions: run scrapers, produce JSON, upload to blob.
"""

import json
import logging
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from scripts.data_ingestion.base.document_schema import IngestibleDocument, make_document_id
from scripts.data_ingestion.base.state_config import StateConfig, get_state_config

log = logging.getLogger(__name__)


def prepare_for_indexing(doc: IngestibleDocument) -> Dict[str, Any]:
    """
    Convert an IngestibleDocument to the format expected by the indexer
    (content, metadata for chunking/domain inference).

    Args:
        doc: Normalized document from the pipeline.

    Returns:
        Dict with content and metadata for index_from_blob.
    """
    return {"content": doc.content, "metadata": doc.metadata, "id": doc.id}


def upload_json_to_blob(
    docs: List[IngestibleDocument],
    state: StateConfig,
    storage=None,
    source: Optional[str] = None,
) -> int:
    """
    Upload documents to blob storage with path {state_name}/{source}/{id}.json.

    Args:
        docs: List of IngestibleDocuments.
        state: State configuration.
        storage: LegalDocsStorage instance. If None, skips upload.
        source: Override source subfolder (default: from metadata.source).

    Returns:
        Number of documents uploaded.
    """
    if not storage:
        log.warning("Storage not configured; skipping blob upload.")
        return 0

    uploaded = 0
    for doc in docs:
        meta_source = doc.metadata.get("source", "unknown")
        folder = source or meta_source
        blob_name = f"{state.state_name}/{folder}/{doc.id}.json"
        content = doc.to_json_str()
        if storage.upload_text(content, blob_name):
            uploaded += 1
            log.info("Uploaded %s", blob_name)
        else:
            log.error("Failed to upload %s", blob_name)

    return uploaded


def save_docs_local(
    docs: List[IngestibleDocument],
    output_dir: Path,
    source: Optional[str] = None,
) -> List[Path]:
    """
    Save documents as individual JSON files locally.

    Args:
        docs: List of IngestibleDocuments.
        output_dir: Directory to write JSON files.
        source: Optional subfolder (e.g., california_law).

    Returns:
        List of paths to written files.
    """
    output_dir = Path(output_dir)
    if source:
        output_dir = output_dir / source
    output_dir.mkdir(parents=True, exist_ok=True)

    paths: List[Path] = []
    for doc in docs:
        out_path = output_dir / f"{doc.id}.json"
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(doc.to_dict(), f, ensure_ascii=False, indent=2)
        paths.append(out_path)
        log.debug("Saved %s", out_path)

    return paths


def run_scraper_to_json(
    source: str,
    state_name: str,
    scraper_fn: Callable[[], List[IngestibleDocument]],
    root_dir: Optional[Path] = None,
) -> List[IngestibleDocument]:
    """
    Run a scraper and return documents in unified schema.

    Args:
        source: Source identifier (e.g., california_law).
        state_name: State name (e.g., california).
        scraper_fn: Callable that returns list of IngestibleDocuments.
        root_dir: Project root for path resolution.

    Returns:
        List of IngestibleDocuments.
    """
    state = get_state_config(state_name, root_dir=root_dir)
    log.info("Running scraper %s for state %s", source, state_name)
    docs = scraper_fn()
    log.info("Produced %d document(s) from %s", len(docs), source)
    return docs
