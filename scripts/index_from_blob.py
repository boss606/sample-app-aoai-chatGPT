"""
Index PDFs/TXTs directly from Azure Blob (legal-docs-raw) into Azure AI Search
without writing to the local ./data directory. Tailored for legal code domains.

Requirements (env vars):
- AZURE_SEARCH_KEY: admin/query key of the search service
- AZURE_OPENAI_EMBEDDING_ENDPOINT: embedding deployment endpoint, e.g.
  https://<aoai>.openai.azure.com/openai/deployments/<embedding>/embeddings?api-version=2023-05-15
- FORM_RECOGNIZER_ENDPOINT, FORM_RECOGNIZER_KEY: for text extraction (only needed for PDFs)
- Optional: AZURE_SEARCH_SERVICE (default: glg-ai-search)
- Optional: AZURE_SEARCH_INDEX (default: legal-codes)
- Optional: AZURE_STORAGE_CONNECTION_STRING (or use Managed Identity)
"""

import os
import sys
import re
import json
import tempfile
import mmap
from pathlib import Path
from typing import List, Iterable, Tuple

from dotenv import load_dotenv

# Ensure project root on sys.path for "backend" imports
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from azure.core.credentials import AzureKeyCredential
from azure.search.documents import SearchClient
from azure.search.documents.indexes import SearchIndexClient
from backend.storage.blob_storage import LegalDocsStorage
from scripts.prepdocs import create_search_index, upload_documents_to_index
from scripts.data_utils import chunk_content, Document

# Load environment variables from a .env file if present
load_dotenv(override=True)


SEARCH_SERVICE = os.getenv("AZURE_SEARCH_SERVICE", "glg-ai-search")
# Single index for all domains; override via env
INDEX_NAME = os.getenv("AZURE_SEARCH_INDEX", "legal-codes")
# Default domain; can be overridden per run. If empty, we infer from blob name.
LEGAL_DOMAIN = os.getenv("LEGAL_DOMAIN", "").strip()
# EMBEDDING_ENDPOINT = os.getenv("AZURE_OPENAI_EMBEDDING_ENDPOINT")
# Fallback to base endpoint + deployment (align with app.py usage)
AZURE_BASE_ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT")
EMBEDDING_DEPLOYMENT = os.getenv("AZURE_OPENAI_EMBEDDING_DEPLOYMENT", "text-embedding-3-large")
AZURE_API_VERSION = os.getenv("AZURE_OPENAI_API_VERSION", "2023-05-15")
SEARCH_KEY = os.getenv("AZURE_SEARCH_KEY")
SUPPORTED_EXTENSIONS = (".txt",)
AZURE_OPENAI_KEY = os.getenv("AZURE_OPENAI_KEY")
AZURE_OPENAI_API_KEY = os.getenv("AZURE_OPENAI_API_KEY")

# Simple heuristics to split legal blocks
SECTION_RE = re.compile(r"^(?:Art\.?|§)\s*\d+[^\n]*", re.IGNORECASE | re.MULTILINE)
CASE_RE = re.compile(r"^[A-Z][A-Za-z'().\- ]+ v\. [A-Z][A-Za-z'().\- ]+", re.MULTILINE)
CITATION_RE = re.compile(r"\b\d{3,4}\s+[A-Z][A-Za-z.]*\s+\d+\b")
# Broader reporter-style citations (e.g., 2024 Cal. LEXIS 123, 90 U.S. 1)
CITATION_RE_COURT = re.compile(r"\b\d{2,4}\s+[A-Z][A-Za-z.& ]+\s+\d+\b")

# Domain-specific section patterns (prefix-sensitive)
SECTION_PATTERNS = {
    "family_code": [
        re.compile(r"\bfc\b.*?§\s*([\d.]+)", re.IGNORECASE),
        re.compile(r"\bfamily code\b.*?§\s*([\d.]+)", re.IGNORECASE),
        re.compile(r"^§\s*([\d.]+)", re.IGNORECASE | re.MULTILINE),
    ],
    "code_of_civil_procedure": [
        re.compile(r"\bccp\b.*?§\s*([\d.]+)", re.IGNORECASE),
        re.compile(r"\bcode of civil procedure\b.*?§\s*([\d.]+)", re.IGNORECASE),
        re.compile(r"^§\s*([\d.]+)", re.IGNORECASE | re.MULTILINE),
    ],
    "civil_code": [
        re.compile(r"\bciv\b.*?§\s*([\d.]+)", re.IGNORECASE),
        re.compile(r"\bcivil code\b.*?§\s*([\d.]+)", re.IGNORECASE),
        re.compile(r"^§\s*([\d.]+)", re.IGNORECASE | re.MULTILINE),
    ],
    "evidence_code": [
        re.compile(r"\bevid\b.*?§\s*([\d.]+)", re.IGNORECASE),
        re.compile(r"\bevidence code\b.*?§\s*([\d.]+)", re.IGNORECASE),
        re.compile(r"^§\s*([\d.]+)", re.IGNORECASE | re.MULTILINE),
    ],
    "rules_of_court": [
        re.compile(r"\brule\s+([\d.]+)", re.IGNORECASE),
        re.compile(r"\bcrc\b.*?([\d.]+)", re.IGNORECASE),
    ],
}

