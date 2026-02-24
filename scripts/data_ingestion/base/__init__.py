"""Base module for data ingestion: schema, config, and pipeline utilities."""

from scripts.data_ingestion.base.document_schema import IngestibleDocument, make_document_id
from scripts.data_ingestion.base.state_config import StateConfig, get_state_config

__all__ = ["IngestibleDocument", "make_document_id", "StateConfig", "get_state_config"]
