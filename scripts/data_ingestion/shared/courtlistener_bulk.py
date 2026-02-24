"""
Utilities for downloading CourtListener bulk datasets (opinions, clusters, courts, dockets).
Probes predictable filenames via HEAD. Downloads to raw_root/<court>/<dataset>/.
Use court="shared" for a single shared download used by all states.
"""

from __future__ import annotations

import bz2
import csv
import gzip
import json
import logging
import os
import random
import re
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Tuple
from urllib.parse import urljoin

import requests

STORAGE_BULK_BASE = "https://storage.courtlistener.com/bulk-data/"
DEFAULT_TIMEOUT = 60
RETRIES = 8
BACKOFF = 5
CHUNK = 1024 * 256
DEFAULT_MAX_DATE_STR = "2025-10-31"
MAX_DATE_STR = os.getenv("CL_MAX_BULK_DATE", DEFAULT_MAX_DATE_STR)
MAX_DATE = date.fromisoformat(MAX_DATE_STR) if MAX_DATE_STR else None
TOKEN = os.getenv("COURTLISTENER_TOKEN")
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; courtlistener-bulk/1.0)",
    **({"Authorization": f"Token {TOKEN}"} if TOKEN else {}),
}

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
RAW_ROOT = _PROJECT_ROOT / "downloads" / "courtlistener_bulk" / "teste" / "raw"
SHARED_COURT = "shared"

log = logging.getLogger(__name__)

BULK_FILE_BY_DATASET: Dict[str, str] = {
    "opinions": "opinions",
    "clusters": "opinion-clusters",
    "opinion-clusters": "opinion-clusters",
    "courts": "courts",
    "dockets": "dockets",
}


def _human_bytes(num: Optional[int]) -> str:
    if num is None:
        return "unknown"
    units = ["B", "KB", "MB", "GB", "TB", "PB"]
    size = float(num)
    for u in units:
        if size < 1024.0:
            return f"{size:.1f} {u}"
        size /= 1024.0
    return f"{size:.1f} EB"


def _head_size(url: str) -> Optional[int]:
    try:
        r = requests.head(url, allow_redirects=True, timeout=DEFAULT_TIMEOUT, headers=HEADERS)
        if r.status_code >= 400:
            return None
        cl = r.headers.get("content-length")
        return int(cl) if cl and cl.isdigit() else None
    except requests.RequestException:
        return None


def _year_from_name(name: str) -> Optional[int]:
    if not name:
        return None
    m = re.search(r"(19|20)\d{2}", name)
    return int(m.group(0)) if m else None


def _month_end(d: date) -> date:
    if d.month == 12:
        first_next = date(d.year + 1, 1, 1)
    else:
        first_next = date(d.year, d.month + 1, 1)
    return first_next - timedelta(days=1)


def _iter_candidate_dates(months_back: int = 84) -> Iterator[date]:
    today = date.today()
    cursor = _month_end(today)
    for _ in range(months_back):
        for off in (0, 1, 2, 3, 4, 5, 6, 7):
            yield cursor - timedelta(days=off)
        yield date(cursor.year, cursor.month, 15)
        cursor = date(cursor.year, cursor.month, 1) - timedelta(days=1)


def _head_ok(url: str) -> bool:
    try:
        r = requests.head(url, allow_redirects=True, timeout=DEFAULT_TIMEOUT, headers=HEADERS)
        return 200 <= r.status_code < 300
    except requests.RequestException:
        return False


def _head_result(url: str) -> tuple[int | None, Exception | None]:
    try:
        r = requests.head(url, allow_redirects=True, timeout=DEFAULT_TIMEOUT, headers=HEADERS)
        return (r.status_code, None)
    except requests.RequestException as e:
        return (None, e)


def _bulk_file_prefix(dataset: str) -> str:
    return BULK_FILE_BY_DATASET.get(dataset, dataset)


