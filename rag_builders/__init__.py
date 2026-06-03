

"""RAG payload builder modules.

Purpose:
    report.json -> RAG documents -> payload.json

`prepare_rag_payload.py` uses `build_all_payloads()` as the top-level
orchestrator, while each domain-specific builder can still be imported
individually when needed.
"""

from rag_builders.builder import build_all_payloads
from rag_builders.battery_builder import build_battery_payloads
from rag_builders.binder_builder import build_binder_context_payloads, build_binder_payloads
from rag_builders.crash_builder import build_crash_payloads
from rag_builders.device_builder import build_device_payloads
from rag_builders.network_builder import build_network_payloads
from rag_builders.telephony_builder import build_telephony_payloads

__all__ = [
    "build_all_payloads",
    "build_battery_payloads",
    "build_binder_context_payloads",
    "build_binder_payloads",
    "build_crash_payloads",
    "build_device_payloads",
    "build_network_payloads",
    "build_telephony_payloads",
]