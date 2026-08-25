"""The log excerpts an answer stands on.

Retrieval hands back Chroma metadata rows; this turns them into the reference
blocks a reader checks the answer against. Pure functions, shared by Streamlit,
the browser UI and the API.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
import json
from typing import Any, Dict, List, Optional

# Raw log lines shown per reference before the rest is folded away.
RAW_LOG_PREVIEW_LINES = 10

# Where a row keeps its raw lines, most specific first. Different parsers fill
# different ones, so the lookup falls through in this order.
_RAW_LOG_FIELDS = ("raw_logs", "raw_context", "raw_stack")


def parse_raw_logs(raw_data: Any) -> List[str]:
    """Normalise a row's raw log lines into a list.

    Chroma cannot store lists, so parsers stringify them on the way in — as
    JSON, as a Python literal, or as one blob of newlines. All three come back.
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


@dataclass(frozen=True)
class ReferenceBlock:
    """One retrieved log row, ready to show under an answer."""

    index: int  # 1-based, as the reader sees it
    time: Any
    slot: Any
    # A past analysis recorded for this row — the reason it ranked highly.
    known_solution: Optional[str] = None
    raw_logs: List[str] = field(default_factory=list)
    raw_log_total: int = 0
    raw_request: Optional[str] = None
    raw_response: Optional[str] = None

    @property
    def truncated(self) -> bool:
        return self.raw_log_total > len(self.raw_logs)


def build_reference_blocks(
    metas: Optional[List[Dict[str, Any]]],
    *,
    preview_lines: int = RAW_LOG_PREVIEW_LINES,
) -> List[ReferenceBlock]:
    blocks = []
    for index, meta in enumerate(metas or [], start=1):
        raw_data = "[]"
        for key in _RAW_LOG_FIELDS:
            if key in meta:
                raw_data = meta[key]
                break
        raw_logs = parse_raw_logs(raw_data)

        blocks.append(
            ReferenceBlock(
                index=index,
                time=meta.get("time", "N/A"),
                slot=meta.get("slot", "N/A"),
                known_solution=meta.get("known_solution") or None,
                raw_logs=raw_logs[:preview_lines],
                raw_log_total=len(raw_logs),
                raw_request=meta.get("raw_request") or None,
                raw_response=meta.get("raw_response") or None,
            )
        )
    return blocks
