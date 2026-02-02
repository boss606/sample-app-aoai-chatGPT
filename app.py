"""
Joogni - California Family Law AI Assistant
Main Application with Dashboard, Chat, Calculators, and M365 Integration
Now with Function Calling for Emails, Calendar, and OneDrive
"""

import os
import sys
import io
import math
import json
import base64
import re
import httpx
import uuid
import time
from datetime import datetime, timedelta
from typing import List, Tuple, Optional

from fastapi import FastAPI, Request, HTTPException, UploadFile, File
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from openai import AzureOpenAI
from azure.search.documents import SearchClient
from azure.core.credentials import AzureKeyCredential
from azure.storage.blob import (
    BlobServiceClient,
    BlobSasPermissions,
    generate_blob_sas,
)

from azure.identity import DefaultAzureCredential
from pypdf import PdfReader
import tiktoken

from backend.storage.blob_storage import LegalDocsStorage

from dotenv import load_dotenv
load_dotenv(override=False)

app = FastAPI(title="Joogni", description="California Family Law AI Assistant")

# Mount static files only if directory exists
static_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
if os.path.isdir(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")
elif os.path.isdir("/home/site/wwwroot/static"):
    app.mount("/static", StaticFiles(directory="/home/site/wwwroot/static"), name="static")

# Templates - find the correct directory
templates_dir = "templates"
if os.path.isdir("/home/site/wwwroot/templates"):
    templates_dir = "/home/site/wwwroot/templates"
elif os.path.isdir(os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates")):
    templates_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates")
templates = Jinja2Templates(directory=templates_dir)

# System prompt for Joogni
JOOGNI_SYSTEM_PROMPT = """You are Joogni, a California family law AI assistant designed for attorneys at Gill Law Group. 

Your expertise includes:
- California Family Code
- Child custody and visitation (FC §3000-3465)
- Child support guidelines (FC §4050-4076)
- Spousal support (FC §4300-4360)
- Property division (FC §760-2660)
- Domestic violence restraining orders
- Dissolution procedures and timelines

You have access to the user's Microsoft 365 account and can:
- Search and read their Outlook emails
- Check their calendar for meetings and hearings
- Search their OneDrive files

When a user asks about emails, meetings, calendar events, or files, USE THE AVAILABLE TOOLS to search and retrieve that information. Don't say you can't access their data - you CAN access it through the tools provided.

When answering legal questions:
1. Cite specific Family Code sections when applicable
2. Reference relevant case law when appropriate
3. Provide practical, actionable guidance
4. Note any recent changes in law or procedure
5. Flag issues that may require judicial discretion
6. Use the context provided to answer the question. If the context is not sufficient, respond politely: 'I don't have enough information to answer that. Please reformulate with more specifics related to California family law (including, but not limited to, custody modification, evidence code exclusion, domestic violence findings, child support calculations), and I'll try again. I will not invent information.'
7. The information in the context is the only information you can use to answer the legal question. Do not use any other information or sources.


For document analysis:
- Identify key dates, parties, and issues
- Flag potential problems or inconsistencies
- Suggest follow-up actions

For email/calendar context:
- Summarize case status based on communications
- Identify upcoming deadlines
- Note any urgent matters

Always maintain attorney-client privilege awareness and remind users not to share client-identifying information outside secure channels.

Format responses with clear structure when appropriate. Be thorough but concise."""

# Multi-domain legal search config (single index with domain field)
LEGAL_DOMAINS = [
    d.strip()
    for d in os.getenv("LEGAL_DOMAINS", "family_code,evidence_code").split(",")
    if d.strip()
]
LEGAL_MAX_DOMAINS = int(os.getenv("LEGAL_MAX_DOMAINS", "3"))
LEGAL_TOP_PER_DOMAIN = int(os.getenv("LEGAL_TOP_PER_DOMAIN", "3"))

# Upload / attachment settings
# Defaults raised to support uploads with multiple large PDFs; adjust via env vars.
MAX_UPLOAD_SIZE_MB = int(os.getenv("MAX_UPLOAD_SIZE_MB", "200"))
MAX_UPLOAD_BYTES = MAX_UPLOAD_SIZE_MB * 1024 * 1024
MAX_ATTACHMENT_FILES = int(os.getenv("MAX_ATTACHMENT_FILES", "20"))
EMBEDDING_DEPLOYMENT = os.getenv("AZURE_OPENAI_EMBEDDING_DEPLOYMENT", "text-embedding-3-large")
PDF_OCR_MAX_PAGES = int(os.getenv("PDF_OCR_MAX_PAGES", "20"))
PDF_OCR_DPI = int(os.getenv("PDF_OCR_DPI", "200"))
PDF_OCR_LANG = os.getenv("PDF_OCR_LANG", "eng")

deployment_name = os.getenv("AZURE_OPENAI_EMBEDDING_DEPLOYMENT")
print("AZURE_OPENAI_EMBEDDING_DEPLOYMENT present:", bool(deployment_name))

def run_multi_domain_search(
    search_client: SearchClient,
    query: str,
    domains: List[str],
    max_domains: int,
    top_per_domain: int,
):
    """
    Controlled multi-domain search: applies filter "domain eq '<domain>'" for each candidate.
    If there are no hits in any, performs a search without filter as a fallback.
    Returns (context_chunks, domains_with_hits).
    """
    context_chunks = []
    domains_with_hits = []

    candidates = [d.strip() for d in domains if d.strip()][:max_domains]

    for dom in candidates:
        try:
            results = search_client.search(
                search_text=query,
                top=top_per_domain,
                filter=f"domain eq '{dom}'",
            )
            hit_count = 0
            for r in results:
                content = (r.get("content") or "").strip()
                meta = (r.get("metadata") or "").strip()
                source = r.get("filepath") or r.get("source") or r.get("url") or ""
                if not content:
                    continue
                context_chunks.append(
                    f"[DOMAIN: {dom}] [Source: {source}]\n{content}\nMeta: {meta}"
                )
                hit_count += 1
            if hit_count > 0:
                domains_with_hits.append(dom)
        except Exception as e:
            # log and continue with other domains
            print(f"Search error for domain {dom}: {e}", file=sys.stderr)

    # Fallback without filter if nothing found
    if not context_chunks:
        try:
            results = search_client.search(search_text=query, top=top_per_domain)
            for r in results:
                content = (r.get("content") or "").strip()
                meta = (r.get("metadata") or "").strip()
                source = r.get("filepath") or r.get("source") or r.get("url") or ""
                if not content:
                    continue
                context_chunks.append(
                    f"[DOMAIN: unknown] [Source: {source}]\n{content}\nMeta: {meta}"
                )
            if context_chunks:
                domains_with_hits.append("unknown")
        except Exception as e:
            print(f"Fallback search error: {e}", file=sys.stderr)

    return context_chunks, domains_with_hits


# Define tools for function calling
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_emails",
            "description": "Search the user's Outlook emails. Use this when the user asks about emails, messages, correspondence, or communications from specific people or about specific topics.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query - can be a person's name, email address, subject, or keywords"
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of emails to return (default 10)",
                        "default": 10
                    }
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_email_content",
            "description": "Get the full content of a specific email by its ID. Use this after search_emails to read the full body of an email.",
            "parameters": {
                "type": "object",
                "properties": {
                    "message_id": {
                        "type": "string",
                        "description": "The ID of the email message to retrieve"
                    }
                },
                "required": ["message_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "search_calendar",
            "description": "Search the user's calendar for meetings, appointments, hearings, or events. Use this when the user asks about their schedule, meetings, hearings, or calendar.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Optional search term to filter events by subject or location. Leave empty to get all upcoming events.",
                        "default": ""
                    },
                    "days_ahead": {
                        "type": "integer",
                        "description": "Number of days ahead to search (default 7 for a week)",
                        "default": 7
                    }
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "search_files",
            "description": "Search the user's OneDrive files. Use this when the user asks about documents, files, or wants to find specific files.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query - filename, content keywords, or file type"
                    }
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_todays_events",
            "description": "Get all of today's calendar events. Use this when the user asks 'do I have meetings today', 'what's on my calendar today', or similar.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": []
            }
        }
    }
]


