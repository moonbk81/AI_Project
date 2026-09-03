import pandas as pd

from core.charts import build_app_block_windows
from parsers.network_ts_analyzer import NetworkTimeSeriesAnalyzer
from rag_builders.network_builder import build_network_timeseries_payloads

# 실제 dumpstate 시퀀스. 순서가 뒤섞여 있는 원본 그대로를 유지한다.
# [FRZ] 줄만 시각 형식이 다르다(`[09/01 ...]`) — 원문 그대로 두는 게 핵심이다.
FREEZE_SEQUENCE = [
    "09-01 16:12:42.171  1000  2761  6294 D ConnectivityService: Returning BLOCKED NetworkInfo to uid=10297",
    "09-01 16:12:43.961  1000  2761  4040 I am_freeze: [22536,com.google.android.youtube]",
    "09-01 16:12:43.967  1000  2761  4272 D InetDiagMessage: Destroyed live tcp sockets for uids={10297} in 4ms",
    "[09/01 16:12:43.962] [FRZ] [CAO 22536(10297)]",
    "[09/01 16:12:45.973] [FRZ] [Bg com.google.android.youtube 10297]",
    "09-01 16:13:44.766  1000  2761  4040 I am_unfreeze: [22536,com.google.android.youtube,27]",
    "09-01 16:13:44.849  1000  2761  4789 D ConnectivityService: Returning UNBLOCKED NetworkInfo to uid=10297",
    "09-01 16:13:44.852 10297 22536 22536 I wm_on_restart_called: [32205519,com.google.android.youtube.app.honeycomb.Shell$HomeActivity,performRestart,4]",
    "09-01 16:13:44.907 10297 22536 22536 I wm_on_start_called: [32205519,com.google.android.youtube.app.honeycomb.Shell$HomeActivity,handleStartActivity,53]",
    "09-01 16:13:44.928 10297 22536 22536 I wm_on_resume_called: [32205519,com.google.android.youtube.app.honeycomb.Shell$HomeActivity,RESUME_ACTIVITY,1]",
]

BLOCKED_DNS_LINE = (
    "09-01 16:12:50.100  1000  2761  6294 D NetdEventListenerService: "
    "DNS Requested by 111, 10297, 4(FAIL), isBlocked=true, 0ms"
)


def analyze(lines):
    return NetworkTimeSeriesAnalyzer().analyze(lines)


def test_freeze_sequence_builds_one_block_window():
    windows = analyze(FREEZE_SEQUENCE)["app_block_windows"]

    assert len(windows) == 1
    win = windows[0]
    assert win["uid"] == "10297"
    assert win["package"] == "com.google.android.youtube"
    assert win["pid"] == "22536"
    assert win["blocked_at"] == "09-01 16:12:42.171"
    assert win["unblocked_at"] == "09-01 16:13:44.849"
    assert win["freeze_at"] == "09-01 16:12:43.961"
    assert win["unfreeze_at"] == "09-01 16:13:44.766"
    assert win["unfreeze_reason_code"] == "27"
    assert win["freeze_reason"] == "Bg"
    assert win["sockets_destroyed_at"] == "09-01 16:12:43.967"
    assert win["resumed_at"] == "09-01 16:13:44.928"
    assert win["resumed_component"].endswith("Shell$HomeActivity")
    assert win["cause"] == "APP_BACKGROUND_FREEZE"
    assert win["app_frozen"] is True
    assert win["is_recovered"] is True
    assert round(win["duration_sec"]) == 63


def test_frz_line_supplies_package_name_without_pkg_list():
    """pkg CSV 목록이 없어도 [FRZ] 라인만으로 App_UID_ 대신 실제 앱 이름이 나온다."""
    issues = analyze(FREEZE_SEQUENCE + [BLOCKED_DNS_LINE])["dns_issues"]

    assert issues[0]["package"] == "com.google.android.youtube"


