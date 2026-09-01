"""Current-session KPI summary derived from Chroma metadata.

This used to be computed inside the dashboard tab's render function, which meant
the four headline numbers were defined by render code and could not be
checked without a browser.
"""

from __future__ import annotations

from typing import Any, Dict, List

import pandas as pd

_OOS_PATTERN = "OUT_OF_SERVICE|OOS|POWER_OFF"
_CALL_FAIL_PATTERN = "FAIL|DROP"
_UNKNOWN = "N/A"


def _slice(df: pd.DataFrame, log_type: str) -> pd.DataFrame:
    if df.empty or "log_type" not in df.columns:
        return df.iloc[0:0]
    return df[df["log_type"] == log_type]


def _top_data_usage_app(df: pd.DataFrame) -> Dict[str, Any]:
    du_df = _slice(df, "Data_Usage")
    if du_df.empty or "total_mb" not in du_df.columns:
        return {"top_app_name": "N/A", "top_app_mb": 0.0}

    du_df = du_df.copy()
    du_df["total_mb"] = pd.to_numeric(du_df["total_mb"], errors="coerce")
    top = du_df.sort_values(by="total_mb", ascending=False).iloc[0]
    total_mb = top["total_mb"]
    return {
        "top_app_name": top.get("app_name") or "Unknown",
        "top_app_mb": 0.0 if pd.isna(total_mb) else float(total_mb),
    }


def _call_success(df: pd.DataFrame) -> Dict[str, Any]:
    call_df = _slice(df, "Call_Session")
    if call_df.empty:
        return {"call_success_rate": 100.0, "call_drop_count": 0}

    total_calls = len(call_df)
    if "status" in call_df.columns:
        failed = call_df["status"].astype(str).str.contains(
            _CALL_FAIL_PATTERN, na=False, case=False
        )
        drop_count = int(failed.sum())
    else:
        drop_count = 0

    success_rate = round(((total_calls - drop_count) / total_calls) * 100, 1)
    return {"call_success_rate": success_rate, "call_drop_count": drop_count}


def _oos_count(df: pd.DataFrame) -> int:
    oos_df = _slice(df, "OOS_Event")
    if oos_df.empty:
        return 0

    out_of_service = pd.Series(False, index=oos_df.index)
    for column in ("voice_reg", "data_reg"):
        if column in oos_df.columns:
            out_of_service |= oos_df[column].astype(str).str.contains(
                _OOS_PATTERN, na=False, case=False
            )

    return int(out_of_service.sum())


def _avg_signal(df: pd.DataFrame) -> float:
    sig_df = _slice(df, "Signal_Level")
    if sig_df.empty or "level" not in sig_df.columns:
        return 0.0

    mean = pd.to_numeric(sig_df["level"], errors="coerce").mean()
    return 0.0 if pd.isna(mean) else float(mean)


def _value(value: Any, default: str = _UNKNOWN) -> str:
    if value is None or pd.isna(value):
        return default
    text = str(value).strip()
    return text if text and text.lower() not in {"unknown", "none", "nan"} else default


def _first_value(df: pd.DataFrame, *columns: str, default: str = _UNKNOWN) -> str:
    for column in columns:
        if column not in df.columns:
            continue
        for value in df[column]:
            clean = _value(value, "")
            if clean:
                return clean
    return default


def _split_slot_values(value: Any) -> List[str]:
    clean = _value(value, "")
    if not clean:
        return []
    return [part.strip() or _UNKNOWN for part in clean.split(",")]


def _mcc_mnc(numeric: str) -> Dict[str, str]:
    numeric = _value(numeric, "")
    if len(numeric) < 5 or not numeric.isdigit():
        return {"mcc": _UNKNOWN, "mnc": _UNKNOWN, "mcc_mnc": _UNKNOWN}
    return {"mcc": numeric[:3], "mnc": numeric[3:], "mcc_mnc": numeric}


def _system_properties(df: pd.DataFrame) -> Dict[str, str]:
    props: Dict[str, str] = {}
    for _, row in _slice(df, "System_Property").iterrows():
        for key, value in row.items():
            if isinstance(key, str) and key.startswith(("gsm.", "ril.", "persist.radio.")):
                clean = _value(value, "")
                if clean:
                    props[key] = clean
    return props


