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
        # "원인"/"알려줘" 는 의도어라 근거 토큰에서 빠진다.
        assert "cause" not in tokens

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


class _FakeCollection:
    """log_type 별로 문서를 갖고 있는 최소한의 Chroma 흉내.

    where 의 log_type / $in 조건과 n_results 만 해석한다.
    """

    def __init__(self, rows):
        # rows: [(id, document, metadata, distance), ...] 거리 오름차순
        self.rows = rows
        self.queries = []

    def query(self, query_embeddings=None, n_results=10, where=None):
        self.queries.append(where)
        wanted = self._wanted_types(where)
        picked = [
            r for r in self.rows
            if wanted is None or str(r[2].get("log_type")) in wanted
        ][:n_results]
        return {
            "ids": [[r[0] for r in picked]],
            "documents": [[r[1] for r in picked]],
            "metadatas": [[r[2] for r in picked]],
            "distances": [[r[3] for r in picked]],
        }

    @staticmethod
    def _wanted_types(where):
        if not where:
            return None
        conditions = where.get("$and", [where])
        for condition in conditions:
            log_type = condition.get("log_type")
            if isinstance(log_type, str):
                return {log_type}
            if isinstance(log_type, dict) and "$in" in log_type:
                return set(log_type["$in"])
        return None


class _FakeEmbedder:
    def encode(self, text, **kwargs):
        import numpy as np

        return np.zeros(3, dtype="float32")


def _rows():
    return [
        ("thermal-1", "[관련 증상] 발열 뜨거움\n기기 온도 기록: 41.2도",
         {"log_type": "Thermal_Stat", "sensor": "skin"}, 0.70),
        ("nitz-1", "[관련 증상] 시간 자동 설정\nlog_time: 03-25",
         {"log_type": "Nitz_Time_Event", "timezone": "UTC+9"}, 0.95),
        ("usage-1", "[관련 증상] 데이터 사용량\npkg com.example",
         {"log_type": "Data_Usage", "pkg": "com.example"}, 1.30),
    ]


class TestSoftLogTypeFilter:
    """라우팅 오판이 리콜 0 으로 이어지지 않게 한다."""

    def test_wrong_routing_still_surfaces_the_right_document(self):
        from rag.retrieval import retrieve_and_rerank

        col = _FakeCollection(_rows())
        results = retrieve_and_rerank(
            collection=col,
            embed_model=_FakeEmbedder(),
            search_query="발열이 심한데 원인 찾아줘",
            top_k=3,
            target_log_types=["Nitz_Time_Event"],  # 오판
        )

        assert "thermal-1" in results["ids"][0], "좁힌 유형 밖의 정답이 후보에서 사라졌다"
        # 좁힌 검색과 좁히지 않은 검색이 각각 한 번씩 나가야 한다.
        assert len(col.queries) == 2
        assert col.queries[0] is not None
        assert col.queries[1] is None

    def test_no_second_query_without_routing(self):
        from rag.retrieval import retrieve_and_rerank

        col = _FakeCollection(_rows())
        retrieve_and_rerank(
            collection=col,
            embed_model=_FakeEmbedder(),
            search_query="발열이 심한데",
            top_k=3,
            target_log_types=None,
        )
        assert len(col.queries) == 1

    def test_routed_type_gets_a_finite_preference(self):
        from rag.retrieval import ROUTED_LOG_TYPE_BONUS

        # 가점이 키워드 항(최대 0.6)을 넘어서면 예전의 하드 필터와 다를 바 없다.
        assert 0 < ROUTED_LOG_TYPE_BONUS < 0.6

    def test_caller_log_type_list_is_not_mutated(self):
        from rag.retrieval import retrieve_and_rerank

        # config.yaml 의 routing_map 리스트가 그대로 넘어오므로 사본을 써야 한다.
        shared = ["Call_Session"]
        retrieve_and_rerank(
            collection=_FakeCollection(_rows()),
            embed_model=_FakeEmbedder(),
            search_query="데이터 호 연결 실패 사유",  # datacall 확장이 걸리는 질의
            top_k=3,
            target_log_types=shared,
        )
        assert shared == ["Call_Session"]