def get_user_info(request: Request) -> dict:
    """Extract user info from Azure Easy Auth headers."""
    principal = request.headers.get("X-MS-CLIENT-PRINCIPAL")
    if principal:
        try:
            decoded = base64.b64decode(principal)
            return json.loads(decoded)
        except:
            pass
    return None


def is_authenticated(request: Request) -> bool:
    """Check if user is authenticated via Azure Easy Auth."""
    return True
    # principal = request.headers.get("X-MS-CLIENT-PRINCIPAL")
    # id_token = request.headers.get("X-MS-TOKEN-AAD-ID-TOKEN")
    # return bool(principal or id_token)


def get_graph_token(request: Request) -> Optional[str]:
    """Get Microsoft Graph access token from Easy Auth."""
    token = request.headers.get("X-MS-TOKEN-AAD-ACCESS-TOKEN")
    principal = request.headers.get("X-MS-CLIENT-PRINCIPAL")
    id_token = request.headers.get("X-MS-TOKEN-AAD-ID-TOKEN")
    expires_on = request.headers.get("X-MS-TOKEN-AAD-EXPIRES-ON")
    print(
        "Graph token presence: "
        f"access={bool(token)} len={len(token) if token else 0} "
        f"principal={bool(principal)} id_token={bool(id_token)} "
        f"expires_on={expires_on}",
        file=sys.stderr,
    )
    if token:
        print(f"Graph token prefix: {token[:10]}...", file=sys.stderr)
    return token or None


def _get_token_expiry(request: Request) -> Optional[int]:
    """Return token expiry (epoch seconds) from Easy Auth header."""
    expires_on = request.headers.get("X-MS-TOKEN-AAD-EXPIRES-ON")
    if not expires_on:
        return None
    try:
        return int(expires_on)
    except Exception:
        # Some environments send ISO strings like 2026-01-26T23:07:48.0666466Z
        try:
            iso = expires_on.replace("Z", "+00:00")
            return int(datetime.fromisoformat(iso).timestamp())
        except Exception:
            return None


def _token_near_expiry(expiry: Optional[int], skew_seconds: int = 300) -> bool:
    """True if expiry is within skew window."""
    if not expiry:
        return False
    now = int(time.time())
    return expiry - now <= skew_seconds


async def refresh_graph_token(request: Request) -> Optional[str]:
    """
    Attempt to refresh Easy Auth tokens via /.auth/refresh using the caller's cookies.
    Returns a new access token if present in the response.
    """
    cookies = request.headers.get("cookie")
    if not cookies:
        print("[Graph token] No cookies on request; cannot refresh.", file=sys.stderr)
        return None

    refresh_url = str(request.base_url).rstrip("/") + "/.auth/refresh"
    try:
        async with httpx.AsyncClient(follow_redirects=False) as client:
            resp = await client.get(
                refresh_url,
                headers={"cookie": cookies},
                timeout=10.0,
            )
    except Exception as err:
        print(f"[Graph token] Refresh call failed: {err}", file=sys.stderr)
        return None

    if resp.status_code not in (200, 302):
        print(
            f"[Graph token] Refresh failed status={resp.status_code} body={resp.text[:200]}",
            file=sys.stderr,
        )
        return None

    try:
        data = resp.json()
    except Exception as err:
        print(f"[Graph token] Refresh JSON parse error: {err}", file=sys.stderr)
        return None

    if not isinstance(data, dict):
        print("[Graph token] Refresh response not a dict; cannot extract token.", file=sys.stderr)
        return None

    new_token = (
        data.get("access_token")
        or data.get("access_token_v2")
        or data.get("authenticationToken")
    )
    print(
        f"[Graph token] Refresh success token_present={bool(new_token)} "
        f"expires_on={data.get('expires_on')}",
        file=sys.stderr,
    )
    return new_token


async def get_valid_graph_token(request: Request, refresh_skew_seconds: int = 300) -> Optional[str]:
    """
    Return a usable Graph token, attempting refresh if the Easy Auth token is close to expiry.
    """
    token = get_graph_token(request)
    expiry = _get_token_expiry(request)
    if token and not _token_near_expiry(expiry, refresh_skew_seconds):
        return token

    refreshed = await refresh_graph_token(request)
    if refreshed:
        return refreshed

    # Fall back to whatever we had (may be None / expired)
    return token


