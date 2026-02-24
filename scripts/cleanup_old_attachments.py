#!/usr/bin/env python3
"""
Clean ONLY blobs uploaded from the frontend (PDF uploads via /api/get-upload-url).
These blobs have prefix u_{hash}/ and are exclusively created by the frontend.
Does NOT touch blobs from data_ingestion, index_from_blob, or other sources.

Removes u_* blobs older than 24h and corresponding documents from Azure Search index.

Run every 24 hours via cron, Azure Functions Timer, or similar.

Environment variables (use .env or export):
- AZURE_STORAGE_CONNECTION_STRING (or Managed Identity in production)
- AZURE_SEARCH_SERVICE, AZURE_SEARCH_INDEX, AZURE_SEARCH_KEY (for index cleanup)
- CLEANUP_MAX_AGE_HOURS (optional, default: 24)
- CLEANUP_DRY_RUN (optional, "1" = list only, do not delete)

Crontab example (run daily at 03:00, loads .env from project):
    0 3 * * * cd /path/to/project && python scripts/cleanup_old_attachments.py >> /var/log/cleanup_attachments.log 2>&1
"""

import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

# Ensure project root on sys.path
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT_DIR / ".env", override=True)
except ImportError:
    pass  # Rely on env vars from cron/systemd

# Env
MAX_AGE_HOURS = int(os.getenv("CLEANUP_MAX_AGE_HOURS", "24"))
DRY_RUN = os.getenv("CLEANUP_DRY_RUN", "").lower() in ("1", "true", "yes")
SEARCH_SERVICE = os.getenv("AZURE_SEARCH_SERVICE", "glg-ai-search")
INDEX_NAME = os.getenv("AZURE_SEARCH_INDEX", "legal-codes")
SEARCH_KEY = os.getenv("AZURE_SEARCH_KEY")


def main():
    from backend.storage.blob_storage import LegalDocsStorage

    cutoff = datetime.now(timezone.utc) - timedelta(hours=MAX_AGE_HOURS)
    print(f"Cleaning blobs older than {MAX_AGE_HOURS}h (cutoff: {cutoff.isoformat()})")
    if DRY_RUN:
        print("DRY RUN: no changes will be made")

    storage = LegalDocsStorage()
    # Only frontend blobs: prefix u_{hash}/ + PDF (only format accepted on upload)
    # Other paths (california/, court_forms/, etc.) are from data_ingestion/scripts - do not touch
    FRONTEND_PREFIX = "u_"
    blobs_with_ts = storage.list_blobs_with_timestamps(
        prefix=FRONTEND_PREFIX,
        extensions=(".pdf",),  # Frontend accepts PDF only
    )

    to_delete = [(name, lm) for name, lm in blobs_with_ts if lm < cutoff]
    print(f"Found {len(blobs_with_ts)} frontend PDF blobs (u_*), {len(to_delete)} older than {MAX_AGE_HOURS}h")

    if not to_delete:
        return 0

    deleted_blobs = 0
    deleted_names = []
    for blob_name, last_mod in to_delete:
        age_h = (datetime.now(timezone.utc) - last_mod).total_seconds() / 3600
        if DRY_RUN:
            print(f"  [DRY] Would delete: {blob_name} (age: {age_h:.1f}h)")
            deleted_blobs += 1
            deleted_names.append(blob_name)
            continue
        if storage.delete_file(blob_name):
            print(f"  Deleted blob: {blob_name} (age: {age_h:.1f}h)")
            deleted_blobs += 1
            deleted_names.append(blob_name)

    # Clean up index documents that reference these blobs
    if not DRY_RUN and SEARCH_KEY and deleted_names:
        _delete_index_documents_for_blobs(deleted_names)

    print(f"Done. Deleted {deleted_blobs} blob(s).")
    return 0


def _delete_index_documents_for_blobs(blob_names: list):
    """Remove from Azure Search index the documents whose source/filepath reference these blobs."""
    if not blob_names:
        return

    try:
        from azure.core.credentials import AzureKeyCredential
        from azure.search.documents import SearchClient

        endpoint = f"https://{SEARCH_SERVICE}.search.windows.net"
        client = SearchClient(endpoint, INDEX_NAME, AzureKeyCredential(SEARCH_KEY))

        ids_to_delete = []
        for blob_name in blob_names:
            # OData filter: source eq 'u_xxx/yyy' (escape single quotes in blob_name)
            safe = blob_name.replace("'", "''")
            filter_expr = f"source eq '{safe}' or filepath eq '{safe}'"
            results = client.search(search_text="*", filter=filter_expr, select=["id"])
            for r in results:
                doc_id = r.get("id")
                if doc_id:
                    ids_to_delete.append(doc_id)

        if ids_to_delete:
            batch_size = 50
            for i in range(0, len(ids_to_delete), batch_size):
                batch = ids_to_delete[i : i + batch_size]
                docs = [{"@search.action": "delete", "id": str(did)} for did in batch]
                client.upload_documents(documents=docs)
                print(f"  Deleted {len(batch)} index document(s)")
    except Exception as e:
        print(f"  Warning: Index cleanup failed: {e}", file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