def test_dns_block_inside_freeze_window_is_attributed_to_app_background():
    issues = analyze(FREEZE_SEQUENCE + [BLOCKED_DNS_LINE])["dns_issues"]

    assert len(issues) == 1
    issue = issues[0]
    assert issue["is_blocked"] is True
    # 예전에는 blocked_state=가 없어 TRANSITION_DELAY로 뭉개지던 자리다.
    assert issue["effective_policy"] == "APP_BACKGROUND (App Frozen)"
    assert issue["block_cause"] == "APP_BACKGROUND_FREEZE"
    assert issue["app_frozen"] is True
    assert issue["freeze_at"] == "09-01 16:12:43.961"
    assert issue["sockets_destroyed_at"] == "09-01 16:12:43.967"
    # 차트가 파이 조각으로 묶는 값이라 짧은 범주를 유지하고,
    # 서술형 근거는 block_evidence로만 나간다.
    assert issue["suspected_reason"] == "Blocked by APP_BACKGROUND (App Frozen)"
    assert "망 장애" in issue["block_evidence"]


def test_dns_block_outside_freeze_window_keeps_old_behaviour():
    late_line = BLOCKED_DNS_LINE.replace("16:12:50.100", "16:20:00.000")
    issues = analyze(FREEZE_SEQUENCE + [late_line])["dns_issues"]

    issue = issues[0]
    assert issue["effective_policy"] == "SYSTEM_POLICY"
    assert issue["app_frozen"] is False
    assert issue["block_cause"] is None
    assert issue["suspected_reason"] == "Blocked by SYSTEM_POLICY"
    assert issue["block_evidence"] is None


def test_explicit_blocked_state_policy_wins_over_freeze_guess():
    """blocked_state=가 실제 사유를 남겼으면 추정치로 덮어쓰지 않는다."""
    lines = [
        "09-01 16:12:40.000  1000  2761  6294 D NetworkPolicy: UID=10297 blocked_state={blocked=BATTERY_SAVER, effective=BATTERY_SAVER}",
    ] + FREEZE_SEQUENCE + [BLOCKED_DNS_LINE]

    issue = analyze(lines)["dns_issues"][0]
    assert issue["effective_policy"] == "BATTERY_SAVER"
    # 정책이 명확해도 프리즈 정황 자체는 근거로 계속 붙는다.
    assert issue["app_frozen"] is True


def test_freeze_without_connectivity_line_still_forms_window():
    lines = [line for line in FREEZE_SEQUENCE if "ConnectivityService" not in line]
    windows = analyze(lines)["app_block_windows"]

    assert len(windows) == 1
    win = windows[0]
    assert win["blocked_at"] == "09-01 16:12:43.961"
    assert win["unblocked_at"] is None
    assert win["is_recovered"] is True  # am_unfreeze로 구간이 닫힌다
    assert round(win["duration_sec"]) == 61


def test_parser_output_reaches_the_chart_through_the_rag_metadata():
    """파서 → RAG 메타데이터 → 차트. 셋이 다른 파일이라 필드 이름이 어긋나기 쉽다."""
    net = analyze(FREEZE_SEQUENCE + [BLOCKED_DNS_LINE])
    payloads = build_network_timeseries_payloads({"network_timeseries": net}, None, None)
    metas = [payload["metadata"] for payload in payloads]

    assert "App_Network_Block_Window" in {meta["log_type"] for meta in metas}

    chart = build_app_block_windows(pd.DataFrame(metas), year=2026)

    assert chart.status == "ok"
    assert chart.freeze_count == 1
    assert chart.longest_sec == 62.678
    window = chart.windows[0]
    assert window.package == "com.google.android.youtube"
    assert window.sockets_destroyed is True
    assert window.resumed is True
    assert (window.end_dt - window.start_dt).total_seconds() == 62.678


