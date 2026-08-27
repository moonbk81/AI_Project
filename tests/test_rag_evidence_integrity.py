"""증거가 파이프라인 중간에서 조용히 사라지는 경로를 막는 회귀 테스트.

여기서 잡는 세 가지는 모두 "답변 품질이 나쁘다"로만 드러나서 원인 추적이 어려웠던
종류다. 실패하면 LLM 앞에 도달하는 근거가 줄어든 것이므로 프롬프트를 고치기 전에
이 테스트를 먼저 본다.
"""

import re

from core.analysis_pipeline import merge_log_files
from rag.keyword_match import build_keyword_scorer, extract_query_tokens
from ril_rag_chat import RilRagChat


class TestCleanLogPayloadKeepsEvidence:
    """_clean_log_payload 의 다이어트 정규식이 문서 경계를 넘지 못하게 한다."""

    def _clean(self, text):
        # 인스턴스 상태를 쓰지 않는 메서드라 클래스를 self 로 넘겨 호출한다.
        return RilRagChat._clean_log_payload(RilRagChat, text)

    def test_nitz_diet_does_not_eat_the_next_document(self):
        text = (
            "[자료 1 - Nitz_Time_Event]\n[메타정보]\n  - log_time: 03-25 17:38:20.100\n\n"
            "[자료 2 - Call_Session]\n[메타정보]\n  - call_id: TC@119_1\n"
            "  - callFailCause: 49\n  - vendorCause: 24\n  - end_reason: CALL DROP\n\n"
            "[자료 3 - Nitz_Time_Event]\n[메타정보]\n  - log_time: 03-25 17:40:00.000\n"
            "  - timezone: UTC+9\n  - dst_status: 미적용\n"
        )
        cleaned = self._clean(text)

        # 뒤 문서의 결정적 증거가 남아 있어야 한다.
        for evidence in ["TC@119_1", "callFailCause: 49", "vendorCause: 24", "CALL DROP"]:
            assert evidence in cleaned, f"{evidence} 가 정제 과정에서 사라졌다"

        # 정작 압축 대상인 NITZ 레코드는 지워져야 한다.
        assert "dst_status" not in cleaned
        assert "NITZ 시간 보정 로그 1건" in cleaned

    def test_system_wtf_diet_does_not_eat_the_next_document(self):
        text = (
            "[자료 1 - System_Kill_Wtf_Event]\n"
            "03-25 17:38:20.100 SYSTEM_WTF: 무언가 이상합니다. 교차 확인해야 합니다.\n\n"
            "[자료 2 - Native_Crash_Event]\n[메타정보]\n  - process: rild\n"
            "  - signal: SIGSEGV\n"
        )
        cleaned = self._clean(text)

        assert "rild" in cleaned
        assert "SIGSEGV" in cleaned
        assert "SYSTEM_WTF" not in cleaned

    def test_unmatched_opening_token_leaves_document_intact(self):
        """닫는 토큰이 없으면 아무것도 지우지 않는다(비탐욕 매칭 실패)."""
        text = (
            "[자료 1 - Nitz_Time_Event]\n  - log_time: 03-25 17:38:20.100\n\n"
            "[자료 2 - Call_Session]\n  - callFailCause: 49\n"
        )
        cleaned = self._clean(text)
        assert "callFailCause: 49" in cleaned
        assert "log_time" in cleaned


