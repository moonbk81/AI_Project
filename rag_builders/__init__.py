
"""RAG payload builder modules.

Purpose:
    report.json -> RAG documents -> payload.json

Recommended module layout:
    binder_builder.py
    crash_builder.py
    telephony_builder.py
    network_builder.py
    battery_builder.py
    device_builder.py

prepare_rag_payload.py should gradually become a thin orchestrator
that imports builder functions from this package.
"""