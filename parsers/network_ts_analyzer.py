import datetime
import re
from collections import defaultdict
from parsers.base import BaseParser

class NetworkTimeSeriesAnalyzer(BaseParser):
    def __init__(self, context_getter=None):
        super().__init__(context_getter)

        self.stats_start = re.compile(r'network statistics:', re.I)
        self.stats_end = re.compile(r'packet wakeup events:', re.I)
        # 1. 상세 DNS 차단 로그 패턴 (isBlocked)
        self.re_time = re.compile(r'\d{2}-\d{2}\s\d{2}:\d{2}:\d{2}\.\d{3}')
        self.re_tag = re.compile(r'[VDIWE]\s+([a-zA-Z0-9_\-]+)\s*(?=:)', re.I)
        self.re_dns_event = re.compile(r'DNS\s+requested\s+by\s+(\d+),\s+(\d+)(?:\(([^)]*)\))?,\s+(\d+)\(([^)]*)\),\s+isBlocked=(\w+)', re.I)

        self.re_uid_state = re.compile(
            r'UID=(?P<uid>\d+).*?blocked_state=\{blocked=(?P<blocked>[^,]+).*?effective=(?P<effective>[^}]+)\}', re.I)

        # 2. NetId별 성능 통계 패턴 (NetStats)
        self.re_net_perf = re.compile(
            r'(?:^|,\s*)'  # 시작 또는 콤마로 구분
            r'(?:((?:\d{2}:){2}\d{2}\.\d{3}):\s*)?'  # 시간 (선택적)
            r'\{netId=(\d+),\s*transports=\{(.*?)\},\s*'
            r'dns\s+avg=(\d+)ms\s+max=(\d+)ms\s+err=(\d+(?:\.\d+)?)%\s+tot=(\d+),\s*'
            r'delayed\s+rsp=(\d+),\s*(?:blocked\s+rsp=(\d+),\s*)?'
            r'connect\s+avg=(\d+)ms\s+max=(\d+)ms\s+err=(\d+(?:\.\d+)?)%\s+tot=(\d+),\s*'
            r'tcp\s+avg_loss=(\d+(?:\.\d+)?)%', re.I
        )

        # 3. Active default network 패턴
        self.re_active_network = re.compile(r'Active\s+default\s+network:\s*(\d+)', re.I)

        # 4. 앱 프리즈로 인한 UID 단위 네트워크 차단 패턴
        #
        # "인터넷 느려요/안돼요" 신고 중 상당수는 망 장애가 아니라, 백그라운드로
        # 내려간 앱이 얼려지면서(Freeze) UID 통째로 차단된 경우다. 이 정황은 한
        # 줄이 아니라 아래 여섯 종류의 로그가 한 세트로 만든다:
        #   ConnectivityService BLOCKED  → UID 차단 시작
        #   am_freeze / [FRZ] [Bg ...]   → 누가, 왜 얼었는지 (패키지·UID·사유)
        #   InetDiagMessage Destroyed    → 살아있던 TCP 소켓까지 끊김
        #   am_unfreeze                  → 해동
        #   ConnectivityService UNBLOCKED→ UID 차단 해제
        #   wm_on_resume_called          → 앱이 포그라운드로 복귀
        # blocked_state= 가 안 남는 로그에서도 이 체인만 있으면 차단 사유를 단정할 수 있다.
        self.re_ts_parts = re.compile(r'(\d{2})-(\d{2})\s+(\d{2}):(\d{2}):(\d{2})\.(\d{3})')
        self.re_conn_block = re.compile(
            r'Returning\s+(BLOCKED|UNBLOCKED)\s+NetworkInfo\s+to\s+uid=(\d+)', re.I)
        self.re_am_freeze = re.compile(r'\bam_freeze\s*:\s*\[(\d+),([^,\]]+)')
        self.re_am_unfreeze = re.compile(r'\bam_unfreeze\s*:\s*\[(\d+),([^,\]]+)(?:,([^\],]*))?')
        # [FRZ] 줄만 시각 형식이 다르다: 다른 줄은 `09-01 16:12:43.962` 인데
        # 여기는 `[09/01 16:12:43.962]` 로 대괄호에 슬래시다. 같은 줄로 취급하려고
        # 파서 표준형(MM-DD HH:MM:SS.mmm)으로 바꿔 담는다.
        self.re_frz_line = re.compile(
            r'^\[(\d{2})/(\d{2})\s+(\d{2}:\d{2}:\d{2}\.\d{3})\]\s*\[FRZ\]\s*\[(.+?)\]\s*$')
        # 두 가지 형태가 있고 주는 정보가 다르다.
        #   Bg <package> <uid>  : 백그라운드 전환이라는 '사유' + 패키지↔uid
        #   CAO <pid>(<uid>)    : 사유 없이 pid 단위 프리즈 실행 기록
        # 시스템 앱은 CAO 만 남는다 — 백그라운드로 내려간 게 아니라 캐시된
        # 프로세스로 얼린 것이라 Bg 사유가 애초에 없다.
        self.re_frz_bg = re.compile(r'^Bg\s+([A-Za-z0-9_.]+)\s+(\d+)$')
        self.re_frz_cao = re.compile(r'^CAO\s+(\d+)\((\d+)\)$')
        self.re_socket_destroy = re.compile(
            r'Destroyed\s+live\s+tcp\s+sockets\s+for\s+uids=\{([^}]*)\}', re.I)
        self.re_wm_resume = re.compile(r'\bwm_on_resume_called\s*:\s*\[\d+,([^,\]]+)')

    # [FRZ] 태그가 남기는 사유 토큰. 확실한 것만 한글로 풀고 나머지는 원문 유지.
    FREEZE_REASON_TEXT = {"Bg": "백그라운드 전환"}

    # 이 값들은 "차단 사유를 모르겠다"는 뜻이라, 프리즈 근거가 있으면 덮어쓴다.
    UNRESOLVED_POLICIES = {"NONE", "SYSTEM_POLICY", "TRANSITION_DELAY", ""}

    def analyze(self, lines):
        in_stats = False
        dns_issues = []
        uid_block_map = {} # UID별 상세 차단 원인 저장소
        uid_map = {}       # 🚨 신규: UID별 실제 패키지명 매핑 딕셔너리

        # 시계열 분석을 위해 시간(Time)을 키로 사용하는 딕셔너리
        timeline = defaultdict(lambda: {"net_stats": []})
        last_perf_ts = ''  # 💡 동일 라인 내 후속 netId 블록을 위한 timestamp 상속용

        current_netid = None
        private_dns_status = {}
        active_network_id = None
        active_network_type = None

        lifecycle_events = []  # 프리즈/차단 라이프사이클 이벤트 (발생 순서 유지)
        frz_uid_map = {}       # [FRZ] 라인이 알려준 uid → package (pkg 리스트 없을 때의 구원투수)
        frz_reasons = {}       # [FRZ] 라인이 알려준 uid → 프리즈 사유(Bg 등)

        # 1단계: UID별 blocked_state 및 패키지명 정보 사전 수집
        for line in lines:
            clean_line = self.clean_line(line)

            self._collect_lifecycle_event(clean_line, lifecycle_events, frz_uid_map, frz_reasons)

            # (A) 차단 정책 수집
            uid_m = self.re_uid_state.search(clean_line)
            if uid_m:
                uid_val = uid_m.group('uid')
                blocked_val = uid_m.group('blocked')
                effective_val = uid_m.group('effective')

                # 💡 핵심: effective가 NONE이더라도, blocked에 명확한 사유(예: APP_BACKGROUND)가
                # 있다면 그 값을 진짜 차단 원인으로 기록해 둡니다.
                if effective_val == 'NONE' and blocked_val != 'NONE':
                    uid_block_map[uid_val] = f"{blocked_val} (Delayed Unlock)"
                else:
                    uid_block_map[uid_val] = effective_val

            # 🚨 (B) 신규: pkg 리스트에서 실제 패키지 이름 긁어오기
            if clean_line.startswith("pkg,"):
                m_pkg_csv = re.match(r'^pkg,([^,]+),(\d+)', clean_line)
                if m_pkg_csv:
                    pkg_name = m_pkg_csv.group(1)
                    uid_val = m_pkg_csv.group(2)
                    uid_map[uid_val] = pkg_name

            # (C) Active default network 정보 추출
            active_m = self.re_active_network.search(clean_line)
            if active_m and active_network_id is None:
                active_network_id = active_m.group(1)

        # pkg CSV 목록이 우선이고, [FRZ]가 알려준 매핑은 빈 자리만 채운다.
        for uid_val, pkg_name in frz_uid_map.items():
            uid_map.setdefault(uid_val, pkg_name)

        app_block_windows = self._build_app_block_windows(lifecycle_events, uid_map, frz_reasons)
        block_index = self._index_block_windows(app_block_windows)

        # 2단계: 메인 분석 루프
        for line in lines:
            clean_line = self.clean_line(line)
            if "NetId:" in clean_line:
                netid_m = re.search(r'NetId:\s*(\d+)', clean_line)
                if netid_m:
                    current_netid = netid_m.group(1)
                    if current_netid not in private_dns_status:
                        private_dns_status[current_netid] = {
                            "mode": "UNKNOWN",
                            "fail_count": 0,
                            "failed_ips": []
                        }

            if current_netid:
                if "Private DNS mode:" in clean_line:
                    mode_m = re.search(r'Private DNS mode:\s*([a-zA-Z]+)', clean_line, re.I)
                    if mode_m:
                        private_dns_status[current_netid]["mode"] = mode_m.group(1).upper()

                # DoT configuration 세션 붕괴 감지
                if "status{fail}" in clean_line:
                    private_dns_status[current_netid]["fail_count"] += 1
                    ip_m = re.search(r'([a-fA-F0-9:]+|\d+\.\d+\.\d+\.\d+)\s+name', clean_line)
                    if ip_m:
                        private_dns_status[current_netid]["failed_ips"].append(ip_m.group(1))

            tag_m = self.re_tag.search(clean_line)
            tag = tag_m.group(1).strip() if tag_m else None

            if tag == "NetdEventListenerService":
                dns_m = self.re_dns_event.search(clean_line)
                if dns_m:
                    net_id, uid, pkg, res_code, res_str, blocked = dns_m.groups()
                    pkg = pkg or ''  # 💡 (app_name) 생략 시 None → 빈 문자열

                    if "FAIL" in res_str or "NODATA" in res_str or blocked.lower() == 'true':
                        is_blocked = blocked.lower() == 'true'
                        event_time = clean_line[:18]
                        effective_policy = uid_block_map.get(uid, "SYSTEM_POLICY")
                        if is_blocked and effective_policy == 'NONE':
                            effective_policy = "TRANSITION_DELAY"

                        # 🚨 신규: 1단계에서 수집한 실제 앱 이름으로 치환
                        # 로그 원본의 pkg 값이 없거나 단순 숫자일 경우 uid_map에서 찾아옵니다.
                        real_pkg = uid_map.get(uid, pkg) or f"App_UID_{uid}"
                        if real_pkg.isdigit():
                            real_pkg = f"App_UID_{real_pkg}"

                        # 💡 이 실패가 앱 프리즈 차단 구간 안에서 났다면, blocked_state=가
                        #    없어서 뭉개졌던 정책을 실제 사유(APP_BACKGROUND)로 확정한다.
                        window = self._window_at(block_index, uid, event_time)
                        if window and effective_policy in self.UNRESOLVED_POLICIES:
                            effective_policy = window["effective_policy"]

                        # suspected_reason은 차트에서 파이 조각 라벨로 묶이는 값이라
                        # 짧은 범주로 유지한다. 서술형 근거는 block_evidence로 따로 뺀다.
                        issue = {
                            "time": event_time,
                            "net_id": net_id,
                            "uid": uid,
                            "package": real_pkg, # 치환된 패키지명 사용
                            "result": res_str,
                            "is_blocked": is_blocked,
                            "suspected_reason": (
                                f"Blocked by {effective_policy}" if is_blocked else "Network Timeout/Fail"
                            ),
                            "effective_policy": effective_policy,
                            "block_evidence": window["summary"] if window else None,
                        }
                        issue.update(self._block_window_fields(window))
                        dns_issues.append(issue)

            if self.stats_start.search(clean_line):
                in_stats = True
                continue

            if in_stats:
                # 💡 동일 라인에 여러 netId 블록이 있을 때 시간이 첫 블록에만 나오므로
                #    이전 timestamp를 기억해두고 후속 블록에 재사용합니다.
                for perf_m in self.re_net_perf.finditer(clean_line):
                    groups = perf_m.groups()
                    ts = groups[0]
                    if ts:
                        last_perf_ts = ts
                    else:
                        ts = last_perf_ts
                    net_id = groups[1]
                    timeline[ts]["net_stats"].append({
                        "netId": net_id,
                        "transport": "Wi-Fi" if groups[2] == "1" else "Cellular",
                        "dns_avg": int(groups[3]),
                        "dns_max": int(groups[4]),
                        "dns_err_rate": float(groups[5]),
                        "dns_tot": int(groups[6]),
                        "dns_delayed_cnt": int(groups[7]),
                        "dns_blocked_cnt": int(groups[8]) if groups[8] is not None else 0,
                        "connect_avg": int(groups[9]),
                        "connect_max": int(groups[10]),
                        "connect_err_rate": float(groups[11]),
                        "connect_tot": int(groups[12]),
                        "tcp_avg_loss": float(groups[13])
                    })
            if self.stats_end.search(clean_line):
                in_stats = False

        # 활성 네트워크의 transport는 같은 netId의 통계 블록에서 가져온다.
        if active_network_id is not None:
            for window in timeline.values():
                match = next(
                    (stat for stat in window["net_stats"] if stat["netId"] == active_network_id),
                    None,
                )
                if match:
                    active_network_type = match["transport"]
                    break

        return {
            "sorted_timeline": dict(sorted(timeline.items())),
            "dns_issues": dns_issues,
            "app_block_windows": app_block_windows,
            "private_dns_status": private_dns_status,
            "active_network_id": active_network_id,
            "active_network_type": active_network_type,
        }

    # ==========================================
    # 앱 프리즈 → UID 네트워크 차단 구간 재구성
    # ==========================================
    def _collect_lifecycle_event(self, clean_line, events, frz_uid_map, frz_reasons):
        """한 줄에서 프리즈/차단 라이프사이클 이벤트를 뽑아 events에 쌓는다.

        메인 루프가 로그 전체를 훑는 김에 같이 처리한다. 정규식을 여섯 개나 매
        줄에 돌리면 대용량 로그에서 비싸므로, 먼저 싼 substring 검사로 거른다.
        """
        # [FRZ] 는 시각 형식이 달라서 아래 가드에 걸린다. 먼저 걷어내 표준형으로
        # 바꿔 담는다. ([LEV] [[FRZ] ...] 같은 상태 덤프는 여기서 안 걸리고 버려진다)
        if "[FRZ]" in clean_line:
            m = self.re_frz_line.match(clean_line)
            if not m:
                return
            month, day, hhmmss, payload = m.groups()
            frz_ts = f"{month}-{day} {hhmmss}"

            bg = self.re_frz_bg.match(payload)
            if bg:
                pkg_name, uid_val = bg.groups()
                frz_uid_map[uid_val] = pkg_name
                frz_reasons.setdefault(uid_val, "Bg")
                events.append({"kind": "FRZ_BG", "ts": frz_ts, "uid": uid_val,
                               "package": pkg_name})
                return

            cao = self.re_frz_cao.match(payload)
            if cao:
                pid_val, uid_val = cao.groups()
                events.append({"kind": "FRZ_CAO", "ts": frz_ts, "uid": uid_val,
                               "pid": pid_val})
            return

        ts = clean_line[:18]
        if not self.re_ts_parts.match(ts):
            return

        if "NetworkInfo to uid=" in clean_line:
            m = self.re_conn_block.search(clean_line)
            if m:
                events.append({"kind": m.group(1).upper(), "ts": ts, "uid": m.group(2)})

        elif "am_freeze" in clean_line:
            m = self.re_am_freeze.search(clean_line)
            if m:
                events.append({"kind": "FREEZE", "ts": ts, "pid": m.group(1),
                               "package": m.group(2).strip()})

        elif "am_unfreeze" in clean_line:
            m = self.re_am_unfreeze.search(clean_line)
            if m:
                events.append({"kind": "UNFREEZE", "ts": ts, "pid": m.group(1),
                               "package": m.group(2).strip(), "reason_code": m.group(3)})

        elif "Destroyed live tcp sockets" in clean_line:
            m = self.re_socket_destroy.search(clean_line)
            if m:
                uids = [u.strip() for u in m.group(1).split(',') if u.strip().isdigit()]
                if uids:
                    events.append({"kind": "SOCKETS", "ts": ts, "uids": uids})

        elif "wm_on_resume_called" in clean_line:
            m = self.re_wm_resume.search(clean_line)
            if m:
                events.append({"kind": "RESUME", "ts": ts, "component": m.group(1).strip()})

    def _build_app_block_windows(self, events, uid_map, frz_reasons=None):
        """BLOCKED/UNBLOCKED 쌍을 한 구간으로 묶고 프리즈 정황을 채워 넣는다.

        am_freeze와 wm_on_resume_called는 UID 없이 패키지명만, [FRZ]는 둘 다
        남긴다. 그래서 패키지→UID 표를 먼저 완성한 뒤 이벤트를 순서대로 훑는다.
        """
        frz_reasons = frz_reasons or {}
        pkg_to_uid = {}
        for uid_val, pkg_name in uid_map.items():
            pkg_to_uid.setdefault(pkg_name, uid_val)

        # dumpstate 는 버퍼를 여러 개 이어붙인 파일이라 줄 순서가 시간순이 아니다.
        # ConnectivityService 는 시스템 버퍼, am_freeze/wm_on_* 는 이벤트 버퍼에
        # 있어서, 파일 순서대로 읽으면 BLOCKED~UNBLOCKED 를 다 닫은 뒤에야
        # am_freeze 가 도착한다. 그러면 같은 사건이 창 두 개로 쪼개지고
        # (UID_NETWORK_BLOCK 하나 + APP_BACKGROUND_FREEZE 하나) 차단 사유를
        # 못 붙인다. 창을 만들기 전에 시간으로 줄을 세운다.
        events = sorted(events, key=lambda ev: self._ts_seconds(ev["ts"]) or 0.0)

        windows = []
        open_by_uid = {}  # 아직 UNBLOCKED를 못 만난 구간
        last_by_uid = {}  # 뒤늦게 도착하는 이벤트(해동, 복귀)를 붙일 마지막 구간

        def live_window(uid_val):
            """아직 진행 중인 창. 이미 해동된 창은 지난 사이클이라 내보낸다.

            앱은 한 세션에서 여러 번 얼었다 녹는다. 해동된 창에 다음 프리즈를
            계속 붙이면 두 사이클이 한 구간으로 뭉쳐서, 두 번째 차단 동안 난
            DNS 실패가 전부 구간 밖으로 밀려 근거를 잃는다.
            """
            win = open_by_uid.get(uid_val)
            if win is not None and win["unfreeze_at"]:
                open_by_uid.pop(uid_val, None)
                return None
            return win

        def open_window(uid_val, ts):
            win = {
                "uid": uid_val,
                "package": uid_map.get(uid_val) or f"App_UID_{uid_val}",
                "pid": None,
                "blocked_at": ts,
                "unblocked_at": None,
                "freeze_at": None,
                "unfreeze_at": None,
                "freeze_reason": None,
                "freeze_evidence": None,
                "unfreeze_reason_code": None,
                "sockets_destroyed_at": None,
                "resumed_at": None,
                "resumed_component": None,
            }
            windows.append(win)
            open_by_uid[uid_val] = win
            last_by_uid[uid_val] = win
            return win

        for ev in events:
            kind = ev["kind"]
            ts = ev["ts"]

            if kind == "BLOCKED":
                if ev["uid"] not in open_by_uid:
                    open_window(ev["uid"], ts)

            elif kind == "UNBLOCKED":
                win = open_by_uid.pop(ev["uid"], None)
                if win and not win["unblocked_at"]:
                    win["unblocked_at"] = ts

            elif kind == "FREEZE":
                uid_val = pkg_to_uid.get(ev["package"])
                if not uid_val:
                    continue
                # ConnectivityService 라인이 없어도 프리즈 자체가 차단 근거다.
                win = live_window(uid_val) or open_window(uid_val, ts)
                if not win["freeze_at"]:
                    win["freeze_at"] = ts
                if not win["pid"]:
                    win["pid"] = ev["pid"]
                win["freeze_evidence"] = "am_freeze"

            elif kind == "UNFREEZE":
                uid_val = pkg_to_uid.get(ev["package"])
                win = last_by_uid.get(uid_val) if uid_val else None
                # 얼기도 전에 온 해동은 직전 사이클의 것이다. 그걸로 창을 닫으면
                # 0.06초짜리 가짜 구간이 생기고(그러면 너무 짧다고 그래프에서
                # 빠진다), 정작 뒤에 오는 진짜 프리즈가 해동보다 늦어 보인다.
                if win and not win["unfreeze_at"] and win["freeze_at"] and ts >= win["freeze_at"]:
                    win["unfreeze_at"] = ts
                    win["unfreeze_reason_code"] = ev.get("reason_code")

            elif kind == "FRZ_BG":
                # 사유만 채우고 창은 열지 않는다.
                #
                # 이 줄로 창을 열어 보면 DNS 차단이 있는 앱을 더 덮긴 한다. 하지만
                # 이 줄에는 끝이 없어서, 열린 창이 몇 시간 뒤에야 오는 다음
                # am_unfreeze 까지 통째로 삼킨다. 실제 로그에서 webtoon 17시간,
                # gameduo 14시간짜리 "차단 구간" 이 나왔다. 사용자가 그동안 계속
                # 막혀 있던 게 아니므로 이건 근거를 만들어내는 것이다.
                # 끝이 로그에 있는 프리즈(am_freeze / FRZ CAO)만 창을 연다.
                win = live_window(ev["uid"]) or last_by_uid.get(ev["uid"])
                if win and win["freeze_reason"] in (None, "CAO"):
                    win["freeze_reason"] = "Bg"  # CAO(실행 기록)보다 구체적인 사유다

            elif kind == "FRZ_CAO":
                # 이 구간 안에서 실제로 얼었다는 증거. 창을 새로 열지는 않는다 —
                # 안드로이드는 네트워크 차단과 무관한 프로세스도 수시로 얼린다.
                win = live_window(ev["uid"])
                if win:
                    if not win["pid"]:
                        win["pid"] = ev["pid"]
                    if not win["freeze_at"]:
                        win["freeze_at"] = ts
                    if not win["freeze_evidence"]:
                        win["freeze_evidence"] = "FRZ CAO"

            elif kind == "SOCKETS":
                for uid_val in ev["uids"]:
                    win = open_by_uid.get(uid_val)
                    if win and not win["sockets_destroyed_at"]:
                        win["sockets_destroyed_at"] = ts

            elif kind == "RESUME":
                uid_val = self._uid_for_component(ev["component"], pkg_to_uid)
                win = last_by_uid.get(uid_val) if uid_val else None
                # 차단이 시작되기도 전의 복귀는 이 구간과 무관한 다른 사건이다.
                if win and not win["resumed_at"] and ts >= win["blocked_at"]:
                    win["resumed_at"] = ts
                    win["resumed_component"] = ev["component"]

        for win in windows:
            # [FRZ] 가 알려준 사유는 시각이 없으므로 uid 로만 붙인다.
            if not win["freeze_reason"]:
                win["freeze_reason"] = frz_reasons.get(win["uid"])
            self._finalize_block_window(win)
        return windows

    def _uid_for_component(self, component, pkg_to_uid):
        """`com.foo.bar.Shell$HomeActivity` 같은 컴포넌트명에서 앱 UID를 되짚는다."""
        best_pkg = None
        for pkg_name, uid_val in pkg_to_uid.items():
            if component == pkg_name or component.startswith(pkg_name + "."):
                if best_pkg is None or len(pkg_name) > len(best_pkg):
                    best_pkg = pkg_name
        return pkg_to_uid.get(best_pkg) if best_pkg else None

    def _ts_seconds(self, ts):
        """'MM-DD HH:MM:SS.mmm' -> 초. 구간 길이 계산 전용."""
        m = self.re_ts_parts.match(ts or '')
        if not m:
            return None
        month, day, hour, minute, sec, ms = (int(v) for v in m.groups())
        try:
            dt = datetime.datetime(2000, month, day, hour, minute, sec, ms * 1000)
        except ValueError:
            return None
        start = datetime.datetime(2000, 1, 1)
        return (dt - start).total_seconds()

    def _finalize_block_window(self, win):
        start = self._ts_seconds(win["blocked_at"])
        # UNBLOCKED가 안 남았으면 해동 시각으로라도 구간을 닫는다.
        end_ts = win["unblocked_at"] or win["unfreeze_at"]
        end = self._ts_seconds(end_ts)

        win["duration_sec"] = (
            round(end - start, 3) if start is not None and end is not None and end >= start else None
        )
        win["is_recovered"] = bool(end_ts)
        win["app_frozen"] = bool(win["freeze_at"])
        # 얼었다는 사실과 '왜' 얼었는지는 다른 이야기다. Bg 사유가 실제로 남은
        # 경우만 백그라운드 전환으로 단정하고, 나머지는 프리즈 사실까지만 말한다.
        # (프리즈 자체는 CAO/am_freeze 로 확실하지만 사유는 로그에 없다)
        if not win["app_frozen"]:
            win["cause"] = "UID_NETWORK_BLOCK"
            win["effective_policy"] = "UID_BLOCKED"
        elif win["freeze_reason"] == "Bg":
            win["cause"] = "APP_BACKGROUND_FREEZE"
            win["effective_policy"] = "APP_BACKGROUND (App Frozen)"
        else:
            win["cause"] = "APP_PROCESS_FREEZE"
            win["effective_policy"] = "APP_FROZEN (Reason Unlogged)"
        win["summary"] = self._describe_block_window(win)
        return win

    def _describe_block_window(self, win):
        pkg_name = win["package"]
        reason = win["freeze_reason"]
        reason_text = self.FREEZE_REASON_TEXT.get(reason, reason)

        if win["cause"] == "APP_BACKGROUND_FREEZE":
            head = (f"{pkg_name}(uid={win['uid']}) 앱이 {win['freeze_at']}에 "
                    f"{reason_text}(FRZ {reason})으로 프리즈되어 UID 단위로 네트워크가 차단됨")
        elif win["app_frozen"]:
            evidence = f", {win['freeze_evidence']} 근거" if win["freeze_evidence"] else ""
            head = (f"{pkg_name}(uid={win['uid']}) 프로세스가 {win['freeze_at']}에 "
                    f"프리즈되어 UID 단위로 네트워크가 차단됨{evidence}")
        else:
            head = f"{pkg_name}(uid={win['uid']})의 네트워크가 {win['blocked_at']}부터 UID 단위로 차단됨"

        parts = [head]
        if win["duration_sec"] is not None:
            parts.append(f"차단 구간 {win['blocked_at']} ~ {win['unblocked_at'] or win['unfreeze_at']} ({win['duration_sec']}초)")
        else:
            parts.append(f"차단 시작 {win['blocked_at']}, 해제 로그 없음(구간 종료 미확인)")
        if win["sockets_destroyed_at"]:
            parts.append(f"{win['sockets_destroyed_at']}에 살아있던 TCP 소켓 강제 종료")
        if win["unfreeze_at"]:
            code = f", reason={win['unfreeze_reason_code']}" if win["unfreeze_reason_code"] else ""
            parts.append(f"{win['unfreeze_at']} 해동(am_unfreeze{code})")
        if win["resumed_at"]:
            parts.append(f"{win['resumed_at']} 앱이 포그라운드로 복귀({win['resumed_component']})")

        if win["cause"] == "APP_BACKGROUND_FREEZE":
            parts.append(
                "단말 절전/백그라운드 정책에 의한 정상 차단 동작이며 망 장애나 DNS 서버 에러가 아님"
            )
        elif win["app_frozen"]:
            # 프리즈는 확실하지만 사유(Bg 등)가 로그에 없다. 절전 정책이라고 단정하지 않는다.
            parts.append(
                "단말이 프로세스를 얼리면서 생긴 차단이며 망 장애나 DNS 서버 에러가 아님. "
                "다만 프리즈 사유는 로그에 남지 않아 절전 정책 여부는 단정할 수 없음"
            )
        return ". ".join(parts) + "."

    def _index_block_windows(self, windows):
        """UID별 (시작초, 종료초, 구간) 목록. 종료가 없으면 열린 구간으로 둔다."""
        index = defaultdict(list)
        for win in windows:
            start = self._ts_seconds(win["blocked_at"])
            if start is None:
                continue
            end = self._ts_seconds(win["unblocked_at"] or win["unfreeze_at"])
            index[win["uid"]].append((start, end, win))
        return index

    def _window_at(self, block_index, uid, ts):
        point = self._ts_seconds(ts)
        if point is None:
            return None
        for start, end, win in block_index.get(uid, []):
            if start <= point and (end is None or point <= end):
                return win
        return None

    def _block_window_fields(self, win):
        """dns_issue에 붙일 평면 필드. Chroma 메타데이터라 중첩 dict는 피한다."""
        if not win:
            return {"app_frozen": False, "block_cause": None}
        return {
            "app_frozen": win["app_frozen"],
            "block_cause": win["cause"],
            "blocked_at": win["blocked_at"],
            "unblocked_at": win["unblocked_at"],
            "block_duration_sec": win["duration_sec"],
            "freeze_at": win["freeze_at"],
            "unfreeze_at": win["unfreeze_at"],
            "freeze_reason": win["freeze_reason"],
            "sockets_destroyed_at": win["sockets_destroyed_at"],
            "resumed_at": win["resumed_at"],
        }
