
"""Common helpers shared by agent toolkit modules."""

from __future__ import annotations

import json
import os
from datetime import datetime
from functools import lru_cache

def _load_report_json(base_name: str, result_dir: str = "./result") -> dict:
    """분석된 통합 리포트 파일을 안전하게 로드합니다."""
    report_path = os.path.join(result_dir, f"{base_name}_report.json")

    if not os.path.exists(report_path):
        return {}

    with open(report_path, "r", encoding="utf-8") as f:
        return json.load(f)

def _ensure_dict(value):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return {}

    return value if isinstance(value, dict) else {}


# Helpers for Android log time parsing and RF correlation
@lru_cache(maxsize=2048)
def _parse_android_time(time_str: str):
    if not time_str:
        return None

    s = str(time_str).strip()
    current_year = datetime.now().year

    for fmt in [
        "%Y-%m-%d %H:%M:%S.%f",
        "%Y-%m-%d %H:%M:%S",
        "%m-%d %H:%M:%S.%f",
        "%m-%d %H:%M:%S",
    ]:
        try:
            if fmt.startswith("%m"):
                return datetime.strptime(f"{current_year}-{s}", f"%Y-{fmt}")
            return datetime.strptime(s, fmt)
        except ValueError:
            continue

    return None

def _load_json(file_path, default_value=None):
    if default_value is None:
        default_value = [] # 리스트를 기본값으로 쓰던 기존 코드 호환성 유지용

    if not os.path.exists(file_path):
        return default_value

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"JSON Load Error ({file_path}): {e}")
        return default_value
