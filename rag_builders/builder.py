"""Top-level RAG payload builder orchestrator."""

from typing import Callable, Optional

from rag_builders.battery_builder import build_battery_payloads
from rag_builders.binder_builder import build_binder_context_payloads, build_binder_payloads
from rag_builders.crash_builder import build_crash_payloads
from rag_builders.device_builder import build_device_payloads
from rag_builders.network_builder import build_network_payloads
from rag_builders.rtos_builder import build_rtos_payloads
from rag_builders.telephony_builder import build_telephony_payloads

ProgressCallback = Optional[Callable[[int, int, str], None]]


def build_all_payloads(
    report_data,
    input_file,
    build_markdown_doc,
    extract_metadata,
    progress_callback: ProgressCallback = None,
):
    rag_payload = []
    if report_data.get("log_domain") == "rtos":
        # RTOS 리포트에는 Android 파서 결과가 없다. Android 빌더를 돌려봐야 빈 문서뿐이다.
        rag_payload.extend(build_rtos_payloads(report_data, input_file))
        if progress_callback:
            progress_callback(1, 1, "rtos")
        return rag_payload

    steps = [
        (
            "telephony",
            lambda: build_telephony_payloads(
                report_data,
                input_file,
                build_markdown_doc,
                extract_metadata,
            ),
        ),
        (
            "network",
            lambda: build_network_payloads(
                report_data,
                input_file,
                build_markdown_doc,
                extract_metadata,
            ),
        ),
        (
            "crash",
            lambda: build_crash_payloads(
                report_data,
                build_markdown_doc,
                extract_metadata,
            ),
        ),
        ("battery", lambda: build_battery_payloads(report_data, input_file)),
        (
            "device",
            lambda: build_device_payloads(
                report_data,
                build_markdown_doc,
                extract_metadata,
            ),
        ),
    ]
    if "binder_warnings" in report_data:
        steps.append(("binder", lambda: build_binder_payloads(report_data, input_file)))
    if "binder_context_summary" in report_data:
        steps.append(("binder context", lambda: build_binder_context_payloads(report_data, input_file)))

    total = len(steps)
    for index, (label, build) in enumerate(steps, start=1):
        rag_payload.extend(build())
        if progress_callback:
            progress_callback(index, total, label)

    return rag_payload