class TestWeakEvidenceIsFlagged:
    """근거가 멀면 '없다' 고 말할 경로를 만든다."""

    def test_close_evidence_is_not_weak(self):
        from rag.retrieval import retrieve_and_rerank

        results = retrieve_and_rerank(
            collection=_FakeCollection(_rows()),
            embed_model=_FakeEmbedder(),
            search_query="발열이 심한데",
            top_k=3,
        )
        assert results["evidence_is_weak"] is False
        assert results["best_distance"] == 0.70

    def test_far_evidence_is_weak(self):
        from rag.retrieval import WEAK_EVIDENCE_DISTANCE, retrieve_and_rerank

        far = [(i, d, m, WEAK_EVIDENCE_DISTANCE + 0.2) for i, d, m, _ in _rows()]
        results = retrieve_and_rerank(
            collection=_FakeCollection(far),
            embed_model=_FakeEmbedder(),
            search_query="김치찌개 맛있게 끓이는 법",
            top_k=3,
        )
        assert results["evidence_is_weak"] is True

    def test_distances_line_up_with_the_returned_documents(self):
        from rag.retrieval import retrieve_and_rerank

        results = retrieve_and_rerank(
            collection=_FakeCollection(_rows()),
            embed_model=_FakeEmbedder(),
            search_query="발열이 심한데",
            top_k=2,
        )
        assert len(results["distances"][0]) == len(results["documents"][0]) == 2


class TestKeywordScoreIgnoresSchema:
    """metadata 의 키 이름이 순위를 정하지 못하게 한다."""

    def test_intent_words_are_not_evidence_terms(self):
        from rag.keyword_match import extract_query_tokens

        tokens = extract_query_tokens("앱이 죽은 근본 원인이 뭐야")
        assert "cause" not in tokens, "'원인' 은 의도어라 근거 토큰이 되면 안 된다"
        assert "원인" not in tokens
        assert "kill" in tokens or "crash" in tokens

    def test_one_rare_token_cannot_decide_alone(self):
        from rag.keyword_match import build_keyword_scorer

        # 두 토큰 중 흔한 것만 가진 문서와, 희귀한 것만 가진 문서.
        common = ["crash log here"] * 14
        rare_holder = "sigsegv only here"
        texts = common + [rare_holder, "unrelated text"]
        scorer = build_keyword_scorer("crash sigsegv", texts)

        # 희귀 토큰 하나가 점수를 독점하면 흔한 토큰 보유 문서가 0 에 가까워진다.
        assert scorer(common[0]) > 0.2