def test_buffers_arriving_out_of_order_still_make_one_window():
    """dumpstate 는 버퍼를 이어붙인 파일이라 줄 순서가 시간순이 아니다.

    ConnectivityService 는 시스템 버퍼, am_freeze/wm_on_* 는 이벤트 버퍼에
    담겨 통째로 뒤에 붙는다. 파일 순서를 그대로 믿으면 같은 차단 하나가
    'UID_NETWORK_BLOCK' + 'APP_BACKGROUND_FREEZE' 두 구간으로 쪼개진다.
    """
    system_buffer = [
        "09-01 16:12:42.171  1000  2761  6294 D ConnectivityService: Returning BLOCKED NetworkInfo to uid=10297",
        "09-01 16:12:43.967  1000  2761  4272 D InetDiagMessage: Destroyed live tcp sockets for uids={10297} in 4ms",
        "09-01 16:13:44.849  1000  2761  4789 D ConnectivityService: Returning UNBLOCKED NetworkInfo to uid=10297",
    ]
    events_buffer = [
        "09-01 16:12:43.961  1000  2761  4040 I am_freeze: [22536,com.google.android.youtube]",
        "09-01 16:13:44.766  1000  2761  4040 I am_unfreeze: [22536,com.google.android.youtube,27]",
        "09-01 16:13:44.928 10297 22536 22536 I wm_on_resume_called: [32205519,com.google.android.youtube.app.honeycomb.Shell$HomeActivity,RESUME_ACTIVITY,1]",
    ]
    pkg_list = ["pkg,com.google.android.youtube,10297"]

    windows = analyze(pkg_list + system_buffer + events_buffer)["app_block_windows"]

    assert len(windows) == 1
    win = windows[0]
    # Bg 줄이 없는 시퀀스라 사유는 미상, 프리즈 사실만 확정된다.
    assert win["cause"] == "APP_PROCESS_FREEZE"
    assert win["freeze_evidence"] == "am_freeze"
    assert win["blocked_at"] == "09-01 16:12:42.171"
    assert win["unblocked_at"] == "09-01 16:13:44.849"
    assert win["freeze_at"] == "09-01 16:12:43.961"
    assert win["unfreeze_at"] == "09-01 16:13:44.766"
    assert win["sockets_destroyed_at"] == "09-01 16:12:43.967"
    assert win["resumed_at"] == "09-01 16:13:44.928"


def test_a_resume_from_before_the_block_is_not_attached():
    """앞선 다른 사건의 복귀 로그가 이 구간 근거로 붙으면 시간선이 거꾸로 된다."""
    lines = [
        "pkg,com.openai.chatgpt,10399",
        "09-01 16:13:27.107 10399 24514 24514 I wm_on_resume_called: [1,com.openai.chatgpt.MainActivity,RESUME_ACTIVITY,1]",
        "09-01 16:15:26.491  1000  2761  6294 D ConnectivityService: Returning BLOCKED NetworkInfo to uid=10399",
        "09-01 16:16:00.374  1000  2761  4040 I am_freeze: [24514,com.openai.chatgpt]",
        "09-01 16:16:28.074  1000  2761  4040 I am_unfreeze: [24514,com.openai.chatgpt,19]",
    ]

    win = analyze(lines)["app_block_windows"][0]

    assert win["blocked_at"] == "09-01 16:15:26.491"
    assert win["resumed_at"] is None


def test_cao_line_proves_the_freeze_when_only_a_uid_block_is_logged():
    """`[FRZ] [CAO pid(uid)]` 는 사유는 없지만 그 pid 가 실제로 얼었다는 증거다.

    am_freeze 가 안 남는 로그에서도 이것만 있으면 '원인 미상 UID 차단' 을
    프리즈로 확정할 수 있다.
    """
    lines = [
        "09-01 16:12:42.171  1000  2761  6294 D ConnectivityService: Returning BLOCKED NetworkInfo to uid=10297",
        "[09/01 16:12:43.962] [FRZ] [CAO 22536(10297)]",
        "09-01 16:13:44.849  1000  2761  4789 D ConnectivityService: Returning UNBLOCKED NetworkInfo to uid=10297",
    ]

    win = analyze(lines)["app_block_windows"][0]

    assert win["pid"] == "22536"
    assert win["freeze_at"] == "09-01 16:12:43.962"
    assert win["freeze_evidence"] == "FRZ CAO"
    # 얼린 건 확실하지만 '왜' 는 안 남았다 — 백그라운드 전환으로 단정하지 않는다.
    assert win["cause"] == "APP_PROCESS_FREEZE"
    assert win["freeze_reason"] is None
    assert "단정할 수 없음" in win["summary"]