# CourtListener domain defaults for metadata
COURTLISTENER_DOMAIN_INFO = {
    "cal_supreme": {"court": "California Supreme Court", "jurisdiction": "CA"},
    "calctapp": {"court": "California Court of Appeal", "jurisdiction": "CA"},
    "scotus": {"court": "United States Supreme Court", "jurisdiction": "US"},
}

# Direct stem-to-domain mapping for known CourtListener dumps
COURTLISTENER_STEM_MAP = {
    "cal_supreme_all": "cal_supreme",
    "cal_supreme": "cal_supreme",
    "calctapp_all": "calctapp",
    "calctapp": "calctapp",
    "scotus_all": "scotus",
    "scotus": "scotus",
}

DECISION_DATE_RE_ISO = re.compile(r"\b(19|20)\d{2}-\d{2}-\d{2}\b")
DECISION_DATE_RE_LONG = re.compile(
    r"\b(?:Jan|January|Feb|February|Mar|March|Apr|April|May|Jun|June|Jul|July|Aug|August|Sep|September|Oct|October|Nov|November|Dec|December)\s+\d{1,2},\s+(19|20)\d{2}\b",
    re.IGNORECASE,
)

CASE_HEADING_RE = re.compile(
    r"^\s*(OPINION|FACTS|BACKGROUND|PROCEDURAL HISTORY|ISSUE|ISSUES|HOLDING|ANALYSIS|DISCUSSION|STANDARD OF REVIEW|DISPOSITION|CONCLUSION)\b",
    re.IGNORECASE | re.MULTILINE,
)

# Chunking strategy constants
TOKENS_SECTION_OR_CASE = 4000  # keep sections/cases intact whenever possible
TOKENS_DEFAULT = 800
TOKENS_SUBSECTION = 1200  # sub-chunk large sections
UPLOAD_BATCH_SIZE = int(os.getenv("UPLOAD_BATCH_SIZE", "128"))


def estimate_batch_bytes(batch: List[Document]) -> int:
    """Rough byte size estimate of a batch for logging/monitoring."""
    total = 0
    for c in batch:
        try:
            total += len(c.content.encode("utf-8"))
        except Exception:
            total += len(c.content)
        if c.metadata:
            if isinstance(c.metadata, str):
                total += len(c.metadata.encode("utf-8"))
            else:
                try:
                    total += len(json.dumps(c.metadata))
                except Exception:
                    pass
        if c.title:
            total += len(c.title.encode("utf-8"))
        if c.url:
            total += len(c.url.encode("utf-8"))
        if c.contentVector:
            try:
                total += len(json.dumps(c.contentVector))
            except Exception:
                pass
    return total


def make_chunk_id(file_name: str, chunk_idx: int) -> str:
    """Generate a Search-safe document key (letters/digits/_/-/= only)."""
    stem = Path(file_name).stem
    safe_stem = re.sub(r"[^A-Za-z0-9_\-=]", "-", stem)
    return f"{safe_stem}-{chunk_idx}"


def _find_markers(text: str, patterns: Iterable[re.Pattern]) -> List[int]:
    positions: List[int] = []
    for pat in patterns:
        for m in pat.finditer(text):
            positions.append(m.start())
    return sorted(set(positions))


