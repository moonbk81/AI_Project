"""Device state payload builders."""

from rag_builders.common import append_callback_payload


def build_device_payloads(
    report_data,
    build_markdown_doc,
    extract_metadata,
):
    rag_payload = []

    def add_payload(item, log_type):
        append_callback_payload(
            rag_payload,
            item,
            log_type,
            build_markdown_doc,
            extract_metadata,
        )

    if "boot_stats" in report_data:
        for boot_stat in report_data["boot_stats"]:
            add_payload(boot_stat, "Boot_Stat")

    if "signal_level_history" in report_data:
        for sig in report_data["signal_level_history"]:
            add_payload(sig, "Signal_Level")

    if "nitz_history" in report_data:
        for nitz in report_data["nitz_history"]:
            add_payload(nitz, "Nitz_Time_Event")

    if "system_properties" in report_data:
        props = report_data["system_properties"]

        if isinstance(props, list):
            for prop in props:
                add_payload(prop, "System_Property")
        elif isinstance(props, dict):
            add_payload(props, "System_Property")

    return rag_payload