def test_bg_reason_wins_over_cao_even_when_it_arrives_later():
    """CAO 가 먼저 찍혀도 사유로는 더 구체적인 Bg 를 쓴다."""
    lines = [
        "pkg,com.google.android.youtube,10297",
        "09-01 16:12:42.171  1000  2761  6294 D ConnectivityService: Returning BLOCKED NetworkInfo to uid=10297",
        "[09/01 16:12:43.962] [FRZ] [CAO 22536(10297)]",
        "[09/01 16:12:45.973] [FRZ] [Bg com.google.android.youtube 10297]",
        "09-01 16:13:44.849  1000  2761  4789 D ConnectivityService: Returning UNBLOCKED NetworkInfo to uid=10297",
    ]

    assert analyze(lines)["app_block_windows"][0]["freeze_reason"] == "Bg"


def test_cao_alone_never_invents_a_window():
    """안드로이드는 네트워크 차단과 무관한 프로세스도 수시로 언다."""
    lines = [f"[09/01 16:1{i}:00.000] [FRZ] [CAO {2000 + i}(1013{i})]" for i in range(5)]

    assert analyze(lines)["app_block_windows"] == []


def test_lev_state_dumps_are_not_mistaken_for_freeze_events():
    """`[LEV] [[FRZ] 10343 ]` 는 얼어있는 uid 목록일 뿐 사건이 아니다."""
    lines = [
        "09-01 16:12:42.171  1000  2761  6294 D ConnectivityService: Returning BLOCKED NetworkInfo to uid=10343",
        "[09/01 16:12:43.000] [LEV] [[FRZ] 10343 15010343 ]",
        "[09/01 16:12:44.000] [LEV] [ [IMP] [FRZ] 10343:33 ]",
    ]

    win = analyze(lines)["app_block_windows"][0]

    assert win["cause"] == "UID_NETWORK_BLOCK"
    assert win["freeze_at"] is None
    assert win["freeze_reason"] is None


def test_an_unfreeze_from_the_previous_cycle_does_not_close_the_window():
    """앱은 한 세션에서 여러 번 얼었다 녹는다.

    차단이 시작된 직후 도착한 해동은 직전 사이클의 것이다. 그걸로 창을 닫으면
    0.06초짜리 가짜 구간이 남아 "너무 짧다"고 그래프에서 빠지고, 정작 10초 뒤의
    진짜 프리즈가 해동보다 늦게 찍힌 것처럼 보인다.
    """
    lines = [
        "pkg,viva.republica.toss,10395",
        "09-01 16:16:27.969  1000  2761  6294 D ConnectivityService: Returning BLOCKED NetworkInfo to uid=10395",
        # 직전 사이클의 해동 — 이 창과 무관하다.
        "09-01 16:16:28.030  1000  2761  4040 I am_unfreeze: [11880,viva.republica.toss,19]",
        "[09/01 16:16:37.948] [FRZ] [CAO 11880(10395)]",
        "[09/01 16:16:37.950] [FRZ] [Bg viva.republica.toss 10395]",
        "09-01 16:16:37.953  1000  2761  4272 D InetDiagMessage: Destroyed live tcp sockets for uids={10395} in 4ms",
    ]

    windows = analyze(lines)["app_block_windows"]

    assert len(windows) == 1
    win = windows[0]
    assert win["blocked_at"] == "09-01 16:16:27.969"
    assert win["freeze_at"] == "09-01 16:16:37.948"
    assert win["unfreeze_at"] is None
    # 0.061초짜리 가짜 구간이 아니라, 끝을 모르는 열린 구간이어야 한다.
    assert win["duration_sec"] is None
    assert win["is_recovered"] is False
    assert win["sockets_destroyed_at"] == "09-01 16:16:37.953"


