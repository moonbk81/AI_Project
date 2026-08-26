"""Diagnostic report queries and generation.

The UI tabs used to own the report prompt text and drive the
"fetch health KPI -> build prompt -> engine.ask()" sequence themselves. Keeping
it here means the prompt ships with the backend that answers it, and the UI is
left with a single call whose result it only has to render.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from core.config import PROMPTS, SATELLITE_PROMPTS

_SESSION_REPORT_KEY = "session_diagnostic_report"


def build_session_report_query(health_kpi_json: str) -> str:
    """Query for the dashboard's current-session diagnostic report."""
    template = PROMPTS.get(_SESSION_REPORT_KEY)
    if not template:
        raise ValueError(f"Missing prompt template: prompts.{_SESSION_REPORT_KEY}")
    return template.format(health_kpi_json=health_kpi_json)


def build_satellite_report_query(sat_type: str, health_kpi_json: str) -> str:
    """Query for a satellite (NTN) report.

    Raises on an unknown constellation instead of the previous behavior of
    sending the literal string "Prompt template not found." to the LLM, which
    produced a confident-looking report built on nothing.
    """
    template = SATELLITE_PROMPTS.get(sat_type)
    if not template:
        raise ValueError(f"Missing prompt template: satellite_prompts.{sat_type}")
    return template.format(health_kpi_json=health_kpi_json)


def _health_kpi(base_name: str) -> str:
    from agent_toolkit.kpi_tools import get_device_health_kpi

    return get_device_health_kpi(base_name)


def _ask(engine, query: str, current_file: Optional[str]) -> Dict[str, Any]:
    """Normalize engine.ask() into a dict.

    ask() returns a 4-tuple, but every caller of this used to re-derive that
    shape with its own isinstance/length checks.
    """
    result = engine.ask(query, current_file=current_file)
    if not isinstance(result, (tuple, list)):
        return {"answer": result, "ids": [], "metas": [], "thinking": ""}

    padded: List[Any] = list(result) + [None] * (4 - len(result))
    answer, ids, metas, thinking = padded[:4]
    return {
        "answer": answer or "",
        "ids": ids or [],
        "metas": metas or [],
        "thinking": thinking or "",
    }


def generate_session_report(
    engine,
    base_name: str,
    current_file: Optional[str] = None,
) -> Dict[str, Any]:
    """Current-session diagnostic report for an analyzed log."""
    query = build_session_report_query(_health_kpi(base_name))
    return _ask(engine, query, current_file)


def generate_satellite_report(
    engine,
    base_name: str,
    sat_type: str,
    current_file: Optional[str] = None,
) -> Dict[str, Any]:
    """Satellite (NTN) report for an analyzed log."""
    query = build_satellite_report_query(sat_type, _health_kpi(base_name))
    report = _ask(engine, query, current_file)

    # The gateway returns literal backslash-n in this prompt's answers; the
    # satellite tab used to unescape them right before st.info().
    answer = report["answer"]
    if isinstance(answer, str):
        report["answer"] = answer.replace("\\n", "\n")

    return report
