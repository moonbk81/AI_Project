"""답변에 이 저장소의 내부 이름이 새지 않는지 지킨다.

config.yaml 은 intent, 도구 함수, 문서 분류를 전부 코드 이름으로 부르고 그
이름을 그대로 프롬프트에 실어 모델에게 지시한다. 모델은 지시받은 말로 답하므로,
"`Binder_Warning`에서 확인됩니다" 같은 문장이 PLM 코멘트까지 그대로 나간다.

여기서 잡는 것은 둘이다. config 가 새 이름을 늘렸는데 우리말이 없는 경우와,
단말이 남긴 문자열까지 같이 번역해 버리는 경우.
"""

import yaml

from rag.plain_language import DISPLAY_NAMES, humanize


def _config():
    with open("config.yaml", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def test_every_config_name_has_korean_wording():
    config = _config()
    names = set(config["routing_map"])
    for node in config["routing_map"].values():
        names |= set(node.get("tools") or [])
        names |= set(node.get("log_types") or [])
    names |= set(config["log_guidelines"])

    missing = sorted(names - set(DISPLAY_NAMES))
    assert not missing, f"config.yaml 에 있는데 우리말이 없는 이름: {missing}"


def test_document_categories_are_replaced_even_inside_backticks():
    answer = humanize("검색된 `Binder_Warning`에서 지연이 확인되고, Call_Session 3건이 있습니다.")

    assert "Binder_Warning" not in answer
    assert "Call_Session" not in answer
    assert "바인더 경고에서" in answer, "한글 조사 앞에서 치환이 끊겼다"


def test_tool_names_do_not_reach_the_reader():
    answer = humanize("get_crash_anr_analytics 결과 Crash_Event 3건입니다.")

    assert "get_crash_anr_analytics" not in answer
    assert "크래시·ANR 분석 결과" in answer


def test_a_longer_name_is_not_eaten_by_a_shorter_one():
    answer = humanize("Radio_Power_Event 를 봤습니다.")

    assert answer == "무선 전원 이벤트 를 봤습니다."


def test_event_type_labels_are_replaced_but_their_log_anchor_is_not():
    # SYSTEM_KILL 은 우리가 붙인 분류 이름이고, am_kill 은 단말이 남긴 문자열이다.
    answer = humanize("am_kill 강제 종료가 SYSTEM_KILL 로 분류되었습니다.")

    assert "SYSTEM_KILL" not in answer
    assert "am_kill" in answer, "로그 원문 앵커까지 지워졌다"


def test_a_summary_label_is_not_eaten_by_its_shorter_prefix():
    # SYSTEM_WTF 가 먼저 걸리면 "시스템 이상 징후_SUMMARY" 가 된다.
    assert humanize("SYSTEM_WTF_SUMMARY") == "시스템 이상 징후 요약"
    assert humanize("BINDER_PROXY_LEAK_SUMMARY") == "바인더 프록시 누수 요약"
    assert humanize("BINDER_PROXY_LEAK_RCA") == "바인더 프록시 누수 원인 분석"


def test_what_the_device_wrote_is_left_alone():
    original = (
        "am_kill 사유는 'Too many Binders sent to SYSTEM' 이고, "
        "THREAD_EXHAUSTION 이 2512ms, GET_CELL_INFO_LIST 는 ERROR 63 입니다."
    )

    assert humanize(original) == original
