"""
Unified JSON schema for blob storage and indexing pipeline.

Each document follows the format:
{
  "id": "cal_fam_family_code",
  "content": "text content...",
  "metadata": { "domain", "source", "jurisdiction", "doc_type", "state", ... }
}
"""

from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass
class IngestibleDocument:
    """
    Normalized document schema for blob upload and indexing.

    Attributes:
        id: Unique document identifier (state-prefixed per naming convention).
        content: Text content for chunking and search.
        metadata: Domain, source, jurisdiction, doc_type, and source-specific fields.
    """

    id: str
    content: str
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dict for JSON serialization."""
        return {"id": self.id, "content": self.content, "metadata": self.metadata}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "IngestibleDocument":
        """Build from dict (e.g., parsed JSON)."""
        return cls(
            id=data.get("id", ""),
            content=data.get("content", ""),
            metadata=data.get("metadata", {}),
        )

    def to_json_str(self) -> str:
        """Serialize to JSON string."""
        import json

        return json.dumps(self.to_dict(), ensure_ascii=False)


def make_document_id(state_code: str, source: str, unique_part: str) -> str:
    """
    Build document ID per naming convention: {state_code}_{source}_{unique_part}.

    Args:
        state_code: 2-3 letter state abbreviation (e.g., cal, tx).
        source: Source type (e.g., fam, crc, court_forms, courtlistener).
        unique_part: Document-specific identifier.

    Returns:
        Globally unique document ID.
    """
    safe_unique = str(unique_part).replace(" ", "_").lower()
    return f"{state_code}_{source}_{safe_unique}"
