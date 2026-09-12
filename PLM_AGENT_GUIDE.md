# 독립 실행형 PLM 분석 에이전트

ChatGPT/Codex 계정이나 브라우저 없이 실행합니다. 에이전트는 Python 표준
라이브러리만 사용하고, 현재 프로젝트의 FastAPI 백엔드가 PLM 연동과
Ollama/vLLM 기반 RAG 분석을 담당합니다. 에이전트와 백엔드는 같은 서버 또는
서로 통신 가능한 별도 서버에서 실행할 수 있습니다. 백엔드는 사내 PLM 및
모델 서버에 접근할 수 있어야 합니다. Linux/macOS, Python 3.9 이상을 지원합니다.

## 설정

프로젝트 루트에서 `conda`의 `ai` 환경을 활성화합니다. 아래의 백엔드·에이전트·
테스트 명령은 모두 이 환경에서 실행합니다.

```bash
conda activate ai
cp plm/agent.example.json plm_agent.json
```

`plm_agent.json`에서 다음을 지정합니다. 개인 설정 파일은 Git에서 제외됩니다.

| 항목 | 의미 |
|---|---|
| `backend_url` | 실행 중인 이 프로젝트 백엔드 주소, 예: `http://127.0.0.1:8080` |
| `knox_id` | 분석 소유자 및 코멘트 작성자 Knox ID |
| `main_owner_id` | 검색할 담당자 |
| `division_code`, `status`, `search_type` | 기존 PLM 빠른 검색과 동일한 조건 |
| `mode` | `draft`: 초안 저장 / `auto`: 분석 후 자동 등록 |
| `local_test` | 백엔드의 PLM 로컬 테스트 모드와 반드시 일치 |
| `system_code` | PLM 코멘트 등록에 사용하는 시스템 코드 |
| `state_dir` | SQLite 처리 이력·초안 위치. 설정 파일 기준 상대 경로 |
| `schedule.enabled` | `true`일 때 `watch` 실행 가능 |
| `schedule.time` | 현지 시간 `HH:MM`, 예시값 `09:00` |
| `schedule.timezone` | 기본 `Asia/Seoul` |
| `schedule.weekdays` | 월요일 `0`부터 일요일 `6`, 예시는 평일 |

예시 설정은 **작성자·담당자 미지정, 예약 비활성, 초안 모드**입니다.
아직 실제 운영 시간을 정한 것이 아닙니다. PLM 인증/접속과 모델 설정은
기존 백엔드의 설정을 사용합니다. HTTP 프록시는 에이전트에서 사용하지 않습니다.

## 실행

기존 분석 환경에서 백엔드를 실행합니다. Ollama/vLLM 연결은 README의 기존
`RAG_LLM_*` 설정을 사용합니다.

```bash
python -m uvicorn backend.main:app --host 0.0.0.0 --port 8080
```

별도 터미널/서비스에서 에이전트를 실행합니다.

```bash
# 설정만 검사: 네트워크·PLM 호출 없음
python3 -m plm_agent --config plm_agent.json check

# 한 번 검색하고 초안 생성 (mode=draft)
python3 -m plm_agent --config plm_agent.json once

# 실행 이력, entry_id 확인
python3 -m plm_agent --config plm_agent.json history

# 지정 시간에 반복 실행 (schedule.enabled=true 필요)
python3 -m plm_agent --config plm_agent.json watch
```

`watch`는 계속 실행되는 프로세스입니다. 운영 서버에서는 기존 서비스 관리자로
백엔드와 함께 관리하고, 작업 디렉터리를 프로젝트 루트로 지정하세요. 이 구현은
OS 서비스 설치나 서버 재부팅 후 자동 시작 설정을 변경하지 않습니다.
설정 변경 후에는 프로세스를 재시작합니다. 종료는 Ctrl+C입니다.

예약 요일에 지정 시간이 지났다면 그날 한 번 보충 실행하며, 지난 날짜까지
소급하지 않습니다. 예약 처리 여부는 SQLite에 저장됩니다. 완료한 예약 슬롯은
재시작해도 반복하지 않습니다. 실패 건은 다음 예약이나 수동 `once`에서 다시
확인합니다. `once`는 실패가 하나라도 있으면 종료 코드 1, 성공이면 0입니다.
`watch` 실패는 표준 로그에 남습니다. 별도 이메일/메신저 알림은 없습니다.

## 코멘트 검토 및 등록

초안은 `agent_state/drafts/<entry_id>.md`, 답변·검색 근거·job 정보는
`agent_state/history.sqlite3`에 저장됩니다. Markdown 파일은 검토용 사본입니다.
파일을 편집해도 등록 내용은 바뀌지 않습니다. 등록은 DB에 저장된 분석 초안을
사용하고 기존 백엔드가 AI 분석 헤더를 붙입니다.

```bash
python3 -m plm_agent --config plm_agent.json publish <entry_id>
```

