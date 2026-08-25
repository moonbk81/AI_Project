"""Helpers shared by the chart builders."""

from __future__ import annotations

import datetime
from typing import Any, Optional

import pandas as pd


def log_year(year: Optional[int] = None) -> int:
    """Log timestamps carry no year; callers pass one to stay deterministic."""
    return year if year is not None else datetime.datetime.now().year


def has_columns(df: Any) -> bool:
    return df is not None and hasattr(df, "columns")


def parse_log_times(values: pd.Series, *, year: Optional[int] = None) -> pd.Series:
    """Parse a timestamp column that mixes log time and full datetimes.

    Parser output is `"MM-DD HH:MM:SS"`, while anything that came back through
    a report already carries a year. Only the former gets one prefixed.
    """
    current_year = log_year(year)

    def parse(value: Any) -> pd.Timestamp:
        text = str(value).strip()
        if len(text) > 5 and text[2] == "-" and text.count("-") == 1:
            text = f"{current_year}-{text}"
        return pd.to_datetime(text, errors="coerce")

    return values.apply(parse)


def with_parsed_times(df: pd.DataFrame, time_col: str = "time", *, year: Optional[int] = None) -> pd.DataFrame:
    """Add `time_dt`, drop rows that could not be parsed and order by time."""
    if df.empty or time_col not in df.columns:
        return df

    df = df.copy()
    df["time_dt"] = parse_log_times(df[time_col], year=year)
    return df.dropna(subset=["time_dt"]).sort_values("time_dt")
