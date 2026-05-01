"""
Azure Blob Storage client for legal documents
Supports both Managed Identity (production) and Connection String (development)
"""

from datetime import datetime, timezone
from typing import List, Optional, Tuple

from azure.storage.blob import BlobServiceClient
from azure.identity import DefaultAzureCredential
from azure.core.exceptions import ResourceNotFoundError
import os
import logging

logger = logging.getLogger(__name__)

from dotenv import load_dotenv

# Always load .env for local development (connection string, etc.)
load_dotenv(override=False)


class LegalDocsStorage:
    """Manages legal documents (PDF/TXT) in Azure Blob Storage"""
    
    def __init__(self):
        """Initialize Blob Storage client with Managed Identity or Connection String"""
        
        # Try Managed Identity first (production), fallback to Connection String (dev)
        connection_string = os.getenv("AZURE_STORAGE_CONNECTION_STRING")
        # Default storage account name (can be overridden via AZURE_STORAGE_ACCOUNT_NAME)
        account_name = os.getenv("AZURE_STORAGE_ACCOUNT_NAME", "glgaistorage")
        
        if connection_string:
            logger.info("Using Connection String for Blob Storage (development mode)")
            self.blob_service = BlobServiceClient.from_connection_string(connection_string)
        else:
            logger.info("Using Managed Identity for Blob Storage (production mode)")
            credential = DefaultAzureCredential()
            account_url = f"https://{account_name}.blob.core.windows.net"
            self.blob_service = BlobServiceClient(account_url, credential=credential)
        
        self.raw_container = "legal-docs-raw"
        self.processed_container = "legal-docs-processed"
    
    def upload_bytes(self, data: bytes, blob_name: str, container: Optional[str] = None) -> bool:
        """
        Upload arbitrary bytes to a container (defaults to raw_container).

        Args:
            data: Content to upload.
            blob_name: Name to save in blob storage.
            container: Container name (defaults to raw_container).

        Returns:
            True if successful, False otherwise.
        """
        c = container or self.raw_container
        try:
            blob_client = self.blob_service.get_blob_client(
                container=c,
                blob=blob_name
            )

            blob_client.upload_blob(data, overwrite=True)

            logger.info(f"✅ Uploaded {blob_name} to {c}")
            return True

        except Exception as e:
            logger.error(f"❌ Failed to upload {blob_name}: {str(e)}")
            return False

    def upload_pdf(self, file_path: str, blob_name: str) -> bool:
        """
        Upload PDF to Blob Storage
        
        Args:
            file_path: Local path to PDF file
            blob_name: Name to save in blob storage
            
        Returns:
            True if successful, False otherwise
        """
        try:
            with open(file_path, "rb") as data:
                return self.upload_bytes(data.read(), blob_name)
        except Exception as e:
            logger.error(f"❌ Failed to upload {blob_name}: {str(e)}")
            return False

    def upload_text(self, content: str, blob_name: str, encoding: str = "utf-8", container: Optional[str] = None) -> bool:
        """
        Upload a text payload to a container (defaults to raw_container).

        Args:
            content: Text content to upload.
            blob_name: Name to save in blob storage.
            encoding: Text encoding (default utf-8).
            container: Container name (defaults to raw_container).

        Returns:
            True if successful, False otherwise.
        """
        try:
            data = content.encode(encoding)
            return self.upload_bytes(data, blob_name, container=container)
        except Exception as e:
            logger.error(f"❌ Failed to upload text {blob_name}: {str(e)}")
            return False
    
    def download_file(self, blob_name: str, container: Optional[str] = None) -> bytes:
        """
        Download file (PDF/TXT) from Blob Storage
        
        Args:
            blob_name: Name of blob to download
            container: Container name (defaults to raw_container).
                       Use same as UPLOAD_CONTAINER for chat attachments.
            
        Returns:
            File content as bytes
            
        Raises:
            ResourceNotFoundError: If blob doesn't exist
        """
        c = container or self.raw_container
        try:
            blob_client = self.blob_service.get_blob_client(
                container=c,
                blob=blob_name
            )
            
            file_bytes = blob_client.download_blob().readall()
            logger.info(f"✅ Downloaded {blob_name} ({len(file_bytes)} bytes)")
            return file_bytes
            
        except ResourceNotFoundError:
            logger.error(f"❌ Blob not found: {blob_name}")
            raise
        except Exception as e:
            logger.error(f"❌ Failed to download {blob_name}: {str(e)}")
            raise
    
    # Backward-compat
    def download_pdf(self, blob_name: str) -> bytes:
        return self.download_file(blob_name)

    def delete_file(self, blob_name: str, container: Optional[str] = None) -> bool:
        """
        Delete a blob from a container (defaults to raw_container).

        Args:
            blob_name: Name of blob to delete
            container: Container name (defaults to raw_container).

        Returns:
            True if deleted or not found (idempotent), False otherwise.
        """
        c = container or self.raw_container
        try:
            blob_client = self.blob_service.get_blob_client(
                container=c,
                blob=blob_name
            )
            blob_client.delete_blob(delete_snapshots="include")
            logger.info(f"✅ Deleted {blob_name} from {c}")
            return True
        except ResourceNotFoundError:
            logger.info(f"ℹ️ Blob already absent: {blob_name}")
            return True
        except Exception as e:
            logger.error(f"❌ Failed to delete {blob_name}: {str(e)}")
            return False
    
    def list_blobs_by_prefix(self, prefix: str, extensions: tuple = ('.json',)) -> list:
        """
        List blobs under a given prefix (e.g., california/courtlistener/).

        Args:
            prefix: Blob name prefix (e.g., "california/courtlistener").
            extensions: tuple of extensions to include (default: .json).

        Returns:
            List of blob names matching the prefix and extensions.
        """
        try:
            container_client = self.blob_service.get_container_client(self.raw_container)
            blobs = [
                blob.name
                for blob in container_client.list_blobs(name_starts_with=prefix)
                if blob.name.lower().endswith(tuple(ext.lower() for ext in extensions))
            ]
            logger.info(f"✅ Found {len(blobs)} blobs under {prefix} ({extensions})")
            return blobs
        except Exception as e:
            logger.error(f"❌ Failed to list blobs by prefix {prefix}: {str(e)}")
            return []

    def list_blobs_with_timestamps(
        self,
        prefix: str,
        extensions: tuple = ('.pdf', '.txt', '.json'),
    ) -> List[Tuple[str, datetime]]:
        """
        List blobs under a prefix with their last_modified timestamps.

        Args:
            prefix: Blob name prefix (e.g. "u_" for user uploads).
            extensions: Tuple of extensions to include.

        Returns:
            List of (blob_name, last_modified) tuples.
        """
        try:
            container_client = self.blob_service.get_container_client(self.raw_container)
            result = []
            for blob in container_client.list_blobs(name_starts_with=prefix):
                if blob.name.lower().endswith(tuple(ext.lower() for ext in extensions)):
                    # last_modified can be None for some blob types; default to epoch
                    lm = blob.last_modified or datetime(1970, 1, 1, tzinfo=timezone.utc)
                    if lm.tzinfo is None:
                        lm = lm.replace(tzinfo=timezone.utc)
                    result.append((blob.name, lm))
            logger.info(f"✅ Found {len(result)} blobs under {prefix}")
            return result
        except Exception as e:
            logger.error(f"❌ Failed to list blobs with timestamps: {str(e)}")
            return []

    def list_documents(self, extensions: tuple = ('.pdf', '.txt')) -> list:
        """
        List documents in the raw container by extension
        
        Args:
            extensions: tuple of extensions to include (default: pdf, txt)
        
        Returns:
            List of blob names matching the extensions
        """
        try:
            container_client = self.blob_service.get_container_client(self.raw_container)
            blobs = [
                blob.name
                for blob in container_client.list_blobs()
                if blob.name.lower().endswith(tuple(ext.lower() for ext in extensions))
            ]
            logger.info(f"✅ Found {len(blobs)} docs ({extensions}) in {self.raw_container}")
            return blobs
            
        except Exception as e:
            logger.error(f"❌ Failed to list documents: {str(e)}")
            return []
    
    def list_pdfs(self) -> list:
        """Backward-compat: list only PDFs."""
        return self.list_documents(extensions=('.pdf',))
    
    def upload_processed(self, filename: str, content: str) -> bool:
        """
        Save processed chunks as backup in JSON format
        
        Args:
            filename: Name of JSON file (e.g., 'family_code_chunks.json')
            content: JSON string content
            
        Returns:
            True if successful, False otherwise
        """
        try:
            blob_client = self.blob_service.get_blob_client(
                container=self.processed_container,
                blob=filename
            )
            
            blob_client.upload_blob(content, overwrite=True)
            logger.info(f"✅ Uploaded processed data to {self.processed_container}/{filename}")
            return True
            
        except Exception as e:
            logger.error(f"❌ Failed to upload processed data: {str(e)}")
            return False
    
    def download_processed(self, filename: str) -> str:
        """
        Download processed chunks JSON
        
        Args:
            filename: Name of JSON file
            
        Returns:
            JSON string content
        """
        try:
            blob_client = self.blob_service.get_blob_client(
                container=self.processed_container,
                blob=filename
            )
            
            content = blob_client.download_blob().readall().decode('utf-8')
            logger.info(f"✅ Downloaded processed data from {filename}")
            return content
            
        except Exception as e:
            logger.error(f"❌ Failed to download processed data: {str(e)}")
            raise
    
    def blob_exists(self, blob_name: str, container: str = None) -> bool:
        """
        Check if a blob exists
        
        Args:
            blob_name: Name of blob to check
            container: Container name (defaults to raw_container)
            
        Returns:
            True if blob exists, False otherwise
        """
        try:
            container = container or self.raw_container
            blob_client = self.blob_service.get_blob_client(
                container=container,
                blob=blob_name
            )
            return blob_client.exists()
        except Exception:
            return False

