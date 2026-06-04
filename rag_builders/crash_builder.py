"""Crash / ANR payload builders."""

from rag_builders.common import append_callback_payload

def build_crash_payloads(report_data, build_markdown_doc, extract_metadata):
    rag_payload = []

    def add_payload(item, log_type):
        append_callback_payload(
            rag_payload,
            item,
            log_type,
            build_markdown_doc,
            extract_metadata,
        )

    if report_data.get("anr_context"):
        anr_data = report_data["anr_context"]
        if isinstance(anr_data, dict):
            anr_data = [anr_data]
        for anr_item in anr_data:
            add_payload(anr_item, "ANR_Context")

    crashes = report_data.get("crash_context")
    if crashes:
        for crash in crashes:
            add_payload(crash, "Crash_Event")

    if report_data.get("native_crash_context"):
        for native_crash in report_data["native_crash_context"]:
            stack_str = "\n".join([
                f"#{c['frame_level']} {c['library']} ({c['function']})"
                for c in native_crash.get('callstack', [])
            ])
            native_crash['raw_stack'] = stack_str
            add_payload(native_crash, "Native_Crash_Event")

    return rag_payload