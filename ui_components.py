# ui_components.py
import plotly.graph_objects as go
import json
import ast

from ui.network_ui import *
from ui.crash_ui import *
from ui.telephony_ui import *
from ui.satellite_ui import *
from ui.power_ui import *
from ui.common import *


def parse_raw_logs(raw_data):
    """
    JSON, Python List, Text 등 다양한 포맷의 로그 데이터를 파싱하여 리스트로 반환합니다.
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