def _get_blob_service_client() -> BlobServiceClient:
    """
    Return a BlobServiceClient using connection string if present, otherwise Managed Identity.
    """
    connection_string = os.getenv("AZURE_STORAGE_CONNECTION_STRING")
    account_name = os.getenv("AZURE_STORAGE_ACCOUNT_NAME", "glgaistorage")

    if connection_string:
        return BlobServiceClient.from_connection_string(connection_string)

    credential = DefaultAzureCredential()
    account_url = f"https://{account_name}.blob.core.windows.net"
    return BlobServiceClient(account_url, credential=credential)


def _ensure_container(blob_service: BlobServiceClient, container: str):
    container_client = blob_service.get_container_client(container)
    try:
        container_client.create_container()
    except Exception:
        # likely already exists
        pass


def _generate_sas_url(blob_service: BlobServiceClient, container: str, blob_name: str, expiry_hours: int = 1) -> Tuple[str, str]:
    """
    Generate a SAS URL for uploading a blob. Supports both account key and user delegation key.
    Returns (upload_url, blob_name).
    """
    expiry = datetime.utcnow() + timedelta(hours=expiry_hours)
    account_name = blob_service.account_name

    sas_token = None
    # Try account key first (connection string case)
    credential = getattr(blob_service, "credential", None)
    account_key = getattr(credential, "account_key", None)

    if account_key:
        sas_token = generate_blob_sas(
            account_name=account_name,
            container_name=container,
            blob_name=blob_name,
            account_key=account_key,
            permission=BlobSasPermissions(write=True, create=True),
            expiry=expiry,
        )
    else:
        # Managed Identity path: use user delegation key
        delegation_key = blob_service.get_user_delegation_key(datetime.utcnow(), expiry)
        sas_token = generate_blob_sas(
            account_name=account_name,
            container_name=container,
            blob_name=blob_name,
            user_delegation_key=delegation_key,
            permission=BlobSasPermissions(write=True, create=True),
            expiry=expiry,
        )

    blob_client = blob_service.get_blob_client(container=container, blob=blob_name)
    upload_url = f"{blob_client.url}?{sas_token}"
    return upload_url, blob_name


def _safe_blob_name(filename: str) -> str:
    base = os.path.basename(filename)
    base = re.sub(r"[^A-Za-z0-9._-]", "_", base)
    return f"{uuid.uuid4()}-{base}"