def _find_latest_dump_url(dataset: str) -> str:
    prefix = _bulk_file_prefix(dataset)
    if MAX_DATE:
        ds = MAX_DATE.isoformat()
        url = urljoin(STORAGE_BULK_BASE, f"{prefix}-{ds}.csv.bz2")
        status, exc = _head_result(url)
        if status == 200:
            log.info("Found dump for dataset=%s -> %s", dataset, url)
            return url
        err = f"HTTP {status}" if status else str(exc) if exc else "unknown"
        raise RuntimeError(f"Dump not found for dataset={dataset} at {url} ({err})")
    for d in _iter_candidate_dates(months_back=120):
        ds = d.isoformat()
        url = urljoin(STORAGE_BULK_BASE, f"{prefix}-{ds}.csv.bz2")
        if _head_ok(url):
            log.info("Found dump for dataset=%s -> %s", dataset, url)
            return url
    raise RuntimeError(f"Could not find any dump for dataset={dataset}")


def _list_bulk_links(dataset: str) -> List[Dict[str, str]]:
    found: List[Dict[str, str]] = []
    prefix = _bulk_file_prefix(dataset)

    def _try_date(d: date) -> bool:
        ds = d.isoformat()
        name = f"{prefix}-{ds}.csv.bz2"
        url = urljoin(STORAGE_BULK_BASE, name)
        if _head_ok(url):
            found.append({"name": name, "url": url})
            return True
        return False

    if MAX_DATE:
        _try_date(MAX_DATE)
    else:
        for d in _iter_candidate_dates(months_back=120):
            _try_date(d)
            if len(found) >= 36:
                break
    if not found:
        latest = _find_latest_dump_url(dataset)
        return [{"name": latest.split("/")[-1], "url": latest}]
    return found


def iter_files_filtered(
    dataset: str,
    *,
    since_year: Optional[int] = None,
    until_year: Optional[int] = None,
) -> Iterator[Dict]:
    for entry in _list_bulk_links(dataset):
        url = entry["url"]
        name = entry["name"]
        year = _year_from_name(name)
        if since_year and year and year < since_year:
            continue
        if until_year and year and year > until_year:
            continue
        yield {"url": url, "name": name, "size": None}


