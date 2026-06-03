"""Top-level RAG payload builder orchestrator."""

from rag_builders.battery_builder import build_battery_payloads
from rag_builders.binder_builder import build_binder_context_payloads, build_binder_payloads
from rag_builders.crash_builder import build_crash_payloads
from rag_builders.device_builder import build_device_payloads
from rag_builders.network_builder import build_network_payloads
from rag_builders.telephony_builder import build_telephony_payloads


def build_all_payloads(
    report_data,
    input_file,
    build_markdown_doc,
    extract_metadata,
):
    rag_payload = []

    rag_payload.extend(
        build_telephony_payloads(
            report_data,
            input_file,
            build_markdown_doc,
            extract_metadata,
        )
    )

    rag_payload.extend(
        build_network_payloads(
            report_data,
            input_file,
            build_markdown_doc,
            extract_metadata,
        )
    )

    rag_payload.extend(
        build_crash_payloads(
            report_data,
            build_markdown_doc,
            extract_metadata,
        )
    )

    rag_payload.extend(
        build_battery_payloads(
            report_data,
            input_file,
        )
    )

    rag_payload.extend(
        build_device_payloads(
            report_data,
            build_markdown_doc,
            extract_metadata,
        )
    )

    if "binder_warnings" in report_data:
        rag_payload.extend(build_binder_payloads(report_data, input_file))

    if "binder_context_summary" in report_data:
        rag_payload.extend(build_binder_context_payloads(report_data, input_file))

    return rag_payload