def _extract_text_from_pdf(file_bytes: bytes, filename: str) -> str:
    try:
        reader = PdfReader(io.BytesIO(file_bytes))
        texts = []
        for page in reader.pages:
            page_text = page.extract_text() or ""
            texts.append(page_text)
        content = "\n".join(texts).strip()
        if content:
            return content

        page_count = len(reader.pages)
        if not page_count:
            raise HTTPException(status_code=400, detail=f"PDF {filename} is empty or has no readable pages.")

        if page_count > PDF_OCR_MAX_PAGES:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"PDF {filename} has {page_count} pages; OCR limit is {PDF_OCR_MAX_PAGES}. "
                    "Upload a smaller file or one with embedded text."
                ),
            )

        try:
            from pdf2image import convert_from_bytes  # type: ignore
            import pytesseract  # type: ignore
        except Exception as import_err:
            raise HTTPException(
                status_code=500,
                detail=(
                    f"OCR unavailable for {filename}: install poppler + tesseract on the system and "
                    f"pdf2image/pytesseract libraries. Error: {import_err}"
                ),
            ) from import_err

        images = convert_from_bytes(
            file_bytes,
            dpi=PDF_OCR_DPI,
            first_page=1,
            last_page=page_count,
        )
        ocr_chunks = []
        for idx, img in enumerate(images, start=1):
            try:
                txt = pytesseract.image_to_string(img, lang=PDF_OCR_LANG) or ""
            except Exception as ocr_err:
                print(f"OCR failed on page {idx}: {ocr_err}", file=sys.stderr)
                continue
            if txt.strip():
                ocr_chunks.append(txt.strip())

        ocr_content = "\n".join(ocr_chunks).strip()
        if ocr_content:
            return ocr_content

        raise HTTPException(
            status_code=400,
            detail=(
                f"PDF {filename} appears to be scanned or redacted and OCR found no text. "
                "Upload a clearer copy or one with embedded OCR text."
            ),
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to read PDF {filename}: {e}")


def _chunk_text(text: str, max_tokens: int = 350, overlap: int = 60, max_chunks: int = 200) -> List[str]:
    """
    Simple token-based chunker using tiktoken. Returns a list of chunk strings.
    """
    encoder = tiktoken.get_encoding("cl100k_base")
    tokens = encoder.encode(text)
    chunks = []
    start = 0
    while start < len(tokens) and len(chunks) < max_chunks:
        end = min(start + max_tokens, len(tokens))
        chunk_tokens = tokens[start:end]
        chunks.append(encoder.decode(chunk_tokens))
        if end == len(tokens):
            break
        start = end - overlap
    return chunks


def _cosine_similarity(a: List[float], b: List[float]) -> float:
    denom = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    if denom == 0:
        return 0.0
    return sum(x * y for x, y in zip(a, b)) / denom


async def _build_attachment_context(blobs: List[dict], query: str, client: AzureOpenAI) -> List[str]:
    """
    Download, chunk, embed attachments and return top chunks as context strings.
    """
    if not blobs:
        return []
    if not EMBEDDING_DEPLOYMENT:
        raise HTTPException(status_code=500, detail="Embedding deployment not configured")

    if len(blobs) > MAX_ATTACHMENT_FILES:
        raise HTTPException(status_code=400, detail=f"Too many files. Max {MAX_ATTACHMENT_FILES}.")

    storage = LegalDocsStorage()
    attachment_sections: List[str] = []
    top_chunks_per_file = 3

    # Embed query once
    safe_query = str(query or "legal question")
    print("EMBED INPUT TYPE:", type(safe_query), file=sys.stderr)
    print("EMBED INPUT PREVIEW:", repr(safe_query)[:500], file=sys.stderr)
    query_embed_resp = client.embeddings.create(
        model=EMBEDDING_DEPLOYMENT,
        input=[safe_query]
    )
    query_vec = query_embed_resp.data[0].embedding

    for blob in blobs:
        blob_name = blob.get("blob_name")
        original_filename = blob.get("original_filename", "file.pdf")
        if not blob_name:
            continue
        file_bytes = b""
        chunk_texts: List[str] = []
        try:
            file_bytes = storage.download_file(blob_name)
            if len(file_bytes) > MAX_UPLOAD_BYTES:
                raise HTTPException(status_code=400, detail=f"File {original_filename} exceeds {MAX_UPLOAD_SIZE_MB}MB limit.")

            if original_filename.lower().endswith(".pdf"):
                text = _extract_text_from_pdf(file_bytes, original_filename)
            elif original_filename.lower().endswith(".txt"):
                text = file_bytes.decode("utf-8", errors="ignore")
            else:
                raise HTTPException(status_code=400, detail=f"Unsupported file type for {original_filename}. Use PDF or TXT.")

            chunk_texts = _chunk_text(text)
        finally:
            # Delete the blob regardless of processing outcome (best-effort)
            storage.delete_file(blob_name)

        if not chunk_texts:
            continue

        # Embed chunks for this file
        embed_resp = client.embeddings.create(
            model=EMBEDDING_DEPLOYMENT,
            input=[str(c) for c in chunk_texts]
        )
        scored: List[Tuple[float, str]] = []
        for chunk_text, emb in zip(chunk_texts, embed_resp.data):
            score = _cosine_similarity(query_vec, emb.embedding)
            scored.append((score, str(chunk_text)))

        scored.sort(key=lambda x: x[0], reverse=True)
        top_chunks = scored[:top_chunks_per_file]
        if not top_chunks:
            continue

        section_lines = [f"[Attachment: {original_filename}]"]
        for idx, (_, chunk_text) in enumerate(top_chunks, start=1):
            section_lines.append(f"{idx}. {chunk_text}")
        attachment_sections.append("\n".join(section_lines))

    return attachment_sections


# ============== Tool Execution Functions ==============

async def execute_search_emails(token: str, query: str, limit: int = 10, request: Optional[Request] = None) -> dict:
    """Execute email search via Microsoft Graph."""
    try:
        async with httpx.AsyncClient() as client:
            url = f"https://graph.microsoft.com/v1.0/me/messages?$search=\"{query}\"&$top={limit}&$select=id,subject,from,receivedDateTime,bodyPreview,hasAttachments"
            print(f"[Graph] search_emails url={url}", file=sys.stderr)
            
            headers = {"Authorization": f"Bearer {token}"}
            response = await client.get(url, headers=headers, timeout=30.0)
            
            # If token expired, try one refresh + retry (best effort)
            if response.status_code == 401 and request:
                print("[Graph] search_emails 401; attempting token refresh + retry", file=sys.stderr)
                refreshed = await refresh_graph_token(request)
                if refreshed:
                    headers = {"Authorization": f"Bearer {refreshed}"}
                    response = await client.get(url, headers=headers, timeout=30.0)
                    token = refreshed  # use refreshed token for parsing below
                    print(f"[Graph] search_emails retry status={response.status_code}", file=sys.stderr)
            
            if response.status_code != 200:
                print(f"[Graph] search_emails failed status={response.status_code} body={response.text[:300]}", file=sys.stderr)
                return {"error": f"Failed to search emails: {response.status_code}"}
            
            data = response.json()
            emails = []
            for msg in data.get("value", []):
                emails.append({
                    "id": msg.get("id"),
                    "subject": msg.get("subject", "(No subject)"),
                    "from": msg.get("from", {}).get("emailAddress", {}).get("name", "Unknown"),
                    "from_email": msg.get("from", {}).get("emailAddress", {}).get("address", ""),
                    "date": msg.get("receivedDateTime", ""),
                    "preview": msg.get("bodyPreview", "")[:200],
                    "hasAttachments": msg.get("hasAttachments", False)
                })
            
            return {"emails": emails, "count": len(emails)}
            
    except Exception as e:
        return {"error": str(e)}


async def execute_get_email_content(token: str, message_id: str) -> dict:
    """Get full email content."""
    try:
        async with httpx.AsyncClient() as client:
            print(f"[Graph] get_email_content id={message_id}", file=sys.stderr)
            response = await client.get(
                f"https://graph.microsoft.com/v1.0/me/messages/{message_id}?$select=id,subject,from,toRecipients,receivedDateTime,body",
                headers={"Authorization": f"Bearer {token}"},
                timeout=30.0
            )
            
            if response.status_code != 200:
                print(f"[Graph] get_email_content failed status={response.status_code} body={response.text[:300]}", file=sys.stderr)
                return {"error": f"Failed to get email: {response.status_code}"}
            
            msg = response.json()
            
            # Extract text from HTML body
            body_content = msg.get("body", {}).get("content", "")
            text_body = re.sub(r'<[^>]+>', ' ', body_content)
            text_body = re.sub(r'\s+', ' ', text_body).strip()
            
            return {
                "subject": msg.get("subject", "(No subject)"),
                "from": msg.get("from", {}).get("emailAddress", {}).get("name", "Unknown"),
                "from_email": msg.get("from", {}).get("emailAddress", {}).get("address", ""),
                "date": msg.get("receivedDateTime", ""),
                "body": text_body[:3000]
            }
            
    except Exception as e:
        return {"error": str(e)}


async def execute_search_calendar(token: str, query: str = "", days_ahead: int = 7) -> dict:
    """Search calendar events."""
    try:
        start = datetime.utcnow()
        end = start + timedelta(days=days_ahead)
        
        async with httpx.AsyncClient() as client:
            url = f"https://graph.microsoft.com/v1.0/me/calendarView?startDateTime={start.isoformat()}Z&endDateTime={end.isoformat()}Z&$select=id,subject,start,end,location,organizer,isAllDay&$orderby=start/dateTime&$top=50"
            print(f"[Graph] search_calendar url={url} query={query}", file=sys.stderr)
            
            response = await client.get(
                url,
                headers={"Authorization": f"Bearer {token}"},
                timeout=30.0
            )
            
            if response.status_code != 200:
                print(f"[Graph] search_calendar failed status={response.status_code} body={response.text[:300]}", file=sys.stderr)
                return {"error": f"Failed to search calendar: {response.status_code}"}
            
            data = response.json()
            events = []
            
            for event in data.get("value", []):
                if query:
                    query_lower = query.lower()
                    subject = event.get("subject", "").lower()
                    location = str(event.get("location", {}).get("displayName", "")).lower()
                    if query_lower not in subject and query_lower not in location:
                        continue
                
                events.append({
                    "id": event.get("id"),
                    "subject": event.get("subject", "(No title)"),
                    "start": event.get("start", {}).get("dateTime", ""),
                    "end": event.get("end", {}).get("dateTime", ""),
                    "location": event.get("location", {}).get("displayName", ""),
                    "isAllDay": event.get("isAllDay", False),
                    "organizer": event.get("organizer", {}).get("emailAddress", {}).get("name", "")
                })
            
            return {"events": events, "count": len(events), "days_searched": days_ahead}
            
    except Exception as e:
        return {"error": str(e)}


async def execute_get_todays_events(token: str) -> dict:
    """Get today's calendar events."""
    try:
        now = datetime.utcnow()
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=1)
        
        async with httpx.AsyncClient() as client:
            url = f"https://graph.microsoft.com/v1.0/me/calendarView?startDateTime={start.isoformat()}Z&endDateTime={end.isoformat()}Z&$select=id,subject,start,end,location,organizer,isAllDay&$orderby=start/dateTime"
            print(f"[Graph] get_todays_events url={url}", file=sys.stderr)
            
            response = await client.get(
                url,
                headers={"Authorization": f"Bearer {token}"},
                timeout=30.0
            )
            
            if response.status_code != 200:
                print(f"[Graph] get_todays_events failed status={response.status_code} body={response.text[:300]}", file=sys.stderr)
                return {"error": f"Failed to get calendar: {response.status_code}"}
            
            data = response.json()
            events = []
            
            for event in data.get("value", []):
                events.append({
                    "subject": event.get("subject", "(No title)"),
                    "start": event.get("start", {}).get("dateTime", ""),
                    "end": event.get("end", {}).get("dateTime", ""),
                    "location": event.get("location", {}).get("displayName", ""),
                    "isAllDay": event.get("isAllDay", False)
                })
            
            if not events:
                return {"message": "No meetings or events scheduled for today.", "events": [], "count": 0}
            
            return {"events": events, "count": len(events), "date": start.strftime("%A, %B %d, %Y")}
            
    except Exception as e:
        return {"error": str(e)}


