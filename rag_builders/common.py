
"""Common helpers for RAG payload builder modules."""

import os

def source_file_name(input_file):
    return os.path.basename(input_file)

def make_payload(document, metadata):
    return {
        "document": document,
        "metadata": metadata,
    }

def append_payload(rag_payload, document, metadata):
    rag_payload.append(make_payload(document, metadata))

def append_callback_payload(
    rag_payload,
    item,
    log_type,
    build_markdown_doc,
    extract_metadata,
):
    append_payload(
        rag_payload,
        build_markdown_doc(item, log_type),
        extract_metadata(item, log_type),
    )

def build_callback_payloads(
    items,
    log_type,
    build_markdown_doc,
    extract_metadata,
):
    rag_payload = []

    for item in items or []:
        append_callback_payload(
            rag_payload,
            item,
            log_type,
            build_markdown_doc,
            extract_metadata,
        )

    return rag_payload