매번 자동으로 등록하려면 `mode`를 `auto`로 바꿉니다. 기존 초안도 다음 실행에서
변경 여부를 확인한 뒤 등록 대상이 됩니다. `local_test=true`는 백엔드 테스트
응답을 사용하며 실제 등록과 구분해 `simulated`로 기록합니다.

## 처리 및 복구 방식

- 웹 화면에서 사용자가 선택한 로그 이력은 기본적으로
  `agent_state/log_selection_history.sqlite3`에 저장됩니다. 후보 파일명·유형별
  수동 선택률을 다음 검색의 추천 점수에 반영하며, 에이전트가 스스로 선택한
  결과는 피드백 루프를 막기 위해 학습 통계에서 제외합니다. 저장 위치는 백엔드의
  `PLM_LOG_SELECTION_DB` 환경 변수로 변경할 수 있습니다.
- 에이전트도 웹 화면과 같은 후보 스캔을 먼저 실행해 추천 로그를 분석합니다.
  추천 로그에서 검색 근거를 얻지 못하면 전체 후보로 한 번 확대하여 재분석합니다.
- 검색 응답의 전체 결함 코드 목록을 순회합니다. 상세 목록의 99건 제한 때문에
  뒤쪽 결함이 영구히 누락되지 않게 각 결함 상세를 따로 조회합니다.
- 결함 본문·상태·첨부 메타데이터·개발자 코멘트가 같으면 재분석하지 않습니다.
  AI 코멘트는 기존 백엔드의 필터로 제외합니다. 파일 내용만 바뀌고 파일 ID·크기·
  버전·수정 시각이 모두 유지되는 변경은 감지하지 못합니다.
- 결함별 분석 파일을 지정해 질의합니다. 로그 부재, 첨부 처리 실패, 빈 답변,
  검색 근거 부재에는 자동 코멘트를 등록하지 않습니다. 검색 근거 존재가 분석
  정확성을 보장하는 것은 아니므로 실제 모델의 초안을 먼저 검토하세요.
- 분석 중 또는 등록 전 PLM 자료가 변경되면 기존 분석 등록을 중단합니다.
- 분석 대기 시간 초과 후에는 저장한 job ID로 다시 확인합니다. 백엔드가
  재시작하여 job ID가 사라진 경우는 아래 운영 제한을 참고하세요.
- 동일 `state_dir`에 두 에이전트를 동시에 실행할 수 없습니다. `history`와
  `publish`를 쓰려면 실행 중인 `watch`를 먼저 종료합니다. 동일 검색 대상에
  서로 다른 상태 디렉터리를 사용하면 중복 방지가 공유되지 않습니다.
- PLM 전송 직전에 `publishing`을 저장합니다. 응답 유실/프로세스 중단 시
  같은 결함의 추가 등록을 멈추므로 중복 코멘트를 자동 재전송하지 않습니다.
  PLM에서 `Agent run: <entry_id>`를 확인하고 실제 등록이 확인된 경우만:

```bash
python3 -m plm_agent --config plm_agent.json confirm-published <entry_id>
```

이 명령은 로컬 이력만 변경합니다. 반대로 **PLM에 등록되지 않은 것을 확인한 경우만**
`confirm-not-published <entry_id>`로 초안 상태를 복구한 뒤 `publish`를 실행합니다.
사라진 backend job 때문에 `failed`가 된 건은 `retry-analysis <entry_id>`로 저장된
job 연결을 초기화한 뒤 `once`를 실행합니다. 두 명령 모두 같은
`python3 -m plm_agent --config plm_agent.json` 접두어를 사용합니다.
이력 DB를 삭제하면 중복 방지도 사라집니다.

## 검증

에이전트 단위 테스트는 실제 PLM/LLM에 접근하지 않습니다.

```bash
python3 -m unittest tests.test_plm_agent
```

환경을 활성화하지 않은 터미널/서비스에서는 다음처럼 실행할 수 있습니다.

```bash
conda run --no-capture-output -n ai python -m plm_agent --config plm_agent.json watch
conda run --no-capture-output -n ai python -m pytest tests/test_plm_agent.py tests/test_plm_log_pipeline.py tests/test_plm_api.py -q
```

2026-09-12 로컬 통합 검증: `conda ai`, Ollama `gemma3:12b`, PLM 로컬 테스트
모드에서 실제 샘플 다운로드·파싱·임베딩·초안 저장, 모의 등록, 재실행 중복 방지,
첨부 없는 결함 건너뛰기를 확인했습니다. 이는 실행 경로 검증이며 분석 정확도나
사내 PLM 접속 검증은 아닙니다. IMS 샘플 회귀 테스트는
`tests/test_plm_local_sample.py`에 있습니다.

실제 백엔드·모델을 포함한 오프라인 확인에는 백엔드를 `PLM_LOCAL_TEST=1`로
실행하고, 에이전트 설정도 `local_test=true`, `mode=draft`로 맞춥니다.
이는 샘플 PLM을 쓰지만 로그 분석용 모델과 임베딩 환경은 필요합니다.
