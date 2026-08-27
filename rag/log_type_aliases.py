"""log_type 별 한글 증상 어휘. 임베딩 문서 앞에 붙인다.

문제: 문서 본문의 표현이 유형마다 딴판이었다. Wakelock_Stat 은 "배터리를 강제
소모시켰습니다" 같은 자연스러운 한글 서술문인데, Thermal_Stat 에는 "발열" 이라는
단어 자체가 없었고("SUBBAT 센서의 온도가 0.0도"), Crash_Event/Internet_Stall_Analysis
는 ``- key: value`` 키-값 덤프였다.

그 결과 한글 질의가 문서 수가 많고 한글이 풍부한 유형으로 빨려 들어갔다. 실측에서
"앱이 죽은 근본 원인이 뭐야" 의 최근접 문서가 Wakelock_Stat(2469건)이고, 정작 정답인
System_Kill_Wtf_Event(53건)는 뒤로 밀렸다. "발열이 심한데" 는 Thermal_Stat 을
exact 검색으로도 527위에서야 만났다.

그래서 유형마다 사용자가 실제로 쓰는 증상 어휘를 한 줄 붙여, 모든 유형이 자기
어휘를 갖게 한다. 값은 짧게 유지한다. 길면 이 헤더가 문서 본문의 고유 정보를
덮어써서 유형끼리 서로 닮아버린다.
"""

LOG_TYPE_ALIASES = {
    # 통화 / 음성
    "Call_Session": "통화 전화 콜 / 통화 끊김 콜드랍 / 통화 실패 / 발신 착신 수신 / 통화 종료 원인",
    "IMS_SIP_Message": "VoLTE IMS 통화 신호 / SIP 메시지 / 통화 연결 거절 응답 코드",
    "RILJ_Transaction": "모뎀 요청 응답 / RIL 명령 실패 / 문자 SMS 전송 실패 / 통화 요청 / 모뎀 에러 코드",
    # 네트워크 / 데이터
    "OOS_Event": "망 이탈 음영 지역 / 신호 없음 서비스 안됨 / 기지국 연결 끊김",
    "Signal_Level": "신호 세기 / 안테나 막대 / 신호 품질 저하",
    "Internet_Stall_Analysis": "인터넷 먹통 안됨 / 데이터 끊김 스톨 / 웹페이지 안 열림 / 데이터 느림",
    "Data_Stall_Recovery": "데이터 스톨 복구 / 인터넷 끊김 자동 복구 시도",
    "DataCall_Event": "데이터 호 연결 / 데이터 접속 / APN PDN 연결",
    "SetupDataCall_Failed": "데이터 호 연결 실패 / 데이터 접속 실패 사유 / APN 인증 실패",
    "Network_DNS_Issue": "DNS 문제 도메인 조회 실패 / 사설 DNS 차단 / 주소 못 찾음",
    "DNS_Query": "DNS 도메인 조회 / 주소 질의 응답 시간",
    "DNS_Health_Warning": "DNS 응답 지연 경고 / 도메인 조회 느림",
    "Network_Timeline_Stat": "시간대별 네트워크 상태 / 통신 품질 추이",
    "Network_Timeline_Summary": "네트워크 상태 요약 / 통신 품질 종합",
    "Data_Usage": "데이터 사용량 / 앱별 트래픽 소모",
    # 크래시 / 시스템 이상
    "Crash_Event": "앱 죽음 강제 종료 / 앱 튕김 크래시 / 예외 발생 오류",
    "Native_Crash_Event": "네이티브 크래시 / 프로세스 죽음 / SIGSEGV 비정상 종료 / tombstone",
    "ANR_Context": "앱 응답 없음 ANR / 화면 멈춤 프리징 / 터치 반응 없음",
    "System_Kill_Wtf_Event": "폰이 갑자기 죽음 / 시스템 강제 종료 / 시스템 이상 / am_kill 프로세스 종료",
    "RCA_Event": "근본 원인 분석 / 왜 죽었는지 원인 / 장애 원인 종합 판단",
    # Binder / IPC
    "Binder_Warning": "폰 화면 멈춤 프리징 / 터치 반응 없음 / 바인더 IPC 스레드 고갈 병목 / 시스템 지연",
    "Binder_Warning_Critical": "폰 먹통 멈춤 / 바인더 심각 경고 IPC 실패 / 트랜잭션 실패",
    "Binder_Context": "바인더 주변 문맥 / IPC 지연 시점 상황",
    # 전력 / 발열
    "Battery_Drain_Report": "배터리 소모 / 배터리 빨리 닳음 / 방전 충전 상태",
    "Thermal_Stat": "발열 뜨거움 / 단말 온도 상승 / 쓰로틀링 성능 저하",
    "Wakelock_Stat": "배터리 점유 웨이크락 / 앱이 잠들지 못하게 함 / 대기 전력 소모",
    "Cpu_Usage_Stat": "CPU 점유율 / 프로세스 부하 / 폰 느려짐 렉 버벅임",
    "Radio_Power_Event": "라디오 전원 / 비행기 모드 / 모뎀 켜짐 꺼짐",
    # 단말 상태 / 기타
    "Boot_Stat": "부팅 재부팅 리부팅 / 단말 시작 시간",
    "Build_Info": "단말 모델 정보 / 빌드 버전 / 커널 모뎀 버전",
    "System_Property": "시스템 속성 설정값 / 단말 설정 상태",
    "Device_Property_State": "단말 속성 상태 / 설정 변경 이력",
    "Nitz_Time_Event": "시간 자동 설정 / 시각 보정 시간대 / 타임존 변경",
}


def alias_line(log_type):
    """문서 앞에 붙일 한 줄. 매핑이 없으면 빈 문자열."""
    alias = LOG_TYPE_ALIASES.get(str(log_type or "").strip())
    return f"[관련 증상] {alias}" if alias else ""


def with_alias_header(document, log_type):
    """문서 본문 앞에 증상 어휘 줄을 덧붙인다. 이미 붙어 있으면 그대로 둔다."""
    header = alias_line(log_type)
    if not header:
        return document

    text = document or ""
    if text.startswith("[관련 증상]") or "\n[관련 증상]" in text[:200]:
        return text

    # "### [Type: X]" 헤더가 있으면 그 바로 아래에 넣어 기존 형식을 유지한다.
    if text.startswith("### "):
        first_newline = text.find("\n")
        if first_newline != -1:
            return f"{text[:first_newline]}\n{header}{text[first_newline:]}"
        return f"{text}\n{header}"

    return f"{header}\n{text}"
