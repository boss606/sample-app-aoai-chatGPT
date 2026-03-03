"""
Job state storage for attachment processing.
Uses Azure Table Storage when configured; falls back to in-memory dict for development.
"""

from datetime import datetime, timezone
from typing import Optional, Dict, Any
import os


def _get_table_client():
    """Return TableClient for attachment_jobs if Azure Table Storage is configured."""
    conn_str = os.getenv("AZURE_STORAGE_CONNECTION_STRING")
    if not conn_str:
        return None
    try:
        from azure.data.tables import TableServiceClient
        client = TableServiceClient.from_connection_string(conn_str)
        table = client.get_table_client("attachmentjobs")
        try:
            table.create_table()
        except Exception:
            pass
        return table
    except Exception as e:
        print(f"[JobStorage] Table Storage init failed: {e}", file=__import__("sys").stderr)
        return None


# In-memory fallback (single worker only)
_in_memory_jobs: Dict[str, Dict[str, Any]] = {}
_table_client = None


def get_job_storage_backend() -> str:
    """
    Returns which backend is active: 'azure_table' or 'in_memory'.
    Call this to verify if Azure Table Storage is being used.
    """
    conn_str = os.getenv("AZURE_STORAGE_CONNECTION_STRING")
    if not conn_str:
        return "in_memory"
    table = _ensure_table()
    return "azure_table" if table else "in_memory"


def _ensure_table():
    global _table_client
    if _table_client is None:
        _table_client = _get_table_client()
    return _table_client


def get_job(partition_key: str, job_id: str) -> Optional[Dict[str, Any]]:
    """Get job by partition_key (user prefix) and job_id."""
    table = _ensure_table()
    if table:
        try:
            entity = table.get_entity(partition_key=partition_key, row_key=job_id)
            return dict(entity)
        except Exception:
            return None
    return _in_memory_jobs.get(f"{partition_key}:{job_id}")


def create_job(partition_key: str, job_id: str, blob_name: str, original_filename: str) -> bool:
    """Create job with status pending."""
    now = datetime.now(timezone.utc).isoformat()
    entity = {
        "PartitionKey": partition_key,
        "RowKey": job_id,
        "blob_name": blob_name,
        "original_filename": original_filename,
        "status": "pending",
        "error": "",
        "created_at": now,
    }
    table = _ensure_table()
    if table:
        try:
            table.create_entity(entity=entity)
            return True
        except Exception as e:
            print(f"[JobStorage] Create failed: {e}", file=__import__("sys").stderr)
            return False
    _in_memory_jobs[f"{partition_key}:{job_id}"] = {
        "PartitionKey": partition_key,
        "RowKey": job_id,
        "blob_name": blob_name,
        "original_filename": original_filename,
        "status": "pending",
        "error": "",
        "created_at": now,
    }
    return True


def update_job_status(partition_key: str, job_id: str, status: str, error: Optional[str] = None) -> bool:
    """Update job status (processing, completed, failed)."""
    table = _ensure_table()
    if table:
        try:
            entity = table.get_entity(partition_key=partition_key, row_key=job_id)
            entity["status"] = status
            if error is not None:
                entity["error"] = str(error)[:1000]
            table.update_entity(entity=entity)
            return True
        except Exception as e:
            print(f"[JobStorage] Update failed: {e}", file=__import__("sys").stderr)
            return False
    key = f"{partition_key}:{job_id}"
    if key in _in_memory_jobs:
        _in_memory_jobs[key]["status"] = status
        if error is not None:
            _in_memory_jobs[key]["error"] = str(error)[:1000]
        return True
    return False


def create_agentic_job(partition_key: str, job_id: str) -> bool:
    """Create an agentic/chat job with status pending."""
    now = datetime.now(timezone.utc).isoformat()
    entity = {
        "PartitionKey": partition_key,
        "RowKey": job_id,
        "blob_name": "",
        "original_filename": "",
        "status": "pending",
        "response": "",
        "error": "",
        "created_at": now,
    }
    table = _ensure_table()
    if table:
        try:
            table.create_entity(entity=entity)
            return True
        except Exception as e:
            print(f"[JobStorage] Create agentic failed: {e}", file=__import__("sys").stderr)
            return False
    _in_memory_jobs[f"{partition_key}:{job_id}"] = {
        "PartitionKey": partition_key,
        "RowKey": job_id,
        "blob_name": "",
        "original_filename": "",
        "status": "pending",
        "response": "",
        "error": "",
        "created_at": now,
    }
    return True


def update_agentic_job(
    partition_key: str, job_id: str, status: str, response: Optional[str] = None, error: Optional[str] = None
) -> bool:
    """Update agentic job status and optionally response/error."""
    table = _ensure_table()
    if table:
        try:
            entity = table.get_entity(partition_key=partition_key, row_key=job_id)
            entity["status"] = status
            if response is not None:
                entity["response"] = str(response)[:50000]  # Cap very long responses
            if error is not None:
                entity["error"] = str(error)[:1000]
            table.update_entity(entity=entity)
            return True
        except Exception as e:
            print(f"[JobStorage] Update agentic failed: {e}", file=__import__("sys").stderr)
            return False
    key = f"{partition_key}:{job_id}"
    if key in _in_memory_jobs:
        _in_memory_jobs[key]["status"] = status
        if response is not None:
            _in_memory_jobs[key]["response"] = str(response)[:50000]
        if error is not None:
            _in_memory_jobs[key]["error"] = str(error)[:1000]
        return True
    return False
