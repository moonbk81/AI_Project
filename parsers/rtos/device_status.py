"""워치 nsh 명령(`ps`, `free`, `uptime`) 출력을 구조화한다. 실시간 단말 모니터가 몇 초마다 부른다.

`ps` (2026-09-29 실제 단말):
  PID GROUP PRI POLICY   TYPE    NPX STATE    EVENT     SIGMASK            STACK    USED FILLED    CPU COMMAND
    2     0 100 RR       Kthread   - Waiting  Semaphore 0000000000000000 0001888 0001660  87.9%!  0.0% lpwork 0x1824c280
  158   158 100 RR       Task      - Ready              0000000000000000 0005968 0003444  57.7%  18.7% dbusdaemon --system
- EVENT 는 비어 있거나(Ready) 두 단어일 수 있다 (`MQ empty`, `Mutex:-1`)
- FILLED 뒤 `!` 는 NuttX 가 스택 위험으로 표시한 것 (80% 이상)
- COMMAND 뒤에 인자와 주소가 붙는다 → 첫 단어를 이름으로 쓴다
`free`: `total used free maxused maxfree nused nfree name` 헤더 다음 줄마다 힙 하나 (`Umem`)
`uptime`: `00:17:45 up  0:07, load average: 1.00, 1.00, 1.00`
"""

import re
from typing import Dict, List, Optional

PS_ROW_RE = re.compile(
    r'^\s*(\d+)\s+(\d+)\s+(\d+)\s+(\w+)\s+(\w+)\s+(\S+)\s+(\w+)\s+(.*?)\s*'
    r'([0-9A-Fa-f]{16})\s+(\d+)\s+(\d+)\s+([\d.]+)%(!?)\s+([\d.]+)%\s+(.*)$'
)
UPTIME_RE = re.compile(r'up\s+(?:(\d+) days?,\s*)?(\d+):(\d+)')
LOAD_RE = re.compile(r'load average:\s*([\d.]+),\s*([\d.]+),\s*([\d.]+)')

IDLE_PID = 0
# ps 가 스택에 `!` 를 붙이는 기준과 같게 둔다
STACK_WARN_PCT = 80.0


def parse_ps(text: str) -> List[Dict]:
    tasks = []
    for line in (text or "").splitlines():
        m = PS_ROW_RE.match(line)
        if not m:
            continue
        (pid, group, pri, policy, ttype, _npx, state, event, _sigmask,
         stack, used, filled, bang, cpu, command) = m.groups()
        tasks.append({
            "pid": int(pid), "group": int(group), "pri": int(pri), "policy": policy, "type": ttype,
            "state": state, "event": event.strip() or None,
            "stack": int(stack), "stack_used": int(used), "stack_filled": float(filled),
            "stack_warn": bool(bang) or float(filled) >= STACK_WARN_PCT,
            "cpu": float(cpu), "name": command.split()[0] if command.split() else "", "command": command.strip(),
        })
    return tasks


def cpu_total(tasks: List[Dict]) -> Optional[float]:
    """전체 점유율 = 100 - Idle_Task. Idle 줄이 없으면 모른다."""
    idle = next((t for t in tasks if t["pid"] == IDLE_PID), None)
    return round(max(0.0, 100.0 - idle["cpu"]), 1) if idle else None


def parse_free(text: str) -> List[Dict]:
    heaps = []
    for line in (text or "").splitlines():
        parts = line.split()
        if len(parts) >= 8 and all(p.isdigit() for p in parts[:7]):
            total, used, free, maxused, maxfree, nused, nfree = (int(p) for p in parts[:7])
            heaps.append({
                "name": " ".join(parts[7:]), "total": total, "used": used, "free": free,
                "max_used": maxused, "max_free": maxfree, "nused": nused, "nfree": nfree,
                "used_pct": round(used * 100.0 / total, 1) if total else None,
                "max_used_pct": round(maxused * 100.0 / total, 1) if total else None,
            })
    return heaps


def parse_uptime(text: str) -> Dict:
    text = (text or "").strip()
    out = {"raw": text, "uptime_min": None, "load": None}
    m = UPTIME_RE.search(text)
    if m:
        days, hours, minutes = int(m.group(1) or 0), int(m.group(2)), int(m.group(3))
        out["uptime_min"] = days * 1440 + hours * 60 + minutes
    load = LOAD_RE.search(text)
    if load:
        out["load"] = [float(v) for v in load.groups()]
    return out