def download_file(url: str, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    last_exc: Optional[Exception] = None
    for attempt in range(1, RETRIES + 1):
        try:
            with requests.get(url, stream=True, timeout=DEFAULT_TIMEOUT, headers=HEADERS) as r:
                if r.status_code in (429,) or 500 <= r.status_code < 600:
                    time.sleep(BACKOFF * attempt * random.uniform(0.5, 1.5))
                    continue
                r.raise_for_status()
                tmp = dest.with_suffix(dest.suffix + ".part")
                with open(tmp, "wb") as f:
                    for chunk in r.iter_content(chunk_size=CHUNK):
                        if chunk:
                            f.write(chunk)
                tmp.replace(dest)
                return dest
        except requests.RequestException as exc:
            last_exc = exc
            if attempt == RETRIES:
                break
            time.sleep(BACKOFF * attempt * random.uniform(0.5, 1.5))
    raise last_exc if last_exc else RuntimeError(f"Failed to download {url}")


def download_dataset_for_court(
    *,
    court: str,
    dataset: str,
    since_year: Optional[int] = None,
    until_year: Optional[int] = None,
    limit_files: Optional[int] = None,
    raw_root: Path = RAW_ROOT,
) -> List[Path]:
    target_dir = raw_root / court / dataset
    entries: List[Dict] = []
    for entry in iter_files_filtered(dataset, since_year=since_year, until_year=until_year):
        entry["size"] = _head_size(entry["url"])
        entries.append(entry)
    total_bytes = sum(e["size"] for e in entries if e.get("size") is not None)
    has_unknown = any(e.get("size") is None for e in entries)
    log.info(
        "Dataset=%s court=%s -> %d files, estimated %s%s",
        dataset,
        court,
        len(entries),
        _human_bytes(total_bytes),
        " (partial)" if has_unknown else "",
    )
    downloaded: List[Path] = []
    for entry in entries:
        if limit_files and len(downloaded) >= limit_files:
            break
        name = entry["name"]
        url = entry["url"]
        dest = target_dir / name
        if dest.exists() and dest.stat().st_size > 0:
            log.info("Skip existing %s", dest)
            downloaded.append(dest)
            continue
        log.info("Downloading %s -> %s", name, dest)
        try:
            download_file(url, dest)
            downloaded.append(dest)
        except Exception as exc:
            log.warning("Failed %s: %s", name, exc)
    if not downloaded:
        log.warning("No files downloaded for dataset=%s court=%s", dataset, court)
    return downloaded


def _find_latest_bz2(directory: Path) -> Path:
    """Return the most recently modified .bz2 file in directory."""
    files = sorted(directory.glob("*.bz2"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        raise FileNotFoundError(f"No .bz2 file found in {directory}")
    return files[0]


def _get_raw_dataset_dir(dataset: str, raw_root: Path = RAW_ROOT) -> Path:
    """Return raw directory for dataset, preferring shared, then cal (backward compat)."""
    for court in (SHARED_COURT, "cal"):
        path = raw_root / court / dataset
        if path.exists() and list(path.glob("*.bz2")):
            return path
    return raw_root / SHARED_COURT / dataset


def load_state_clusters_and_metadata(
    clusters_path: Path,
    courts_path: Path,
    dockets_path: Path,
    court_matchers: Tuple[str, ...],
    state_name: str,
    logger=None,
) -> Tuple[set[str], dict[str, dict]]:
    """
    Build state cluster_ids and cluster_meta in one pass. Low memory (no full cluster_docket).
    Returns (state_cluster_ids, cluster_meta) for clusters whose docket is in state courts.
    """
    slog = logger or log

    def _load_state_court_ids() -> set[str]:
        out: set[str] = set()
        with bz2.open(courts_path, "rt", encoding="utf-8", errors="replace") as f:
            reader = csv.DictReader(f)
            for row in reader:
                cid = (row.get("id") or "").strip()
                short = (row.get("short_name") or "").lower()
                full = (row.get("full_name") or "").lower()
                for m in court_matchers:
                    if m.lower() in short or m.lower() in full:
                        out.add(cid)
                        break
        slog.info("Courts %s identified: %d ids", state_name, len(out))
        return out

    def _load_state_docket_ids(state_court_ids: set[str]) -> set[str]:
        out: set[str] = set()
        with bz2.open(dockets_path, "rt", encoding="utf-8", errors="replace") as f:
            reader = csv.DictReader(f)
            for row in reader:
                cid = (row.get("court_id") or "").strip()
                if cid not in state_court_ids:
                    continue
                did = (row.get("id") or "").strip()
                if did:
                    out.add(did)
        slog.info("Docket IDs %s: %d", state_name, len(out))
        return out

    state_court_ids = _load_state_court_ids()
    state_docket_ids = _load_state_docket_ids(state_court_ids)

    state_cluster_ids: set[str] = set()
    cluster_meta: dict[str, dict] = {}
    with bz2.open(clusters_path, "rt", encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(f)
        for row in reader:
            cid = (row.get("id") or "").strip()
            did = (row.get("docket_id") or "").strip()
            if not cid or not did:
                continue
            if did not in state_docket_ids:
                continue
            state_cluster_ids.add(cid)
            cluster_meta[cid] = {
                "case_name": (row.get("case_name") or "").strip(),
                "date_filed": (row.get("date_filed") or "").strip(),
            }
    slog.info("Cluster IDs %s: %d", state_name, len(state_cluster_ids))
    return state_cluster_ids, cluster_meta


def load_jsonl(path: Path) -> Iterator[Dict]:
    if path.suffix == ".gz" or path.name.endswith(".jsonl.gz") or path.name.endswith(".ndjson.gz"):
        opener = gzip.open
    else:
        opener = open  # type: ignore
    with opener(path, "rt", encoding="utf-8") as f:  # type: ignore[arg-type]
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                log.warning("Malformed JSON line in %s", path)
                continue