def resolve_embedding_endpoint() -> str:
    """
    Resolve embedding endpoint as in app.py:
    - Prefer AZURE_OPENAI_ENDPOINT + AZURE_OPENAI_EMBEDDING_DEPLOYMENT (+ api-version) if available.
    - Only use AZURE_OPENAI_EMBEDDING_ENDPOINT if the base endpoint is not defined.
    """
    if AZURE_BASE_ENDPOINT and EMBEDDING_DEPLOYMENT:
        return f"{AZURE_BASE_ENDPOINT.rstrip('/')}/openai/deployments/{EMBEDDING_DEPLOYMENT}/embeddings?api-version={AZURE_API_VERSION}"
    raise ValueError("Missing embedding endpoint configuration: set AZURE_OPENAI_ENDPOINT + AZURE_OPENAI_EMBEDDING_DEPLOYMENT")


def split_legal_blocks(text: str, domain: str) -> List[str]:
    """Split text into blocks by legal sections or case headings; fallback: whole text."""
    blocks: List[str] = []

    # First, try domain-specific section markers (including §/rule)
    section_patterns = SECTION_PATTERNS.get(domain, [])
    markers = _find_markers(text, section_patterns or [SECTION_RE])
    if markers:
        last = 0
        for pos in markers:
            if pos != 0:
                blocks.append(text[last:pos])
            last = pos
        blocks.append(text[last:])

    # If no section markers were found, try case headings
    if not blocks:
        headings = _find_markers(text, [CASE_HEADING_RE])
        if headings:
            last = 0
            for pos in headings:
                if pos != 0:
                    blocks.append(text[last:pos])
                last = pos
            blocks.append(text[last:])

    # If still nothing, fall back to case-name heuristic
    if not blocks:
        last = 0
        for match in CASE_RE.finditer(text):
            if match.start() != 0:
                blocks.append(text[last:match.start()])
            last = match.start()
        if blocks:
            blocks.append(text[last:])

    # If nothing is found, return the whole text
    if not blocks:
        blocks = [text]

    return [b.strip() for b in blocks if b.strip()]


def _require(value: str, name: str) -> str:
    """Validate required environment variable."""
    if not value:
        raise ValueError(f"Missing required environment variable: {name}")
    return value


def add_domain_metadata(chunks: List[Document], source_name: str, domain: str = "family_code"):
    """Add domain and source metadata."""
    for chunk in chunks:
        meta = chunk.metadata or {}
        meta.update({"domain": domain, "source": source_name})
        chunk.metadata = meta
        # ensure top-level domain is set for indexing/filtering
        chunk.domain = domain


def normalize_code_section(block: str, domain: str) -> str | None:
    """Extract and normalize a code section/rule like 'FC 2550' or 'CRC 3.1332'."""
    if domain == "rules_of_court":
        m = re.search(r"\brule\s+([\d.]+)", block, re.IGNORECASE)
        if m:
            return f"CRC {m.group(1)}"
    else:
        m = re.search(r"§\s*([\d.]+)", block)
        if m:
            prefix_map = {
                "family_code": "FC",
                "code_of_civil_procedure": "CCP",
                "civil_code": "CIV",
                "evidence_code": "EVID",
            }
            prefix = prefix_map.get(domain, "CODE")
            return f"{prefix} {m.group(1)}"
    return None


def extract_heading(block: str) -> str | None:
    """Take the first non-empty line as a lightweight heading."""
    for line in block.splitlines():
        line = line.strip()
        if line:
            return line[:240]
    return None


