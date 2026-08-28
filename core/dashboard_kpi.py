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
    }
