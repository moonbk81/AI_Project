
import json
import os
from typing import Any, Dict, Optional


def detect_satellite_type(ntn_data: Any = None) -> Optional[str]:
    """Which constellation an analyzed log carries, if any."""
    if isinstance(ntn_data, dict) and any(v for v in ntn_data.values() if v):
        return "SpaceX"
    if isinstance(ntn_data, list) and ntn_data:
        return "SpaceX"

    return None


def _load_artifact(base_name: str, artifact: str, result_dir: str):
    path = os.path.join(result_dir, f"{base_name}_{artifact}.json")
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_satellite_overview(base_name: str, result_dir: str = "./result") -> Dict[str, Any]:
    """The NTN artifact plus the detected constellation in one read."""
    ntn_data = _load_artifact(base_name, "ntn", result_dir)
    return {
        "sat_type": detect_satellite_type(ntn_data=ntn_data),
        "ntn": ntn_data,
    }


def get_ntn_spacex_analytics(base_name: str, result_dir: str = "./result") -> str:
    """SpaceX(Direct-to-Cell) 위성 로밍 이력 및 신호 상태만 추출합니다. (Call 미지원)"""
    ntn_path = os.path.join(result_dir, f"{base_name}_ntn.json")
    if not os.path.exists(ntn_path):
        return json.dumps({"status": "NO_DATA", "message": "SpaceX/NTN 연결 이력이 없습니다."}, ensure_ascii=False)

    with open(ntn_path, 'r', encoding='utf-8') as f:
        ntn_data = json.load(f)

    policy_events = [d for d in ntn_data if d.get('log_type') == 'NTN_Policy']

    return json.dumps({
        "spacex_ntn_facts": {
            "ntn_policy_change_events_count": len(policy_events),
            "raw_policy_events": policy_events[:10]
        }
    }, ensure_ascii=False)