async def execute_search_files(token: str, query: str) -> dict:
    """Search OneDrive files."""
    try:
        async with httpx.AsyncClient() as client:
            print(f"[Graph] search_files query={query}", file=sys.stderr)
            response = await client.get(
                f"https://graph.microsoft.com/v1.0/me/drive/root/search(q='{query}')?$select=id,name,webUrl,createdDateTime,size,file,folder&$top=20",
                headers={"Authorization": f"Bearer {token}"},
                timeout=30.0
            )
            
            if response.status_code != 200:
                print(f"[Graph] search_files failed status={response.status_code} body={response.text[:300]}", file=sys.stderr)
                return {"error": f"Failed to search files: {response.status_code}"}
            
            data = response.json()
            files = []
            
            for item in data.get("value", []):
                files.append({
                    "id": item.get("id"),
                    "name": item.get("name"),
                    "url": item.get("webUrl"),
                    "created": item.get("createdDateTime"),
                    "size": item.get("size", 0),
                    "isFolder": "folder" in item
                })
            
            return {"files": files, "count": len(files)}
            
    except Exception as e:
        return {"error": str(e)}


async def execute_tool(tool_name: str, arguments: dict, token: str, request: Optional[Request] = None) -> str:
    """Execute a tool and return the result as a string."""
    try:
        print(f"execute_tool start tool={tool_name} args_keys={list(arguments.keys())} token_present={bool(token)}", file=sys.stderr)
        if tool_name == "search_emails":
            result = await execute_search_emails(
                token,
                arguments.get("query", ""),
                arguments.get("limit", 10),
                request=request,
            )
        elif tool_name == "get_email_content":
            result = await execute_get_email_content(
                token,
                arguments.get("message_id", "")
            )
        elif tool_name == "search_calendar":
            result = await execute_search_calendar(
                token,
                arguments.get("query", ""),
                arguments.get("days_ahead", 7)
            )
        elif tool_name == "get_todays_events":
            result = await execute_get_todays_events(token)
        elif tool_name == "search_files":
            result = await execute_search_files(
                token,
                arguments.get("query", "")
            )
        else:
            result = {"error": f"Unknown tool: {tool_name}"}
        
        print(f"execute_tool result tool={tool_name} keys={list(result.keys())}", file=sys.stderr)
        return json.dumps(result, indent=2)
        
    except Exception as e:
        print(f"execute_tool error tool={tool_name}: {e}", file=sys.stderr)
        return json.dumps({"error": str(e)})


# ============== Page Routes ==============

@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    """Root route - show login or redirect to dashboard."""
    if is_authenticated(request):
        return templates.TemplateResponse("dashboard.html", {"request": request})
    return templates.TemplateResponse("login.html", {"request": request})


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    """Login page."""
    if is_authenticated(request):
        return HTMLResponse(
            content='<script>window.location.href="/dashboard";</script>',
            status_code=200
        )
    return templates.TemplateResponse("login.html", {"request": request})


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request):
    """Dashboard - main hub after login."""
    if not is_authenticated(request):
        return HTMLResponse(
            content='<script>window.location.href="/.auth/login/aad?post_login_redirect_uri=/dashboard";</script>',
            status_code=200
        )
    return templates.TemplateResponse("dashboard.html", {"request": request})


@app.get("/chat", response_class=HTMLResponse)
async def chat_page(request: Request):
    """Chat interface with Joogni AI."""
    if not is_authenticated(request):
        return HTMLResponse(
            content='<script>window.location.href="/.auth/login/aad?post_login_redirect_uri=/chat";</script>',
            status_code=200
        )
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/calculators", response_class=HTMLResponse)
async def calculators_page(request: Request):
    """Family law calculators."""
    if not is_authenticated(request):
        return HTMLResponse(
            content='<script>window.location.href="/.auth/login/aad?post_login_redirect_uri=/calculators";</script>',
            status_code=200
        )
    return templates.TemplateResponse("calculators.html", {"request": request})


# ============== API Routes ==============

# In-memory storage for agreements (use database in production)
user_agreements = {}

@app.get("/api/user")
async def get_user(request: Request):
    """Get current user info."""
    user_info = get_user_info(request)
    if user_info:
        return JSONResponse(user_info)
    return JSONResponse({"authenticated": False})


@app.get("/api/user-info")
async def get_user_info_api(request: Request):
    """Get user info for display."""
    user_info = get_user_info(request)
    if user_info:
        return JSONResponse({
            "name": user_info.get("name", "User"),
            "email": user_info.get("email", ""),
            "authenticated": True
        })
    return JSONResponse({
        "name": "Guest",
        "email": "",
        "authenticated": False
    })


