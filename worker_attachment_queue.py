"""
Queue worker for attachment OCR + embeddings processing.

Run this as a separate process from the web app:
    python worker_attachment_queue.py

Uses an Azure Blob Lease as a distributed singleton lock so that only one
worker processes the queue even when the App Service is scaled out.
"""

import json
import os
import sys
import threading
import time

from azure.core.exceptions import ResourceExistsError, HttpResponseError
from azure.storage.blob import BlobServiceClient, BlobClient
from azure.storage.queue import QueueClient

from app import process_attachment_job
from backend.job_storage import get_job


QUEUE_NAME = os.getenv("ATTACHMENT_QUEUE_NAME", "attachment-jobs")
POLL_SECONDS = int(os.getenv("ATTACHMENT_QUEUE_POLL_SECONDS", "3"))
VISIBILITY_TIMEOUT = int(os.getenv("ATTACHMENT_QUEUE_VISIBILITY_TIMEOUT", "600"))

_LEASE_CONTAINER = os.getenv("ATTACHMENT_LEASE_CONTAINER", "attachment-locks")
_LEASE_BLOB = os.getenv("ATTACHMENT_LEASE_BLOB", "queue-worker.lock")
_LEASE_DURATION = 60       # Azure max is 60s; renewed every 30s
_LEASE_RENEW_INTERVAL = 30
_LEASE_RETRY_INTERVAL = 10


# ---------------------------------------------------------------------------
# Queue helpers
# ---------------------------------------------------------------------------

def _get_queue_client() -> QueueClient:
    conn_str = os.getenv("AZURE_STORAGE_CONNECTION_STRING")
    if not conn_str:
        raise RuntimeError("AZURE_STORAGE_CONNECTION_STRING is not configured")
    q = QueueClient.from_connection_string(conn_str, QUEUE_NAME)
    try:
        q.create_queue()
    except Exception:
        pass
    return q


def _handle_message(payload: dict) -> None:
    job_id = (payload.get("job_id") or "").strip()
    partition_key = (payload.get("partition_key") or "").strip()
    blob_name = (payload.get("blob_name") or "").strip()
    original_filename = (payload.get("original_filename") or "file.pdf").strip()

    if not all([job_id, partition_key, blob_name]):
        raise ValueError("Invalid payload: missing required fields")

    # Idempotency guard: skip if already completed.
    existing = get_job(partition_key, job_id) or {}
    if existing.get("status") == "completed":
        print(f"[QueueWorker] Job {job_id} already completed; skipping", file=sys.stderr)
        return

    process_attachment_job(job_id, partition_key, blob_name, original_filename)


# ---------------------------------------------------------------------------
# Blob Lease singleton
# ---------------------------------------------------------------------------

def _get_lock_blob() -> BlobClient:
    conn_str = os.getenv("AZURE_STORAGE_CONNECTION_STRING")
    if not conn_str:
        raise RuntimeError("AZURE_STORAGE_CONNECTION_STRING is not configured")
    svc = BlobServiceClient.from_connection_string(conn_str)
    container = svc.get_container_client(_LEASE_CONTAINER)
    try:
        container.create_container()
    except ResourceExistsError:
        pass
    blob = container.get_blob_client(_LEASE_BLOB)
    try:
        blob.upload_blob(b"", overwrite=False)
    except ResourceExistsError:
        pass
    return blob


def _renew_loop(lease, stop: threading.Event) -> None:
    """Background thread that renews the blob lease every _LEASE_RENEW_INTERVAL seconds."""
    while not stop.wait(_LEASE_RENEW_INTERVAL):
        try:
            lease.renew()
        except Exception as e:
            print(f"[QueueWorker] Lease renewal failed: {e}", file=sys.stderr)
            stop.set()


def _acquire_lease(blob: BlobClient):
    """Try to acquire the blob lease. Returns BlobLeaseClient or None if already held."""
    try:
        return blob.acquire_lease(lease_duration=_LEASE_DURATION)
    except HttpResponseError as e:
        if e.error_code == "LeaseAlreadyPresent":
            return None
        raise


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    blob = _get_lock_blob()

    while True:
        lease = _acquire_lease(blob)
        if lease is None:
            print(f"[QueueWorker] Lease held by another instance; retrying in {_LEASE_RETRY_INTERVAL}s", file=sys.stderr)
            time.sleep(_LEASE_RETRY_INTERVAL)
            continue

        print(f"[QueueWorker] Lease acquired — listening queue={QUEUE_NAME}", file=sys.stderr)
        stop = threading.Event()
        renewer = threading.Thread(target=_renew_loop, args=(lease, stop), daemon=True)
        renewer.start()

        try:
            q = _get_queue_client()
            while not stop.is_set():
                try:
                    messages = q.receive_messages(
                        messages_per_page=1,
                        visibility_timeout=VISIBILITY_TIMEOUT,
                    ).by_page()

                    found = False
                    for page in messages:
                        for msg in page:
                            found = True
                            try:
                                payload = json.loads(msg.content)
                                _handle_message(payload)
                                q.delete_message(msg)
                            except Exception as e:
                                print(f"[QueueWorker] Message failed: {e}", file=sys.stderr)
                    if not found:
                        time.sleep(POLL_SECONDS)
                except Exception as e:
                    print(f"[QueueWorker] Loop error: {e}", file=sys.stderr)
                    time.sleep(POLL_SECONDS)
        finally:
            stop.set()
            try:
                lease.release()
                print("[QueueWorker] Lease released", file=sys.stderr)
            except Exception:
                pass


if __name__ == "__main__":
    main()