class TestRecallEvalGroundTruth:
    """골든셋을 검색 측정의 정답 라벨로 쓰는 규칙."""

    def test_evidence_matching_survives_formatting_differences(self):
        from rag.recall_eval import document_haystack, index_evidence

        entries = [(
            "d1",
            "### [Type: Call_Session]\n-  callFailCause:   49\n- end_reason: CALL DROP",
            {"is_user_reject": False, "call_id": "TC@119_1"},
        )]
        found = index_evidence(
            ["callFailCause: 49", "TC@119_1", "is_user_reject: false", "없는값"], entries
        )

        assert found["callFailCause: 49"] == {"d1"}, "공백이 달라도 붙어야 한다"
        assert found["TC@119_1"] == {"d1"}
        assert found["is_user_reject: false"] == {"d1"}, "metadata 는 '키: 값' 으로 펴야 한다"
        assert found["없는값"] == set()
        assert "call drop" in document_haystack(entries[0][1], entries[0][2])

    def test_negative_statements_are_not_treated_as_evidence(self):
        from rag.recall_eval import scored_evidence_terms

        case = {
            "evidence_should_include": [
                "SIGSEGV", "Crash_Event 없음", "존재하지 않", "음영지역/기지국 장애가 아님",
            ]
        }
        assert scored_evidence_terms(case) == ["SIGSEGV"]

    def test_absence_cases_are_recognised(self):
        from rag.recall_eval import is_absence_case

        assert is_absence_case({"trap_type": "absence_check"}) is True
        assert is_absence_case({"trap_type": "negative_binder_leak_absence_check"}) is True
        assert is_absence_case({"trap_type": "root_cause_disambiguation"}) is False
        assert is_absence_case({}) is False

    def test_denied_log_types_are_pulled_from_the_case(self):
        from rag.recall_eval import denied_log_types

        case = {
            "evidence_should_include": ["Nitz_Time_Event", "존재하지 않"],
            "must_have": ["Crash_Event 없음", "ANR_Context 없음"],
            "root_cause": "request_failed 로그에는 Nitz_Time_Event가 존재하지 않습니다.",
        }
        denied = denied_log_types(case)
        assert "Nitz_Time_Event" in denied
        assert "Crash_Event" in denied
        assert "ANR_Context" in denied
        assert denied.count("Nitz_Time_Event") == 1

    def test_absence_measure_flags_polluted_top_k(self):
        from rag.recall_eval import measure_absence

        case = {"trap_type": "absence_check", "evidence_should_include": ["Crash_Event 없음"]}
        corpus = [("d1", "무관한 문서", {"log_type": "Boot_Stat"})]
        clean = measure_absence(case, corpus, corpus)
        assert clean["absence_holds"] == ["Crash_Event"]
        assert clean["false_evidence_in_top_k"] == []

        polluted_corpus = corpus + [("d2", "크래시", {"log_type": "Crash_Event"})]
        polluted = measure_absence(case, polluted_corpus, polluted_corpus)
        assert polluted["absence_broken"] == ["Crash_Event"]
        assert polluted["false_evidence_in_top_k"] == ["Crash_Event"]

    def test_retrieval_only_blames_evidence_that_exists(self):
        from rag.recall_eval import measure_retrieval

        case = {"evidence_should_include": ["있는근거", "코퍼스에없는근거"]}
        corpus = [
            ("d1", "있는근거 포함 문서", {}),
            ("d2", "다른 문서", {}),
        ]
        # top_k 가 정답 문서를 놓친 경우
        missed = measure_retrieval(case, [corpus[1]], corpus)
        assert missed["answerable_evidence"] == 1, "코퍼스에 없는 근거는 검색 책임이 아니다"
        assert missed["evidence_recall"] == 0.0
        assert missed["missed_evidence"] == ["있는근거"]
        assert missed["first_relevant_rank"] is None

        # top_k 가 정답 문서를 올린 경우
        hit = measure_retrieval(case, [corpus[0], corpus[1]], corpus)
        assert hit["evidence_recall"] == 1.0
        assert hit["first_relevant_rank"] == 1

    def test_ann_recall_compares_exact_with_approximate(self):
        from rag.recall_eval import ann_recall

        assert ann_recall(["a", "b", "c", "d"], ["a", "b", "x", "y"]) == 0.5
        assert ann_recall(["a"], ["a"]) == 1.0
        assert ann_recall([], ["a"]) is None