@app.get("/api/check-agreement")
async def check_agreement(request: Request):
    """Check if user has accepted the agreement."""
    user_info = get_user_info(request)
    user_id = user_info.get("email", "anonymous") if user_info else "anonymous"
    
    accepted = user_agreements.get(user_id, False)
    return JSONResponse({"accepted": accepted})


@app.post("/api/accept-agreement")
async def accept_agreement(request: Request):
    """Record user's acceptance of the agreement."""
    user_info = get_user_info(request)
    user_id = user_info.get("email", "anonymous") if user_info else "anonymous"
    
    user_agreements[user_id] = True
    return JSONResponse({"success": True, "message": "Agreement accepted"})


@app.get("/api/box/status")
async def box_status(request: Request):
    """Check Box integration status (placeholder)."""
    return JSONResponse({
        "connected": False,
        "message": "Box integration not configured"
    })

def normalize_to_text(x) -> str:
    if x is None:
        return ""
    if isinstance(x, str):
        return x
    if isinstance(x, bytes):
        return x.decode("utf-8", errors="ignore")
    if isinstance(x, list):
        # ex: [{"type":"text","text":"..."}, ...]
        parts = []
        for it in x:
            if isinstance(it, str):
                parts.append(it)
            elif isinstance(it, dict):
                # common patterns
                if "text" in it and isinstance(it["text"], str):
                    parts.append(it["text"])
                elif it.get("type") == "text" and isinstance(it.get("text"), str):
                    parts.append(it["text"])
        return "\n".join([p for p in parts if p])
    if isinstance(x, dict):
        # fallback: try to pick common fields
        if "text" in x and isinstance(x["text"], str):
            return x["text"]
        if "content" in x:
            return normalize_to_text(x["content"])
        return ""
    return str(x)