def _prop_slots(props: Dict[str, str], key: str) -> List[str]:
    """Slot-indexed values for one property.

    Android joins the per-SIM values into a single property, comma separated and
    positional: `gsm.sim.state = "LOADED,ABSENT"`. A single-SIM dump has no comma
    and yields one entry; an empty slot yields "N/A".
    """
    return _split_slot_values(props.get(key))


def _slot_value(values: List[str], index: int) -> str:
    return _value(values[index], "") if index < len(values) else _UNKNOWN


def _sim_slots(props: Dict[str, str]) -> List[Dict[str, str]]:
    states = _prop_slots(props, "gsm.sim.state")
    operators = _prop_slots(props, "gsm.sim.operator.numeric")
    carriers = _prop_slots(props, "gsm.sim.operator.alpha")
    count = max(len(states), len(operators), len(carriers), 0)

    slots = []
    for index in range(count):
        numeric = _slot_value(operators, index)
        slots.append({
            "slot": str(index),
            "state": _slot_value(states, index),
            "carrier": _slot_value(carriers, index),
            **_mcc_mnc(numeric),
        })
    return slots


def _network_slots(df: pd.DataFrame, props: Dict[str, str]) -> List[Dict[str, str]]:
    by_slot: Dict[str, Dict[str, str]] = {}
    oos_df = _slice(df, "OOS_Event")
    if not oos_df.empty:
        for _, row in oos_df.iterrows():
            slot = _value(row.get("slotId", row.get("slot", "0")), "0")
            by_slot[slot] = {
                "slot": slot,
                "voice_reg": _value(row.get("voice_reg")),
                "data_reg": _value(row.get("data_reg")),
                "rat": _value(row.get("rat")),
                "operator": _value(row.get("operator")),
            }

    network_numbers = _prop_slots(props, "gsm.operator.numeric")
    network_names = _prop_slots(props, "gsm.operator.alpha")
    for index in range(max(len(network_numbers), len(network_names))):
        slot = str(index)
        current = by_slot.setdefault(slot, {"slot": slot})
        numeric = _slot_value(network_numbers, index)
        current.update({
            **_mcc_mnc(numeric),
            "plmn": numeric,
            "network_name": _slot_value(network_names, index),
            "voice_reg": current.get("voice_reg", _UNKNOWN),
            "data_reg": current.get("data_reg", _UNKNOWN),
            "rat": current.get("rat", _UNKNOWN),
            "operator": current.get("operator", _UNKNOWN),
        })

    return [by_slot[key] for key in sorted(by_slot.keys())]


def _mobile_data(df: pd.DataFrame) -> str:
    """모바일 데이터 사용 설정. 로그의 0/1 을 읽는 말로 바꾼다.

    `_system_properties` 는 통신 프로퍼티 접두사만 통과시키므로 이 설정은
    거기서 오지 않는다. 적재 행에서 직접 읽는다.
    """
    raw = _first_value(_slice(df, "System_Property"), "mobile_data", default="")
    return {"1": "사용", "0": "사용 안 함"}.get(raw, _UNKNOWN)


def _device_context(df: pd.DataFrame) -> Dict[str, Any]:
    build_df = _slice(df, "Build_Info")
    props = _system_properties(df)
    return {
        "model_name": _first_value(build_df, "model_name"),
        "build_id": _first_value(build_df, "build_id", "build_fingerprint"),
        "radio": _first_value(build_df, "radio"),
        "network": _first_value(build_df, "network"),
        "mobile_data": _mobile_data(df),
        "sim_slots": _sim_slots(props),
        "network_slots": _network_slots(df, props),
    }


def compute_session_kpi(metadatas: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Headline device-state numbers for one analyzed session.

    Accepts raw Chroma metadata rows so the caller does not need pandas.
    """
    rows = [m for m in (metadatas or []) if m]
    df = pd.DataFrame(rows) if rows else pd.DataFrame()

    return {
        **_top_data_usage_app(df),
        **_call_success(df),
        "oos_count": _oos_count(df),
        "avg_signal_level": _avg_signal(df),
        "device_context": _device_context(df),
    }