class TestBucketsKeepEveryOccurrence:
    """버킷이 같은 문자열을 접어버려 발생 횟수를 잃지 않게 한다.

    예전에는 라인 문자열로 중복을 지웠다. 그러면 window 가 겹쳐 생긴 중복과, 로그에
    실제로 여러 번 찍힌 같은 줄을 구분할 수 없다. 실측으로 request_failed 로그에서
    528줄(9%), binder_leak 로그에서 10,166줄(9%, binder 버킷만 5,544줄)이 사라졌다.
    """

    def _build(self, lines):
        from log_orchestrator import LogOrchestrator

        # 실제 배선을 그대로 쓴다. __init__ 은 파일을 읽지 않는다.
        orchestrator = LogOrchestrator("dummy.log")
        return orchestrator.bucket_builder.build(lines)

    def test_repeated_identical_lines_all_survive(self):
        line = "01-01 00:00:01.000 SatelliteController: NTN signal lost\n"
        lines = [line, "01-01 00:00:02.000 Foo: unrelated\n", line, line]

        buckets = self._build(lines)

        assert len(buckets["ntn"]) == 3, "같은 문자열이 세 번 찍혔으면 세 번 남아야 한다"

    def test_overlapping_windows_include_each_line_once(self):
        lines = [f"01-01 00:00:{i:02d}.000 Foo: filler\n" for i in range(40)]
        lines[10] = "01-01 00:00:10.000 DEBUG: Fatal signal 11 (SIGSEGV)\n"
        lines[12] = "01-01 00:00:12.000 DEBUG: Fatal signal 11 (SIGSEGV)\n"

        buckets = self._build(lines)

        # window=80 이라 두 앵커의 문맥이 전체를 덮는다. 겹쳐도 한 번씩만.
        assert len(buckets["crash"]) == len(lines)
        assert len(set(map(id, buckets["crash"]))) == len(lines)

    def test_bucket_keeps_original_line_order(self):
        lines = [
            "01-01 00:00:03.000 SatelliteController: NTN c\n",
            "01-01 00:00:01.000 SatelliteController: NTN a\n",
            "01-01 00:00:02.000 SatelliteController: NTN b\n",
        ]

        buckets = self._build(lines)

        assert buckets["ntn"] == lines, "원본에 적힌 순서를 그대로 유지해야 한다"

    def test_context_window_lines_are_not_duplicated_across_buckets(self):
        """datacall 과 internet_stall 은 같은 앵커에서 각자 window 를 뜬다."""
        lines = [f"01-01 00:00:{i:02d}.000 Foo: filler\n" for i in range(30)]
        lines[15] = "01-01 00:00:15.000 DcTracker: SetupDataCall failed\n"

        buckets = self._build(lines)

        # filler 는 초가 달라 전부 서로 다른 문자열이다. 중복이 있으면 window 가
        # 같은 줄을 두 번 담았다는 뜻이다.
        assert len(buckets["datacall"]) == len(set(buckets["datacall"]))
        assert lines[15] in buckets["datacall"]
        assert lines[15] in buckets["internet_stall"]


class TestYearBoundary:
    """연도가 없는 "MM-DD" 타임스탬프가 연말에 뒤집히지 않게 한다."""

    def test_merge_puts_january_after_december(self, tmp_path):
        log = tmp_path / "a.log"
        log.write_text(
            "01-01 00:00:05.000 I Foo: new year\n"
            "12-31 23:59:58.000 I Foo: old year\n",
            encoding="utf-8",
        )
        merged = tmp_path / "m.log"

        merge_log_files([str(log)], str(merged))
        lines = merged.read_text(encoding="utf-8").splitlines()

        assert "old year" in lines[0], "12-31 이 01-01 보다 앞에 와야 한다"
        assert "new year" in lines[1]

    def test_merge_without_rollover_is_unchanged(self, tmp_path):
        log = tmp_path / "a.log"
        log.write_text(
            "03-26 10:00:02.000 I Foo: second\n"
            "03-26 10:00:01.000 I Foo: first\n",
            encoding="utf-8",
        )
        merged = tmp_path / "m.log"

        merge_log_files([str(log)], str(merged))
        lines = merged.read_text(encoding="utf-8").splitlines()

        assert "first" in lines[0]
        assert "second" in lines[1]

    def test_slice_handles_a_range_that_crosses_new_year(self, tmp_path):
        from core.analysis_pipeline import slice_log_by_time

        source = tmp_path / "in.log"
        source.write_text(
            "12-31 22:00:00.000 I Foo: before\n"
            "12-31 23:30:00.000 I Foo: inside old year\n"
            "01-01 00:30:00.000 I Foo: inside new year\n"
            "01-01 02:00:00.000 I Foo: after\n",
            encoding="utf-8",
        )
        sliced = tmp_path / "out.log"

        written = slice_log_by_time(
            str(source), str(sliced), "12-31 23:00:00", "01-01 01:00:00"
        )
        text = sliced.read_text(encoding="utf-8")

        assert written == 2
        assert "inside old year" in text
        assert "inside new year" in text
        assert "before" not in text
        assert "after" not in text

    def test_slice_does_not_truncate_when_time_goes_backwards(self, tmp_path):
        """dumpstate 는 섹션을 이어 붙여 시간이 되돌아간다. 예전엔 거기서 잘렸다."""
        from core.analysis_pipeline import slice_log_by_time

        source = tmp_path / "in.log"
        source.write_text(
            "03-26 10:00:00.000 I Foo: wanted 1\n"
            "03-26 23:00:00.000 I Foo: out of range, past the end\n"
            "03-26 10:00:05.000 I Foo: wanted 2\n",
            encoding="utf-8",
        )
        sliced = tmp_path / "out.log"

        written = slice_log_by_time(
            str(source), str(sliced), "03-26 09:00:00", "03-26 11:00:00"
        )
        text = sliced.read_text(encoding="utf-8")

        assert "wanted 1" in text
        assert "wanted 2" in text, "되돌아가는 줄 뒤가 잘려나갔다"
        assert "out of range" not in text
        assert written == 2

    def test_slice_normal_range_still_works(self, tmp_path):
        from core.analysis_pipeline import slice_log_by_time

        source = tmp_path / "in.log"
        source.write_text(
            "03-26 09:00:00.000 I Foo: before\n"
            "03-26 10:00:00.000 I Foo: inside\n"
            "03-26 12:00:00.000 I Foo: after\n",
            encoding="utf-8",
        )
        sliced = tmp_path / "out.log"

        written = slice_log_by_time(
            str(source), str(sliced), "03-26 09:30:00", "03-26 11:00:00"
        )

        assert written == 1
        assert "inside" in sliced.read_text(encoding="utf-8")


