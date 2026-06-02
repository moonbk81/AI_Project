

import re
from typing import Any


class StructuredEventRenderer:
    """Render structured Summary/RCA events without asking the LLM to reinterpret them.

    This is a generic direct-rendering layer for parser/payload outputs that are already
    structured enough to answer safely:
    - Summary/count questions -> Summary/Histogram metadata
    - Root cause/correlation questions -> RCA_Event metadata
    """

    @staticmethod
    def _is_structured_fact_query(query_lower: str) -> bool:
        return any(k in query_lower for k in [
            "몇 회", "몇회", "발생 횟수", "횟수", "몇 개", "몇개", "최대 개수", "개수", "count",
            "몇 건", "몇건", "총 몇", "얼마나", "최대 몇"
        ])

    @staticmethod
    def _is_root_cause_query(query_lower: str) -> bool:
        return any(k in query_lower for k in [
            "root cause", "근본 원인", "원인", "왜", "죽", "강제 종료", "강제종료",
            "크래시", "crash", "am_kill", "system_kill", "바인더", "binder",
            "프록시", "proxy", "누수", "leak", "연관", "상관", "관련"
        ])

    @staticmethod
    def _metas(results: dict[str, Any]) -> list[dict[str, Any]]:
        if not results or not results.get("metadatas") or not results["metadatas"] or not results["metadatas"][0]:
            return []
        return [m for m in results["metadatas"][0] if isinstance(m, dict)]

    @classmethod
    def _render_rca_event_answer(cls, meta: dict[str, Any], user_query: str) -> str | None:
        """Render a generic RCA_Event from structured metadata."""
        if not isinstance(meta, dict) or meta.get("log_type") != "RCA_Event":
            return None

        rca_type = meta.get("rca_type") or "RCA"
        process = meta.get("process") or "Unknown"
        root_cause = meta.get("root_cause") or "원인 정보 없음"
        time = meta.get("time") or "Unknown"
        developer_action = meta.get("developer_action") or "관련 생명주기/리소스 해제 로직 점검 필요"

        if rca_type == "BINDER_PROXY_LEAK_RCA":
            kill_event = meta.get("kill_event") or "am_kill"
            kill_reason = meta.get("kill_reason") or "Too many Binders sent to SYSTEM"
            leaked_descriptor = meta.get("leaked_descriptor") or "Binder proxy object"
            max_proxy_count = meta.get("max_proxy_count") or meta.get("max_count") or "Unknown"
            query_lower = (user_query or "").lower()

            if any(k in query_lower for k in ["개발", "가이드", "고쳐", "수정", "점검", "연관", "상관", "관련"]):
                return (
                    f"{process}의 am_wtf 대량 발생, {kill_event} 강제 종료, Binder Proxy 누수는 서로 연관된 현상으로 판단됨. "
                    f"강제 종료 사유는 '{kill_reason}'이며, Binder Proxy Histogram에서 {leaked_descriptor} 객체가 최대 {max_proxy_count}개까지 누수됨. "
                    f"근본 원인은 {root_cause}이며, 단순 일시 오류나 Native Crash가 아니라 바인더 프록시 누수에 따른 시스템 리소스 고갈로 판단됨. "
                    f"개발자는 {developer_action}."
                )

            return (
                f"{process} 프로세스가 {kill_event}로 강제 종료된 이력이 확인됨. "
                f"강제 종료 사유는 '{kill_reason}'이며, 동시간대 Binder Proxy Histogram에서 "
                f"{leaked_descriptor} 객체가 최대 {max_proxy_count}개까지 누수된 정황이 확인됨. "
                f"따라서 근본 원인은 단순 앱 크래시나 Native Crash가 아니라 {root_cause}에 따른 시스템 리소스 고갈로 판단됨. "
                f"발생 시각은 {time}이며, 개발자는 {developer_action}."
            )

        return (
            f"{process}에서 {rca_type} RCA_Event가 확인됨. "
            f"근본 원인은 {root_cause}로 판단됨. "
            f"발생 시각은 {time}이며, 개발 조치는 {developer_action}."
        )

    @classmethod
    def _render_summary_event_answer(cls, results: dict[str, Any], user_query: str) -> str | None:
        """Render Summary/Count style answers from structured metadata."""
        query_lower = (user_query or "").lower()
        if not cls._is_structured_fact_query(query_lower):
            return None

        metas = cls._metas(results)
        wants_wtf = "am_wtf" in query_lower or "wtf" in query_lower
        wants_proxy = any(k in query_lower for k in ["바인더", "binder", "프록시", "proxy", "누수", "leak"])
        if not (wants_wtf or wants_proxy):
            return None

        parts: list[str] = []

        if wants_wtf:
            wtf_summaries = []
            for meta in metas:
                if meta.get("type") != "SYSTEM_WTF_SUMMARY":
                    continue
                text = " ".join([
                    str(meta.get("exception_info", "")),
                    str(meta.get("trigger_sample", "")),
                ])
                count = None
                match = re.search(r"총\s*(\d+)\s*회", text)
                if match:
                    try:
                        count = int(match.group(1))
                    except Exception:
                        count = None
                wtf_summaries.append({
                    "process": meta.get("process") or "Unknown",
                    "count": count,
                    "time": meta.get("time") or "Unknown",
                })

            if wtf_summaries:
                summary_texts = []
                for item in wtf_summaries:
                    if item["count"] is not None:
                        summary_texts.append(f"{item['process']} {item['count']}회")
                    else:
                        summary_texts.append(f"{item['process']} 발생 횟수 확인 필요")
                parts.append("am_wtf 이상 징후 대량 발생 이력은 " + ", ".join(summary_texts) + "로 확인됨.")
            else:
                parts.append("am_wtf 이상 징후 대량 발생 요약은 검색 결과에서 명확히 확인되지 않음.")

        if wants_proxy:
            proxy_meta = next(
                (
                    meta for meta in metas
                    if meta.get("log_type") == "Binder_Warning"
                    and meta.get("type") in ("BINDER_PROXY_LEAK_SUMMARY", "BINDER_PROXY_HISTOGRAM", "BINDER_PROXY_LEAK")
                ),
                None
            )
            if not proxy_meta:
                proxy_meta = next(
                    (
                        meta for meta in metas
                        if meta.get("log_type") == "RCA_Event"
                        and meta.get("rca_type") == "BINDER_PROXY_LEAK_RCA"
                    ),
                    None
                )

            if proxy_meta:
                leaked_descriptor = proxy_meta.get("leaked_descriptor") or "Binder proxy object"
                max_count = proxy_meta.get("max_proxy_count") or proxy_meta.get("max_count") or "Unknown"
                raw_info = proxy_meta.get("raw_info") or proxy_meta.get("trigger") or ""
                parts.append(
                    f"동시간대 Binder Proxy Histogram에서는 {leaked_descriptor} 객체가 최대 {max_count}개까지 누수된 것으로 확인됨."
                )
                if raw_info:
                    parts.append(f"근거 원문 요약: {raw_info}")
            else:
                parts.append("Binder Proxy Histogram의 최대 누수 개수는 검색 결과에서 명확히 확인되지 않음.")

        if not parts:
            return None

        parts.append("따라서 수치형 질문은 Raw Event 추론보다 Summary/Histogram의 구조화된 count 값을 우선 사용함.")
        return " ".join(parts)

    @classmethod
    def render(cls, results: dict[str, Any], user_query: str) -> str | None:
        """Generic entry point for structured direct rendering."""
        if not results or not results.get("metadatas") or not results["metadatas"] or not results["metadatas"][0]:
            return None

        query_lower = (user_query or "").lower()

        if cls._is_structured_fact_query(query_lower):
            answer = cls._render_summary_event_answer(results, user_query)
            if answer:
                return answer

        if not cls._is_root_cause_query(query_lower):
            return None

        metas = cls._metas(results)
        rca_meta = next((m for m in metas if m.get("log_type") == "RCA_Event"), None)
        if not rca_meta:
            return None

        return cls._render_rca_event_answer(rca_meta, user_query)