@app.post("/api/get-upload-url")
async def get_upload_url(request: Request):
    """Return a SAS URL for uploading a single blob (up to MAX_UPLOAD_SIZE_MB)."""
    if not is_authenticated(request):
        raise HTTPException(status_code=401, detail="Not authenticated")

    try:
        data = await request.json()
        filename = (data.get("filename") or "").strip()
        if not filename:
            raise HTTPException(status_code=400, detail="filename is required")

        container = os.getenv("UPLOAD_CONTAINER", "legal-docs-raw")
        blob_service = _get_blob_service_client()
        _ensure_container(blob_service, container)

        blob_name = _safe_blob_name(filename)
        upload_url, final_blob_name = _generate_sas_url(blob_service, container, blob_name)

        return JSONResponse({
            "upload_url": upload_url,
            "blob_name": final_blob_name,
            "max_size_mb": MAX_UPLOAD_SIZE_MB
        })
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/conversation")
@app.post("/api/agentic")
async def conversation(request: Request):
    """Handle chat conversation with Azure OpenAI - with function calling for M365."""
    import sys
    print("=== CONVERSATION ENDPOINT HIT ===", file=sys.stderr)
    try:
        data = await request.json()
        print(f"Full request data: {data}", file=sys.stderr)
        print(f"Data keys: {data.keys() if isinstance(data, dict) else 'not a dict'}", file=sys.stderr)
        
        # Try different possible message formats
        messages = data.get("messages", [])
        if not messages:
            messages = data.get("conversation", [])
        if not messages:
            messages = data.get("history", [])
        if not messages and "message" in data:
            # Single message format
            messages = [{"role": "user", "content": data.get("message", "")}]
        if not messages and "query" in data:
            messages = [{"role": "user", "content": data.get("query", "")}]
        if not messages and "prompt" in data:
            messages = [{"role": "user", "content": data.get("prompt", "")}]
            
        context = data.get("context", "")
        print(f"Messages after parsing: {len(messages)}", file=sys.stderr)
        print(f"Messages content: {messages}", file=sys.stderr)
        
        # Get Azure OpenAI credentials
        key = os.getenv("AZURE_OPENAI_KEY")
        raw_endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
        deployment = os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4.1")
        search_service = os.getenv("AZURE_SEARCH_SERVICE")
        search_index = os.getenv("AZURE_SEARCH_INDEX")
        search_key = os.getenv("AZURE_SEARCH_KEY")
        # Normalize: SDK needs base endpoint (no path). Version: prefer EMBEDDING, then API, then query, then default.
        endpoint = raw_endpoint
        version = os.getenv("AZURE_OPENAI_API_VERSION", "2023-05-15")
        
        print(f"Endpoint: {endpoint}", file=sys.stderr)
        print(f"Deployment: {deployment}", file=sys.stderr)
        print(f"Key present: {bool(key)}", file=sys.stderr)
        print(f"Search cfg present: service={bool(search_service)} index={bool(search_index)} key={bool(search_key)}", file=sys.stderr)
        
        if not key or not endpoint:
            raise HTTPException(status_code=500, detail="Azure OpenAI not configured")
        
        # Get Graph token for M365 access
        auth_headers = {
            "X-MS-CLIENT-PRINCIPAL": bool(request.headers.get("X-MS-CLIENT-PRINCIPAL")),
            "X-MS-TOKEN-AAD-ACCESS-TOKEN": bool(request.headers.get("X-MS-TOKEN-AAD-ACCESS-TOKEN")),
            "X-MS-TOKEN-AAD-ID-TOKEN": bool(request.headers.get("X-MS-TOKEN-AAD-ID-TOKEN")),
        }
        print(f"Auth headers presence: {auth_headers}", file=sys.stderr)
        graph_token = await get_valid_graph_token(request)
        print(f"Graph token available for tools: {bool(graph_token)}", file=sys.stderr)
        
        print("Creating AzureOpenAI client...", file=sys.stderr)
        client = AzureOpenAI(
            api_key=key,
            azure_endpoint=endpoint,
            api_version=version,
        )
        print("Client created, making request...", file=sys.stderr)
        
        # Build messages with system prompt
        chat_messages = [{"role": "system", "content": JOOGNI_SYSTEM_PROMPT}]
        
        last_user = ""
        for msg in reversed(messages):
            if msg.get("role") == "user":
                last_user = msg.get("content", "")
                break

        legal_mode = False
        context_chunks = []
        no_context = False
        attachment_contexts: List[str] = []
        if search_service and search_index and search_key and last_user:
            try:
                search_endpoint = f"https://{search_service}.search.windows.net"
                search_client = SearchClient(
                    search_endpoint, search_index, AzureKeyCredential(search_key)
                )

                # Candidate domains: from payload ("domains") or ENV (LEGAL_DOMAINS)
                request_domains = data.get("domains", [])
                if isinstance(request_domains, str):
                    request_domains = [d.strip() for d in request_domains.split(",") if d.strip()]
                elif not isinstance(request_domains, list):
                    request_domains = []

                candidates = request_domains or LEGAL_DOMAINS
                candidates = [d for d in candidates if d][:LEGAL_MAX_DOMAINS]

                last_user = normalize_to_text(last_user)

                print(f"Running multi-domain search for query='{last_user}' domains={candidates}", file=sys.stderr)
                context_chunks, domains_with_hits = run_multi_domain_search(
                    search_client=search_client,
                    query=last_user,
                    domains=candidates,
                    max_domains=LEGAL_MAX_DOMAINS,
                    top_per_domain=LEGAL_TOP_PER_DOMAIN,
                )

                legal_mode = len(context_chunks) > 0
                print(f"Legal mode via search hits: {legal_mode} (domains_with_hits={domains_with_hits} chunks={len(context_chunks)})", file=sys.stderr)
                if legal_mode:
                    ctx = "\n\n".join(context_chunks)
                    chat_messages.append({
                        "role": "user",
                        "content": (
                            "Use only the context below (tagged por DOMAIN). "
                            "Cite the sources. If context is insufficient, reply exactly: "
                            "'I don't have enough information to answer that question.'\n"
                            + ctx
                        )
                    })
                    print(f"Legal RAG applied with {len(context_chunks)} chunk(s)", file=sys.stderr)
                else:
                    no_context = True
                    print("Legal RAG skipped (no relevant search hits); instructing model to say no context", file=sys.stderr)
            except Exception as search_err:
                print(f"Legal RAG search error: {search_err}", file=sys.stderr)
        else:
            print("Legal RAG not applied (missing search config or empty question)", file=sys.stderr)

        # Attachments uploaded by user (PDF/TXT) for this request
        blobs = data.get("blobs", [])
        if blobs:
            try:
                attachment_contexts = await _build_attachment_context(blobs, last_user, client)
            except HTTPException:
                raise
            except Exception as attach_err:
                print(f"Attachment processing error: {attach_err}", file=sys.stderr)
                raise HTTPException(status_code=500, detail=f"Attachment processing failed: {attach_err}")

        if attachment_contexts:
            # We have user-provided context; do not force the model to say "no context"
            no_context = False
            chat_messages.append({
                "role": "user",
                "content": (
                    "Use ONLY the following attachment excerpts to answer. "
                    "Provide a separate section for EACH attachment, even if you just say it is not relevant. "
                    "Cite them as 'Attachment Source'.\n\n"
                    + "\n\n".join(attachment_contexts)
                )
            })
        
        # If no relevant context, instruct the model not to invent
        if no_context:
            chat_messages.append({
                "role": "user",
                "content": "There is no relevant context in the index for this legal question. Respond with 'I don't have enough information to answer that legal question. do not invent information.'. Do not use any other information or sources."
            })

        # Add context if provided
        if context:
            chat_messages.append({
                "role": "user", 
                "content": f"Context from uploaded documents/emails:\n{context}"
            })
            chat_messages.append({
                "role": "assistant",
                "content": "I've reviewed the provided context. How can I help you with this information?"
            })
        
        # Add conversation messages
        for msg in messages:
            chat_messages.append({
                "role": msg.get("role", "user"),
                "content": msg.get("content", "")
            })
        
        print(f"Total chat_messages: {len(chat_messages)}", file=sys.stderr)
        # lower temperature for legal questions to reduce creativity
        temperature = 0.2 if legal_mode else 0.7
        print(f"Making Azure OpenAI call with tools={bool(graph_token)} legal_mode={legal_mode} temp={temperature}...", file=sys.stderr)
        
        # First call - may request tool use
        try:
            response = client.chat.completions.create(
                model=deployment,
                messages=chat_messages,
                tools=TOOLS if graph_token else None,
                tool_choice="auto" if graph_token else None,
                temperature=temperature,
                max_tokens=2000
            )
            print(f"Azure OpenAI response received!", file=sys.stderr)
        except Exception as api_error:
            print(f"Azure OpenAI API Error: {type(api_error).__name__}: {api_error}", file=sys.stderr)
            raise
        
        response_message = response.choices[0].message
        
        # Check if the model wants to use tools
        if response_message.tool_calls and graph_token:
            # Add the assistant's response to messages
            chat_messages.append({
                "role": "assistant",
                "content": response_message.content,
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments
                        }
                    }
                    for tc in response_message.tool_calls
                ]
            })
            
            # Execute each tool call
            for tool_call in response_message.tool_calls:
                function_name = tool_call.function.name
                function_args = json.loads(tool_call.function.arguments)
                
                # Execute the tool
                tool_result = await execute_tool(function_name, function_args, graph_token, request)
                
                # Add tool result to messages
                chat_messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": tool_result
                })
            
            # Get final response after tool execution
            final_response = client.chat.completions.create(
                model=deployment,
                messages=chat_messages,
                temperature=0.7,
                max_tokens=2000
            )
            
            return JSONResponse({
                "job_id": str(uuid.uuid4()),
                "status": "completed",
                "response": final_response.choices[0].message.content,
                "response": final_response.choices[0].message.content,
                "tools_used": [tc.function.name for tc in response_message.tool_calls],
                "usage": {
                    "prompt_tokens": final_response.usage.prompt_tokens,
                    "completion_tokens": final_response.usage.completion_tokens
                }
            })
        
        # No tool calls - return direct response
        return JSONResponse({
            "job_id": str(uuid.uuid4()),
            "status": "completed",
            "response": response_message.content,
            "response": response_message.content,
            "usage": {
                "prompt_tokens": response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens
            }
        })
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/graph/health")
async def graph_health(request: Request):
    """
    Lightweight Graph diagnostic: checks /me and one messages fetch using the token from Easy Auth.
    """
    token = await get_valid_graph_token(request)
    if not token:
        return JSONResponse({"error": "Missing Graph token"}, status_code=401)
    
    results = {}
    async with httpx.AsyncClient(timeout=10.0) as client:
        for name, url in [
            ("me", "https://graph.microsoft.com/v1.0/me"),
            ("messages", "https://graph.microsoft.com/v1.0/me/messages?$top=1&$select=id,subject"),
        ]:
            try:
                resp = await client.get(url, headers={"Authorization": f"Bearer {token}"})
                try:
                    payload = resp.json()
                except Exception:
                    payload = resp.text[:500]
                results[name] = {
                    "status": resp.status_code,
                    "ok": resp.status_code < 300,
                    "payload_preview": payload if resp.status_code >= 300 else None,
                }
                print(f"[Graph health] {name} status={resp.status_code}", file=sys.stderr)
            except Exception as err:
                results[name] = {"status": None, "ok": False, "error": str(err)}
                print(f"[Graph health] {name} error: {err}", file=sys.stderr)
    
    return JSONResponse({"results": results})