class TestBinderLeakDocumentKeepsItsSourceEvent:
    """요약 문서가 근거 이벤트의 이름을 지우지 않게 한다.

    config.yaml 의 도메인 규칙은 "BINDER_PROXY_HISTOGRAM 에서 추출된 max_count 를
    가장 정확한 팩트로 간주하라" 처럼 원본 이벤트 이름으로 지시한다. 빌더가 type 을
    BINDER_PROXY_LEAK_SUMMARY 로 바꿔 쓰면서 그 이름이 payload 어디에도 남지 않아,
    프롬프트가 가리키는 근거를 검색으로도 인용으로도 찾을 수 없었다.
    """

    def _payloads(self):
        from rag_builders.binder_builder import build_binder_payloads

        report = {
            "binder_warnings": [
                {
                    "time": "06-01 15:13:17.312",
                    "type": "BINDER_PROXY_HISTOGRAM",
                    "max_count": 7262,
                    "desc": "Binder Proxy 객체 상태 덤프: android.content.IIntentReceiver (7262개)",
                }
            ]
        }
        return build_binder_payloads(report, "binder_leak_am_kill_report.json")

    def test_source_event_name_survives_into_the_document(self):
        payloads = self._payloads()
        leak_docs = [
            p for p in payloads
            if (p["metadata"] or {}).get("type") == "BINDER_PROXY_LEAK_SUMMARY"
        ]
        assert leak_docs, "누수 요약 문서가 만들어지지 않았다"

        document = leak_docs[0]["document"]
        metadata = leak_docs[0]["metadata"]

        assert metadata["source_type"] == "BINDER_PROXY_HISTOGRAM"
        assert "BINDER_PROXY_HISTOGRAM" in document
        # 기존 소비자(structured_event_renderer 등)가 보는 type 은 그대로 둔다.
        assert metadata["type"] == "BINDER_PROXY_LEAK_SUMMARY"

    def test_the_numbers_the_rules_ask_for_are_still_there(self):
        leak_docs = [
            p for p in self._payloads()
            if (p["metadata"] or {}).get("type") == "BINDER_PROXY_LEAK_SUMMARY"
        ]
        metadata = leak_docs[0]["metadata"]

        assert metadata["max_proxy_count"] == 7262
        assert metadata["leaked_descriptor"] == "android.content.IIntentReceiver"

    def test_evidence_lookup_now_finds_the_label(self):
        """측정 도구가 TC-022 의 근거를 찾을 수 있어야 한다."""
        from rag.recall_eval import index_evidence

        payloads = self._payloads()
        entries = [
            (str(i), p["document"], p["metadata"]) for i, p in enumerate(payloads)
        ]
        found = index_evidence(["BINDER_PROXY_HISTOGRAM", "IIntentReceiver", "7262개"], entries)

        assert found["BINDER_PROXY_HISTOGRAM"], "골든셋이 요구하는 라벨을 찾지 못했다"
        assert found["IIntentReceiver"]
        assert found["7262개"]
