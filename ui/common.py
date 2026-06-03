import os
import json
import datetime
import pandas as pd

def _load_json(path, default):
    if not os.path.exists(path):
        return default
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def _safe_time_series(df, time_col="time"):
    if df.empty or time_col not in df.columns:
        return df

    current_year = datetime.datetime.now().year

    def parse_time(value):
        value = str(value).strip()
        if len(value) > 5 and value[2] == "-" and value.count("-") == 1:
            value = f"{current_year}-{value}"
        return pd.to_datetime(value, errors="coerce")

    df = df.copy()
    df["time_dt"] = df[time_col].apply(parse_time)
    return df.dropna(subset=["time_dt"]).sort_values("time_dt")