class TestMergeLogFilesKeepsContextTogether:
    """타임스탬프 없는 연속 줄이 앵커에서 떨어지지 않게 한다."""

    def test_backtrace_stays_next_to_its_anchor(self, tmp_path):
        first = tmp_path / "a.log"
        first.write_text(
            "08-27 10:00:00.100 F DEBUG: Fatal signal 11 (SIGSEGV) in tid 1234 (rild)\n"
            "08-27 10:00:00.200 F DEBUG: backtrace:\n"
            "    #00 pc 0000abcd /vendor/lib64/libril.so (ril_handle+40)\n"
            "    #01 pc 0000ef01 /vendor/lib64/libril.so (main+12)\n"
            "08-27 10:00:01.000 I ActivityManager: after crash\n",
            encoding="utf-8",
        )
        second = tmp_path / "b.log"
        second.write_text(
            "08-27 09:59:59.000 I ConnectivityService: earlier line\n"
            "08-27 10:00:00.150 I Foo: interleaved\n",
            encoding="utf-8",
        )
        merged_path = tmp_path / "merged.log"

        merge_log_files([str(first), str(second)], str(merged_path))
        lines = merged_path.read_text(encoding="utf-8").splitlines()

        anchor_index = next(i for i, l in enumerate(lines) if "backtrace:" in l)
        frame_index = next(i for i, l in enumerate(lines) if "#00 pc" in l)
        assert frame_index == anchor_index + 1, "스택 프레임이 앵커에서 떨어졌다"
        assert "#01 pc" in lines[frame_index + 1]

        # 시간순 병합 자체는 그대로 유지된다.
        stamped = [l[:18] for l in lines if re.match(r"^\d{2}-\d{2}\s\d{2}:\d{2}:\d{2}\.\d{3}", l)]
        assert stamped == sorted(stamped)

    def test_header_before_first_timestamp_stays_on_top(self, tmp_path):
        log = tmp_path / "a.log"
        log.write_text(
            "========== dumpstate header ==========\n"
            "08-27 10:00:00.100 I Foo: first stamped line\n",
            encoding="utf-8",
        )
        merged_path = tmp_path / "merged.log"

        merge_log_files([str(log)], str(merged_path))
        lines = merged_path.read_text(encoding="utf-8").splitlines()

        assert lines[0].startswith("==========")


class TestKeywordScorerHandlesKorean:
    """한글 질의에서도 키워드 항이 살아 있게 한다."""

    def test_korean_query_expands_to_log_tokens(self):
        tokens = extract_query_tokens("통화가 갑자기 끊긴 원인 알려줘")
        assert "call" in tokens
        assert "drop" in tokens or "disconnect" in tokens
        assert "cause" in tokens

    def test_korean_query_separates_relevant_from_noise(self):
        relevant = "### [type: call_session]\n- end_reason: call drop\n- callfailcause: 49"
        noise = "### [type: data_usage]\n- pkg: com.example\n- rx_bytes: 1024"
        scorer = build_keyword_scorer("통화가 갑자기 끊긴 원인 알려줘", [relevant, noise])

        assert scorer(relevant) > scorer(noise)
        assert scorer(relevant) > 0.0

    def test_token_present_in_every_candidate_carries_no_weight(self):
        """후보 전체에 깔린 토큰은 변별력이 없으므로 순위를 흔들지 않는다."""
        common_only = "dns query latency 100ms"
        with_rare = "dns query latency 100ms and a sigsegv tombstone"
        scorer = build_keyword_scorer("dns sigsegv", [common_only, with_rare])

        assert scorer(with_rare) > scorer(common_only)

    def test_no_matchable_token_returns_zero_for_everyone(self):
        scorer = build_keyword_scorer("완전히 무관한 질문", ["dns query", "call session"])
        assert scorer("dns query") == 0.0
        assert scorer("call session") == 0.0


class TestLogTypeAliases:
    """모든 유형이 자기 한글 어휘를 갖게 한다."""

    def test_alias_prepended_to_plain_document(self):
        from rag.log_type_aliases import with_alias_header

        out = with_alias_header("기기 온도 기록: SUBBAT 센서의 온도가 41.2도", "Thermal_Stat")
        assert out.startswith("[관련 증상]")
        assert "발열" in out
        assert "41.2도" in out

    def test_alias_goes_under_the_type_header(self):
        from rag.log_type_aliases import with_alias_header

        out = with_alias_header("### [Type: Crash_Event]\n- process: com.foo", "Crash_Event")
        lines = out.splitlines()
        assert lines[0] == "### [Type: Crash_Event]"
        assert lines[1].startswith("[관련 증상]")
        assert lines[2] == "- process: com.foo"

    def test_unknown_type_is_left_alone(self):
        from rag.log_type_aliases import with_alias_header

        assert with_alias_header("본문", "Made_Up_Type") == "본문"

    def test_applied_twice_does_not_duplicate(self):
        from rag.log_type_aliases import with_alias_header

        once = with_alias_header("본문", "Thermal_Stat")
        assert with_alias_header(once, "Thermal_Stat") == once

    def test_payload_choke_point_applies_alias(self):
        from rag_builders.common import make_payload

        payload = make_payload("본문", {"log_type": "System_Kill_Wtf_Event"})
        assert "갑자기 죽음" in payload["document"]

    def test_stall_alias_does_not_claim_freeze_vocabulary(self):
        """'멈춤' 을 인터넷 스톨에 두면 '폰이 멈추고 터치가 안 먹어' 를 흡수한다."""
        from rag.log_type_aliases import LOG_TYPE_ALIASES

        assert "멈춤" not in LOG_TYPE_ALIASES["Internet_Stall_Analysis"]
        assert "멈춤" in LOG_TYPE_ALIASES["Binder_Warning"]


