"""Chart data layer.

Every chart drawn by `ui/*.py` gets its series built here instead of inside the
Streamlit render function. A builder answers two questions the UI must not:

* *what should be drawn* — aggregation, state mapping, transition compression,
  time-series ordering; and
* *whether there is anything to draw* — reported as an explicit status the UI
  translates into `st.info` / `st.success` / silence.

The UI keeps only plotly styling: colors, labels, heights, hover templates.

Builders are pure functions, so `ui/*.py` imports them directly rather than
going through an HTTP endpoint (see `plm/service.py` for the same rule): a
Streamlit rerun must not pay a round trip to redraw a chart.
"""

from .telephony import (
    RILJ_SLOW_THRESHOLD_MS,
    SERVICE_STATE_ORDER,
    UTC_GEO_MAP,
    CallHistorySummary,
    CallSpan,
    DataCallKpi,
    DataCallSummary,
    NitzGeoPoint,
    NitzKpi,
    NitzOffsetPoint,
    NitzTimeline,
    RfCallTimeline,
    RiljKpi,
    RiljOverview,
    RsrpPoint,
    ServiceStatePoint,
    ServiceStateSeries,
    SignalLevelPoint,
    SignalLevelSeries,
    SipErrorMarker,
    SipFlow,
    SipFlowKpi,
    SipMessage,
    build_call_history_summary,
    build_data_call_summary,
    build_nitz_timeline,
    build_rf_call_timeline,
    build_rilj_overview,
    build_service_state_series,
    build_signal_level_series,
    build_sip_flow,
    map_registration_state,
)

__all__ = [
    "RILJ_SLOW_THRESHOLD_MS",
    "SERVICE_STATE_ORDER",
    "UTC_GEO_MAP",
    "CallHistorySummary",
    "CallSpan",
    "DataCallKpi",
    "DataCallSummary",
    "NitzGeoPoint",
    "NitzKpi",
    "NitzOffsetPoint",
    "NitzTimeline",
    "RfCallTimeline",
    "RiljKpi",
    "RiljOverview",
    "RsrpPoint",
    "ServiceStatePoint",
    "ServiceStateSeries",
    "SignalLevelPoint",
    "SignalLevelSeries",
    "SipErrorMarker",
    "SipFlow",
    "SipFlowKpi",
    "SipMessage",
    "build_call_history_summary",
    "build_data_call_summary",
    "build_nitz_timeline",
    "build_rf_call_timeline",
    "build_rilj_overview",
    "build_service_state_series",
    "build_signal_level_series",
    "build_sip_flow",
    "map_registration_state",
]
