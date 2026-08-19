"""Small helpers for the Streamlit UI layer."""

import hashlib

# Moved to core.chroma_helpers so the backend need not import from app/.
# Re-exported here for callers that still import it from app.helpers.
from core.chroma_helpers import get_collection_metadatas_batched

__all__ = ["generate_unique_key", "get_collection_metadatas_batched"]


def generate_unique_key(prefix, data_string):
    hash_obj = hashlib.md5(data_string.encode('utf-8')).hexdigest()[:8]
    return f"{prefix}_{hash_obj}"