class TestCollectionConfig:
    def test_hnsw_config_is_stronger_than_chroma_defaults(self):
        from rag.collection_config import COLLECTION_CONFIGURATION, HNSW_CONFIG

        # 기본값(100/16/100)으로는 소수 유형 군집에 그래프가 닿지 못했다.
        assert HNSW_CONFIG["ef_construction"] > 100
        assert HNSW_CONFIG["max_neighbors"] > 16
        assert HNSW_CONFIG["ef_search"] > 100
        assert COLLECTION_CONFIGURATION["hnsw"]["space"] == "l2"

    def test_ensure_search_ef_skips_when_already_at_target(self):
        from rag.collection_config import HNSW_CONFIG, ensure_search_ef

        class Already:
            name = "c"
            configuration_json = {"hnsw": {"ef_search": HNSW_CONFIG["ef_search"]}}

            def modify(self, **kwargs):
                raise AssertionError("이미 목표값이면 건드리지 않아야 한다")

        assert ensure_search_ef(Already()) is False

    def test_ensure_search_ef_raises_low_value(self):
        from rag.collection_config import HNSW_CONFIG, ensure_search_ef

        calls = []

        class Low:
            name = "c"
            configuration_json = {"hnsw": {"ef_search": 100}}

            def modify(self, **kwargs):
                calls.append(kwargs)

        assert ensure_search_ef(Low()) is True
        assert calls == [{"configuration": {"hnsw": {"ef_search": HNSW_CONFIG["ef_search"]}}}]

    def test_ensure_search_ef_survives_a_broken_collection(self):
        from rag.collection_config import ensure_search_ef

        class Broken:
            name = "c"
            configuration_json = {"hnsw": {"ef_search": 100}}

            def modify(self, **kwargs):
                raise RuntimeError("서버가 거부")

        assert ensure_search_ef(Broken()) is False


class TestHnswMismatchIsVisible:
    """구버전 설정으로 색인된 컬렉션을 조용히 넘어가지 않는다."""

    class _Col:
        name = "ril_logs"

        def __init__(self, ef_construction, max_neighbors, ef_search):
            self.configuration_json = {
                "hnsw": {
                    "ef_construction": ef_construction,
                    "max_neighbors": max_neighbors,
                    "ef_search": ef_search,
                }
            }
            self.modified = []

        def modify(self, **kwargs):
            self.modified.append(kwargs)

    def test_weak_graph_params_are_reported(self, capsys):
        from rag.collection_config import HNSW_CONFIG, ensure_hnsw_settings

        col = self._Col(100, 16, HNSW_CONFIG["ef_search"])
        ensure_hnsw_settings(col)

        out = capsys.readouterr().out
        assert "ef_construction=100" in out
        assert "max_neighbors=16" in out
        assert "재적재" in out

    def test_matching_collection_stays_quiet(self, capsys):
        from rag.collection_config import HNSW_CONFIG, ensure_hnsw_settings

        col = self._Col(
            HNSW_CONFIG["ef_construction"],
            HNSW_CONFIG["max_neighbors"],
            HNSW_CONFIG["ef_search"],
        )
        ensure_hnsw_settings(col)

        assert capsys.readouterr().out == ""
        assert col.modified == []

    def test_search_ef_is_still_raised_while_warning(self):
        from rag.collection_config import HNSW_CONFIG, ensure_hnsw_settings

        col = self._Col(100, 16, 100)
        assert ensure_hnsw_settings(col) is True
        assert col.modified == [
            {"configuration": {"hnsw": {"ef_search": HNSW_CONFIG["ef_search"]}}}
        ]