def subchunk_large_block(block: str, num_tokens: int, domain: str) -> List[str]:
    """
    Sub-chunk a large section/case block using simple subsection markers when above token limit.
    Markers considered: (a), (1), 1., A., etc.
    """
    # quick token estimate: use length heuristic (~4 chars per token)
    approx_tokens = max(1, len(block) // 4)
    if approx_tokens <= num_tokens:
        return [block]

    markers = _find_markers(
        block,
        [
            re.compile(r"^\([a-zA-Z]\)", re.MULTILINE),  # (a)
            re.compile(r"^\(\d+\)", re.MULTILINE),       # (1)
            re.compile(r"^\d+\.", re.MULTILINE),         # 1.
            re.compile(r"^[A-Z]\.", re.MULTILINE),       # A.
        ],
    )
    if not markers:
        return [block]

    chunks: List[str] = []
    last = 0
    for pos in markers:
        if pos != 0:
            chunks.append(block[last:pos])
        last = pos
    chunks.append(block[last:])

    # If still too big, fall back to returning original block
    if len(chunks) == 1:
        return [block]
    return [c.strip() for c in chunks if c.strip()]


def _infer_courtlistener_domain(lower_stem: str) -> str | None:
    """Return domain for known CourtListener bundles."""
    if lower_stem in COURTLISTENER_STEM_MAP:
        return COURTLISTENER_STEM_MAP[lower_stem]

    for stem, domain in COURTLISTENER_STEM_MAP.items():
        if lower_stem.startswith(stem):
            return domain
    return None


def infer_domain_from_blob(name: str) -> str:
    """
    Infer domain from the blob name using lowercase prefixes before the first underscore.
    Examples (case-insensitive):
      fam_* -> family_code
      ccp_* -> code_of_civil_procedure
      civ_* -> civil_code
      evid_* -> evidence_code
      crc_* -> rules_of_court
    If no prefix matches, fall back to the stem (filename without extension).
    """
    stem = Path(name).name
    stem = stem.rsplit(".", 1)[0] if "." in stem else stem
    lower_stem = stem.lower()

    courtlistener_domain = _infer_courtlistener_domain(lower_stem)
    if courtlistener_domain:
        return courtlistener_domain

    prefix_map = {
        "fam": "family_code",
        "ccp": "code_of_civil_procedure",
        "civ": "civil_code",
        "evid": "evidence_code",
        "crc": "rules_of_court",
    }

    if "_" in lower_stem:
        prefix = lower_stem.split("_", 1)[0]
        if prefix in prefix_map:
            return prefix_map[prefix]

    return lower_stem.strip()


def bytes_to_text(name: str, data: bytes) -> tuple[str, bool]:
    """Convert blob bytes to text. Only TXT is supported here; PDF path removed."""
    text = data.decode("utf-8", errors="replace")
    return text, False


def download_blob_to_temp(storage: LegalDocsStorage, blob_name: str, chunk_size: int = 16 * 1024 * 1024) -> str:
    """
    Stream a blob to a temporary file to avoid holding the entire payload in memory.
    Returns the temporary file path (caller is responsible for deletion).
    """
    tmp = tempfile.NamedTemporaryFile(delete=False)
    tmp_path = tmp.name
    try:
        blob_client = storage.blob_service.get_blob_client(
            container=storage.raw_container,
            blob=blob_name,
        )
        # Some SDK versions don't accept chunk_size on chunks(); rely on default chunking.
        stream = blob_client.download_blob()
        for part in stream.chunks():
            tmp.write(part)
        tmp.flush()
        tmp.close()
        return tmp_path
    except Exception:
        tmp.close()
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise


def extract_decision_date(text: str) -> str | None:
    """Find a decision/filing date in common formats."""
    iso = DECISION_DATE_RE_ISO.search(text)
    if iso:
        return iso.group(0)

    long_form = DECISION_DATE_RE_LONG.search(text)
    if long_form:
        return long_form.group(0)
    return None


def main():
    _require(SEARCH_KEY, "AZURE_SEARCH_KEY")
    embedding_endpoint = resolve_embedding_endpoint()
    
    storage = LegalDocsStorage()
    blobs = storage.list_documents(extensions=SUPPORTED_EXTENSIONS)

    print(
        f"Found {len(blobs)} document(s) in {storage.raw_container} "
        f"({len(blobs)} TXT)"
    )

    search_endpoint = f"https://{SEARCH_SERVICE}.search.windows.net"
    index_client = SearchIndexClient(search_endpoint, AzureKeyCredential(SEARCH_KEY))
    search_client = SearchClient(search_endpoint, INDEX_NAME, AzureKeyCredential(SEARCH_KEY))

    # Ensure index exists (same schema as prepdocs)
    create_search_index(INDEX_NAME, index_client)

    total_chunks = 0
    failed_files = 0
    for name in blobs:
        print(f"Processing {name} ...")
        success = False
        for attempt in range(10):
            tmp_path = None
            try:
                # Stream download to temp file to avoid holding the blob in memory
                tmp_path = download_blob_to_temp(storage, name)

                with open(tmp_path, "rb") as f, mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as mm:
                    # mmap avoids an extra Python-level buffer; decode later in bytes_to_text
                    text, _ = bytes_to_text(name, mm[:])

                # Determine domain: LEGAL_DOMAIN takes precedence; otherwise infer from blob name
                domain_for_blob = LEGAL_DOMAIN or infer_domain_from_blob(name)
                if not domain_for_blob:
                    raise ValueError(
                        f"Could not determine domain for blob {name}. Set LEGAL_DOMAIN or use a file name containing the domain."
                    )

                blocks = split_legal_blocks(text, domain_for_blob)
                file_name_for_chunks = name

                chunk_idx = 0
                batch: List[Document] = []
                chunks_for_file = 0
                for block in blocks:
                    has_section = SECTION_RE.search(block) is not None
                    has_case = CASE_RE.search(block) is not None
                    # Section/case: try to keep whole with higher limit
                    block_tokens = TOKENS_SECTION_OR_CASE if (has_section or has_case) else TOKENS_DEFAULT
                    # Sub-chunk large sections/cases if needed
                    sub_blocks = subchunk_large_block(block, TOKENS_SECTION_OR_CASE, domain_for_blob) if (has_section or has_case) else [block]

                    for sub_block in sub_blocks:
                        result = chunk_content(
                            content=sub_block,
                            file_name=file_name_for_chunks,
                            add_embeddings=True,
                            embedding_endpoint=embedding_endpoint,
                            ignore_errors=False,
                            num_tokens=block_tokens if (has_section or has_case) else TOKENS_DEFAULT,
                            min_chunk_size=5,
                        )
                        add_domain_metadata(result.chunks, source_name=name, domain=domain_for_blob)

                        for chunk in result.chunks:
                            meta = chunk.metadata or {}
                            sec = SECTION_RE.search(sub_block)
                            case = CASE_RE.search(sub_block)
                            citations = set(CITATION_RE.findall(sub_block))
                            citations.update(CITATION_RE_COURT.findall(sub_block))
                            if sec:
                                meta["section_id"] = sec.group(0).strip()
                            if case:
                                meta["case_name"] = case.group(0).strip()
                            if citations:
                                meta["citations"] = list({c.strip() for c in citations})
                            code_section = normalize_code_section(sub_block, domain_for_blob)
                            if code_section:
                                meta["code_section"] = code_section
                            heading = extract_heading(sub_block)
                            if heading:
                                meta["heading"] = heading
                            decision_date = extract_decision_date(sub_block)
                            if decision_date:
                                meta["decision_date"] = decision_date
                            court_info = COURTLISTENER_DOMAIN_INFO.get(domain_for_blob)
                            if court_info:
                                meta.setdefault("court", court_info["court"])
                                meta.setdefault("jurisdiction", court_info["jurisdiction"])
                            meta["chunk_index"] = chunk_idx
                            # doc_type heuristics
                            if domain_for_blob in {"family_code", "code_of_civil_procedure", "civil_code", "evidence_code"}:
                                meta["doc_type"] = "statute"
                            elif domain_for_blob == "rules_of_court":
                                meta["doc_type"] = "rule"
                            else:
                                meta["doc_type"] = "case"

                            # stable, search-safe id per chunk to avoid overwrites and invalid keys
                            chunk.id = make_chunk_id(name, chunk_idx)
                            chunk.metadata = json.dumps(meta)
                            chunk_idx += 1

                        batch.extend(result.chunks)
                        chunks_for_file += len(result.chunks)
                        # Upload in small batches to avoid holding all chunks in memory
                        if len(batch) >= UPLOAD_BATCH_SIZE:
                            est_bytes = estimate_batch_bytes(batch)
                            print(f"Uploading batch of {len(batch)} chunk(s) for {name} to index {INDEX_NAME} (~{est_bytes / (1024 * 1024):.2f} MB) ...")
                            upload_documents_to_index(batch, search_client)
                            total_chunks += len(batch)
                            batch.clear()

                        print(f" -> {len(result.chunks)} chunk(s) in block (sub)")

                # Flush any remaining chunks for this file
                if batch:
                    est_bytes = estimate_batch_bytes(batch)
                    print(f"Uploading final batch of {len(batch)} chunk(s) for {name} to index {INDEX_NAME} (~{est_bytes / (1024 * 1024):.2f} MB) ...")
                    upload_documents_to_index(batch, search_client)
                    total_chunks += len(batch)
                    batch.clear()

                if chunks_for_file == 0:
                    print(f"No chunks for {name}; skipping upload.")
                    success = True
                    break

                success = True
                break
            except Exception as file_err:
                print(f"Error processing {name} (attempt {attempt + 1}/3): {file_err}")
            finally:
                if tmp_path:
                    try:
                        os.remove(tmp_path)
                    except OSError:
                        pass
        if not success:
            failed_files += 1

    if total_chunks == 0:
        print("No chunks to index. Exiting.")
    else:
        print(f"Done. Uploaded {total_chunks} chunk(s) across {len(blobs) - failed_files} file(s). Failed files: {failed_files}.")


if __name__ == "__main__":
    main()

