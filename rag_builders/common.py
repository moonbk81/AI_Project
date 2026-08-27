
"""Common helpers for RAG payload builder modules."""

import os

from rag.log_type_aliases import with_alias_header

def source_file_name(input_file):
    return os.path.basename(input_file)

def make_payload(document, metadata):
    # 모든 빌더의 문서가 이 함수를 지나간다. 여기서 한글 증상 어휘를 붙여야
    # 유형별로 표현이 갈리지 않는다(키-값 덤프 유형 vs 한글 서술문 유형).
    log_type = (metadata or {}).get("log_type")
    return {
        "document": with_alias_header(document, log_type),
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