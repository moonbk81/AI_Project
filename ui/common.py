import os
import json
import ast
import datetime
import pandas as pd

def parse_raw_logs(raw_data):
    """
    원본 로그 데이터를 리스트 형태로 정리합니다.
    """
    if isinstance(raw_data, list):
        raw_logs = raw_data
    elif isinstance(raw_data, str):
        raw_data_clean = raw_data.strip()
        try:
            raw_logs = json.loads(raw_data_clean)
            if not isinstance(raw_logs, list):
                raw_logs = [raw_data_clean]
        except Exception:
            try:
                raw_logs = ast.literal_eval(raw_data_clean)
                if not isinstance(raw_logs, list):
                    raw_logs = [raw_data_clean]
            except Exception:
                if raw_data_clean.startswith('[') and raw_data_clean.endswith(']'):
                    inner_text = raw_data_clean[1:-1]
                    if '", "' in inner_text:
                        raw_logs = inner_text.split('", "')
                    elif "', '" in inner_text:
                        raw_logs = inner_text.split("', '")
                    else:
                        raw_logs = [inner_text]
                    raw_logs = [log.strip(' "\'') for log in raw_logs]
                else:
                    clean_text = raw_data_clean.replace('\\n', '\n').replace('\\r', '')
                    raw_logs = clean_text.split('\n')
    else:
        raw_logs = []

    return [log for log in raw_logs if str(log).strip()]

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