@app.get("/api/check_status/{request_id}")
async def check_status(request_id: str):
    """Check status of a request - returns completed since we process synchronously."""
    return JSONResponse({
        "status": "completed",
        "request_id": request_id
    })


@app.post("/api/documents/analyze")
async def analyze_document(request: Request, file: UploadFile = File(...)):
    """Analyze uploaded document using Azure Document Intelligence."""
    try:
        from azure.ai.documentintelligence import DocumentIntelligenceClient
        from azure.core.credentials import AzureKeyCredential
        
        doc_endpoint = os.getenv("AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT")
        doc_key = os.getenv("AZURE_DOCUMENT_INTELLIGENCE_KEY")
        
        if not doc_endpoint or not doc_key:
            raise HTTPException(status_code=500, detail="Document Intelligence not configured")
        
        client = DocumentIntelligenceClient(
            endpoint=doc_endpoint,
            credential=AzureKeyCredential(doc_key)
        )
        
        content = await file.read()
        
        poller = client.begin_analyze_document(
            "prebuilt-layout",
            body=content,
            content_type=file.content_type
        )
        result = poller.result()
        
        text_content = ""
        for page in result.pages:
            for line in page.lines:
                text_content += line.content + "\n"
        
        return JSONResponse({
            "filename": file.filename,
            "content": text_content,
            "pages": len(result.pages)
        })
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ============== Direct Microsoft Graph API Routes (for manual panel) ==============

@app.get("/api/outlook/search")
async def search_emails_direct(request: Request, q: str = "", top: int = 20):
    """Search Outlook emails (direct API for M365 panel)."""
    try:
        token = await get_valid_graph_token(request)
        if not token:
            raise HTTPException(status_code=401, detail="No access token available")
        
        async with httpx.AsyncClient() as client:
            if q:
                url = f"https://graph.microsoft.com/v1.0/me/messages?$search=\"{q}\"&$top={top}&$select=id,subject,from,receivedDateTime,bodyPreview,hasAttachments"
            else:
                url = f"https://graph.microsoft.com/v1.0/me/messages?$top={top}&$orderby=receivedDateTime desc&$select=id,subject,from,receivedDateTime,bodyPreview,hasAttachments"
            
            response = await client.get(
                url,
                headers={"Authorization": f"Bearer {token}"}
            )
            
            if response.status_code != 200:
                raise HTTPException(status_code=response.status_code, detail=response.text)
            
            return JSONResponse(response.json())
            
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/outlook/message/{message_id}")
async def get_email_direct(request: Request, message_id: str):
    """Get full email content (direct API for M365 panel)."""
    try:
        token = await get_valid_graph_token(request)
        if not token:
            raise HTTPException(status_code=401, detail="No access token available")
        
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"https://graph.microsoft.com/v1.0/me/messages/{message_id}?$select=id,subject,from,toRecipients,receivedDateTime,body,hasAttachments",
                headers={"Authorization": f"Bearer {token}"}
            )
            
            if response.status_code != 200:
                raise HTTPException(status_code=response.status_code, detail=response.text)
            
            return JSONResponse(response.json())
            
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/calendar/search")
async def search_calendar_direct(request: Request, q: str = "", days: int = 30):
    """Search calendar events (direct API for M365 panel)."""
    try:
        token = await get_valid_graph_token(request)
        if not token:
            raise HTTPException(status_code=401, detail="No access token available")
        
        start = datetime.utcnow()
        end = start + timedelta(days=days)
        
        async with httpx.AsyncClient() as client:
            url = f"https://graph.microsoft.com/v1.0/me/calendarView?startDateTime={start.isoformat()}Z&endDateTime={end.isoformat()}Z&$select=id,subject,start,end,location,organizer&$orderby=start/dateTime"
            
            response = await client.get(
                url,
                headers={"Authorization": f"Bearer {token}"}
            )
            
            if response.status_code != 200:
                raise HTTPException(status_code=response.status_code, detail=response.text)
            
            data = response.json()
            
            if q:
                q_lower = q.lower()
                data["value"] = [
                    event for event in data.get("value", [])
                    if q_lower in event.get("subject", "").lower()
                    or q_lower in str(event.get("location", {}).get("displayName", "")).lower()
                ]
            
            return JSONResponse(data)
            
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/onedrive/search")
async def search_onedrive_direct(request: Request, q: str):
    """Search OneDrive files (direct API for M365 panel)."""
    try:
        token = await get_valid_graph_token(request)
        if not token:
            raise HTTPException(status_code=401, detail="No access token available")
        
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"https://graph.microsoft.com/v1.0/me/drive/root/search(q='{q}')?$select=id,name,webUrl,createdDateTime,size,file",
                headers={"Authorization": f"Bearer {token}"}
            )
            
            if response.status_code != 200:
                raise HTTPException(status_code=response.status_code, detail=response.text)
            
            return JSONResponse(response.json())
            
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ============== Health Check ==============

@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "healthy", "timestamp": datetime.utcnow().isoformat()}


@app.get("/api/test")
async def test_endpoint():
    """Test endpoint to verify API is working."""
    import sys
    print("=== TEST ENDPOINT HIT ===", file=sys.stderr)
    key = os.getenv("AZURE_OPENAI_KEY")
    endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
    deployment = os.getenv("AZURE_OPENAI_DEPLOYMENT")
    return {
        "status": "ok",
        "key_present": bool(key),
        "endpoint": endpoint,
        "deployment": deployment
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
