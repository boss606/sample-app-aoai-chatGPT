"""
Queue worker for attachment OCR + embeddings processing.

Run this as a separate process from the web app:
    python worker_attachment_queue.py
"""

import json
import os
import sys
import time

from azure.storage.queue import QueueClient

from app import process_attachment_job
from backend.job_storage import get_job


QUEUE_NAME = os.getenv("ATTACHMENT_QUEUE_NAME", "attachment-jobs")
POLL_SECONDS = int(os.getenv("ATTACHMENT_QUEUE_POLL_SECONDS", "3"))
VISIBILITY_TIMEOUT = int(os.getenv("ATTACHMENT_QUEUE_VISIBILITY_TIMEOUT", "600"))


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


def main() -> None:
    q = _get_queue_client()
    print(f"[QueueWorker] Listening queue={QUEUE_NAME}", file=sys.stderr)
    while True:
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
                        # Keep message for retry after visibility timeout.
                        print(f"[QueueWorker] Message failed: {e}", file=sys.stderr)
            if not found:
                time.sleep(POLL_SECONDS)
        except Exception as e:
            print(f"[QueueWorker] Loop error: {e}", file=sys.stderr)
            time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
