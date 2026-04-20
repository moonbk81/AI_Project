# telephony_constants.py

"""
벤더 RIL 및 Telephony 도메인 지식 (Mapping Tables)
이 파일에는 에러 코드, 상태 값 등을 사람이 읽을 수 있는 텍스트로 변환하는 딕셔너리를 관리합니다.
"""

# 통화 종료 및 실패 원인 (3GPP Disconnect Cause & Android Fail Causes)
CALL_FAIL_REASON_MAP = {
    "1": "UNASSIGNED_NUMBER (결번)",
    "16": "NORMAL_CLEARING (정상 종료)",
    "17": "USER_BUSY (상대방 통화 중)",
    "18": "NO_USER_RESPONDING (상대방 무응답)",
    "19": "NO_ANSWER_FROM_USER (사용자 응답 없음)",
    "21": "CALL_REJECTED (통화 거절됨)",
    "27": "DESTINATION_OUT_OF_ORDER (목적지 네트워크 장애)",
    "28": "INVALID_NUMBER_FORMAT (잘못된 번호 형식)",
    "31": "NORMAL_UNSPECIFIED (일반적인 네트워크 단절 / Drop)",
    "34": "NO_CIRCUIT_CHANNEL_AVAILABLE (기지국 채널 자원 부족)",
    "38": "NETWORK_OUT_OF_ORDER (네트워크 장애)",
    "41": "TEMPORARY_FAILURE (일시적 실패)",
    "44": "REQUESTED_CIRCUIT_CHANNEL_NOT_AVAILABLE (요청 채널 사용 불가)",
    "65": "BEARER_NOT_IMPLEMENTED (네트워크 서비스 미지원)",
    "68": "ACM_LIMIT_EXCEEDED (과금 한도 초과)",
    "127": "INTERWORKING_UNSPECIFIED",
    "65535": "ERROR_UNSPECIFIED (알수 없는 에러)"
    # TODO: 추후 벤더별 특정 RIL 에러 코드가 발견되면 여기에 계속 추가
}

VENDER_FAIL_REASON_MAP = {
    "0": "OFFLINE",
    "21": "NO_SERVICE",
    "22": "FADE",
    "25": "RELEASE_NORMAL",
    "40": "REJECTED BY BS",
    "48": "REDI_OR_HANDOFF",
}

# (참고) 나중에 RAT 타입이나 Data 규격 매핑도 이 파일에 추가하시면 됩니다.
RAT_TYPE_MAP = {
    "3": "UMTS",
    "13": "LTE",
    "16": "GSM",
    "19": "LTE_CA",
    "20": "5G (NR)",
    "-2": "Unknown (망 통합 합산)"
}