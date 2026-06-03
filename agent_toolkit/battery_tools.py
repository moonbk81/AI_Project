

"""Battery / thermal analysis tools for RAG agent.

This module is intended to contain battery and thermal related tool functions
moved out of the legacy `agent_tools.py` facade.

Expected functions to move here:
- get_battery_thermal_analytics(...)
- battery drain / thermal summary helpers
- wakelock / CPU usage related fact extraction helpers
"""

from __future__ import annotations

import json
from typing import Any

from agent_toolkit.common import _ensure_dict, _load_report_json

# NOTE:
# Keep this module function-based for now.
# A BaseTool class is unnecessary unless we later introduce a formal tool registry.

def to_json(data: Any) -> str:
    """Return JSON text with Korean-readable unicode output."""
    return json.dumps(data, ensure_ascii=False, indent=2)

def get_battery_thermal_analytics(base_name: str, result_dir: str = "./result") -> str:
    """배터리 광탈 주범(Wakelock)과 기기 발열(Thermal) 최고 온도를 추출합니다."""
    report_data = _load_report_json(base_name, result_dir)
    battery_thermal_stats = _ensure_dict(report_data.get("battery_thermal_stats", {}))

    if not isinstance(battery_thermal_stats, dict):
        return json.dumps({"battery_facts": "데이터 없음"}, ensure_ascii=False)

    thermal_stats = battery_thermal_stats.get("thermal_stats", [])
    wakelock_stats = battery_thermal_stats.get("wakelock_stats", [])
    cpu_stats = report_data.get("cpu_usage_stats", [])

    max_temp = 0
    if thermal_stats:
        max_temp = max([float(t.get("temperature", 0)) for t in thermal_stats])

    top_wakelocks = []
    if wakelock_stats:
        sorted_wl = sorted(wakelock_stats, key=lambda x: int(x.get("times", 0)), reverse=True)[:3]
        top_wakelocks = [{"app": wl.get("app_name"), "times": wl.get("times")} for wl in sorted_wl]

    # 💡 Top 3 CPU 점유율 포맷팅
    top_cpus = [{"process": c.get("process"), "percent": c.get("cpu_percent")} for c in cpu_stats[:3]]

    return json.dumps({
        "max_temperature_celsius": max_temp,
        "top_wakelocks": top_wakelocks,
        "top_cpu_processes": top_cpus # 👈 LLM에게 CPU 정보 제공
    }, ensure_ascii=False)