def test_a_dns_failure_after_that_freeze_is_tied_to_the_open_window():
    """가짜 구간이면 1분 뒤 DNS 실패가 구간 밖으로 밀려 근거를 잃는다."""
    lines = [
        "pkg,viva.republica.toss,10395",
        "09-01 16:16:27.969  1000  2761  6294 D ConnectivityService: Returning BLOCKED NetworkInfo to uid=10395",
        "09-01 16:16:28.030  1000  2761  4040 I am_unfreeze: [11880,viva.republica.toss,19]",
        "[09/01 16:16:37.948] [FRZ] [CAO 11880(10395)]",
        "[09/01 16:16:37.950] [FRZ] [Bg viva.republica.toss 10395]",
        "09-01 16:17:30.983  1000  2761  6294 D NetdEventListenerService: "
        "DNS Requested by 100, 10395, 4(FAIL), isBlocked=true, 0ms",
    ]

    issue = analyze(lines)["dns_issues"][0]

    assert issue["package"] == "viva.republica.toss"
    assert issue["app_frozen"] is True
    assert issue["freeze_at"] == "09-01 16:16:37.948"


def test_a_second_freeze_cycle_gets_its_own_window():
    """앱은 한 세션에서 여러 번 얼었다 녹는다.

    해동된 창에 다음 프리즈를 계속 붙이면 두 사이클이 한 구간으로 뭉쳐서,
    두 번째 차단 동안 난 DNS 실패가 전부 구간 밖으로 밀려 근거를 잃는다.
    """
    lines = [
        "pkg,com.openai.chatgpt,10399",
        "09-01 16:15:26.491  1000  2761  6294 D ConnectivityService: Returning BLOCKED NetworkInfo to uid=10399",
        "09-01 16:16:00.374  1000  2761  4040 I am_freeze: [24514,com.openai.chatgpt]",
        "09-01 16:16:28.074  1000  2761  4040 I am_unfreeze: [24514,com.openai.chatgpt,19]",
        "09-01 16:16:37.263  1000  2761  4040 I am_freeze: [24514,com.openai.chatgpt]",
    ]

    windows = analyze(lines)["app_block_windows"]

    assert len(windows) == 2
    assert windows[0]["blocked_at"] == "09-01 16:15:26.491"
    assert windows[0]["unfreeze_at"] == "09-01 16:16:28.074"
    assert windows[1]["blocked_at"] == "09-01 16:16:37.263"
    assert windows[1]["is_recovered"] is False  # 두 번째 사이클은 안 끝났다


def test_a_dns_block_in_the_second_cycle_is_tied_to_it():
    lines = [
        "pkg,com.openai.chatgpt,10399",
        "09-01 16:16:00.374  1000  2761  4040 I am_freeze: [24514,com.openai.chatgpt]",
        "09-01 16:16:28.074  1000  2761  4040 I am_unfreeze: [24514,com.openai.chatgpt,19]",
        "09-01 16:16:37.263  1000  2761  4040 I am_freeze: [24514,com.openai.chatgpt]",
        "09-01 16:18:28.235  1000  2761  6294 D NetdEventListenerService: "
        "DNS Requested by 100, 10399, 4(FAIL), isBlocked=true, 0ms",
    ]

    issue = analyze(lines)["dns_issues"][0]

    # 첫 사이클(16:16:00~16:16:28)이 아니라 두 번째 구간에 묶여야 한다.
    assert issue["app_frozen"] is True
    assert issue["freeze_at"] == "09-01 16:16:37.263"


def test_a_bg_line_alone_never_opens_a_window():
    """`[FRZ] [Bg ...]` 에는 끝이 없다.

    이걸로 창을 열면 몇 시간 뒤에나 오는 다음 해동까지 삼켜서, 사용자가 계속
    막혀 있지도 않았는데 17시간짜리 "차단 구간" 이 만들어진다.
    """
    lines = [
        "pkg,net.gameduo.gv,10197",
        "[09/01 14:26:43.022] [FRZ] [Bg net.gameduo.gv 10197]",
        "09-01 23:59:59.000  1000  2761  4040 I am_unfreeze: [323,net.gameduo.gv,19]",
    ]

    assert analyze(lines)["app_block_windows"] == []


def test_no_freeze_logs_yields_no_windows():
    result = analyze([BLOCKED_DNS_LINE])

    assert result["app_block_windows"] == []
    assert result["dns_issues"][0]["app_frozen"] is False
