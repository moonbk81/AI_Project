# PLM 첨부 자동 처리 & 로그 추출

PLM 결함에 붙은 첨부 파일에서 분석 가능한 로그를 뽑아 분석 대기열에 넣는 경로를 설명합니다.

## 무슨 일이 일어나는가

1. 결함을 선택하면 첨부 목록을 조회합니다.
2. 빠른 경로인 **"추천 로그 자동 분석"** 은
   `POST /plm/attachments/recommended-analyze` job 하나를 만듭니다.
3. job은 압축 첨부(ZIP·7z)를 내려받고 중첩 압축까지 훑은 뒤, 사용자의 과거 수동
   선택률로 추천된 로그만 추출·분석합니다. 이 자동 선택은 다시 학습 데이터로
   사용하지 않습니다.
4. 직접 고르려면 분석할 첨부를 체크하고 **"로그 파일 찾기"** 를 누릅니다. 후보별
   추천 점수와 기본 선택을 확인·수정한 뒤 **"선택한 로그 분석"** 을 누릅니다.
   이 명시적인 사용자 선택만 다음 추천에 반영됩니다.
5. 화면은 `GET /jobs/{job_id}` 를 폴링해 진행 상황을 표시합니다.

원본 파일이 필요하면 목록의 **"다운로드" 버튼**으로 브라우저를 통해 내려받습니다.

## 구성

| 모듈 | 역할 |
|---|---|
| `core/log_archive.py` | 압축 파일 열기/목록/추출(ZIP·7z), 로그 파일 이름 패턴. 형식은 매직 바이트로 판별한다(확장자가 틀려도 열린다). 웹 프레임워크·파일시스템·네트워크 의존 없음 |
| `plm/log_pipeline.py` | 첨부 다운로드 → 추출 파이프라인. `download` 콜러블을 주입받고 진행 상황을 이벤트로 내보냄 |
| `plm/log_recommendation.py` | 로그 계열별 수동 선택 이력 저장, 추천 점수 계산. LLM이나 별도 학습 실행 불필요 |
| `backend/main.py` | `POST /plm/attachments/analyze` 에서 위 파이프라인을 job 으로 감싸고, 이벤트를 job 진행률로 옮김 |
| `backend/static/js/views/plm.js` | 버튼과 진행 표시. `followJob` 이 job 을 폴링한다 |

파이프라인이 웹 프레임워크를 모르기 때문에 브라우저 없이 테스트할 수 있습니다
(`tests/test_log_archive.py`, `tests/test_plm_log_pipeline.py`).

## 추천 이력

선택 이력은 기본적으로 `agent_state/log_selection_history.sqlite3`에 저장됩니다.
`PLM_LOG_SELECTION_DB` 환경 변수로 위치를 변경할 수 있습니다. 추천기는 사업부별로
`dumpstate`, `bugreport`, `ap_silentlog`, 패킷 캡처, companion device, 기타 로그의
수동 선택률을 집계합니다. 이력이 적을 때는 보수적인 기본 점수와 혼합하고, 후보가
있는데 추천이 하나도 남지 않으면 가장 높은 후보 하나를 안전장치로 선택합니다.

과거의 `AutoDownloadManager`처럼 서버 사용자의 Downloads 폴더에 원본을 저장하지
않습니다. 원본은 분석 job의 임시 공간에서만 처리됩니다.

## 인식되는 로그 파일 이름

`core/log_archive.py` 의 `LOG_PATTERNS` (대소문자 무시):

- `dumpstate.log`, `dumpstate.txt`, `dumpState.log`
- `dumpState_<timestamp>.log` — 예: `dumpState_1783577655961.log`
- `dumpState_<모델>_<timestamp>.log` — 예: `dumpState_S911NKSS7EZCI_202607070957.log`
- `act_dumpstate.txt`

가드:

- **중첩 압축 최대 깊이 3** (`NESTED_ARCHIVE_MAX_DEPTH`) — PLM 첨부는 압축 안에
  압축이 다시 들어있는 경우가 많고(ZIP 안의 7z 처럼 형식이 섞이기도 합니다), 로그는
  보통 안쪽에 있습니다.
- **총 추출 크기 상한 2 GiB** (`MAX_TOTAL_EXTRACT_BYTES`) — zip bomb 방지.
- 서로 다른 아카이브에 같은 이름이 있으면 안쪽 경로를 접두사로 붙여 덮어쓰기를 막습니다.

## 문제 해결

**"인식 가능한 LOG 파일을 찾지 못했습니다"**
화면에 압축 파일 안의 실제 파일 이름이 함께 표시됩니다. 그 이름이 위 패턴(dumpstate 계열)이
아니면 자동 인식 대상이 아닙니다. "검색 및 파일" 탭에서 **📂 Open** 으로 압축 내용을
펼친 뒤 원하는 파일을 직접 선택해 분석에 넣을 수 있습니다.

**목록이 비어 보임**
`📂 Open` 이 쓰는 목록은 압축 파일 **최상위** 파일만 보여줍니다. 하위 폴더나 중첩 압축
안의 파일은 자동 추출 경로에서만 다뤄집니다.

**PLM 클라이언트 연결 실패**
자동 다운로드는 PLM 클라이언트 또는 백엔드 API 중 하나가 필요합니다. 둘 다 없으면
첨부 목록까지만 표시됩니다. `plm/CONFIGURATION_GUIDE.md` 를 참고하세요.

## API

HTTP 경로:

- `POST /plm/attachments/recommended-analyze`: 전체 과정을 한 번에 수행
- `POST /plm/attachments/logs`: 첨부 안의 후보와 추천 점수를 조회
- `POST /plm/attachments/analyze`: 사용자가 고른 로그를 분석하고 수동 선택 이력을 저장

```python
from core.log_archive import (
    extract_logs_from_archive,  # {파일명: bytes} — 중첩 압축까지 훑어 로그만
    extract_file,            # 압축에서 이름 하나 꺼내기 (폴더 안에 있어도 찾음)
    list_root_contents,      # 최상위 {파일명: 크기}
    list_archive_contents,   # 중첩 포함 {표시경로: 크기}
    is_log_file,             # 이름이 로그 패턴과 맞는지
)

from plm.log_pipeline import (
    select_archive_attachments,    # 첨부 중 압축 파일만 (.zip, .7z)
    extract_logs_from_attachments, # 다운로드 → 추출, 진행 이벤트를 yield
    inspect_attachment,            # 내려받은 파일 하나가 무엇인지 판정
)
```

`extract_logs_from_attachments(files, download)` 가 내보내는 이벤트 종류는
`plm/log_pipeline.py` 상단 상수를 참고하세요 (`DOWNLOADING`, `LOGS_EXTRACTED`,
`LOG_READY`, `NO_LOGS_MATCHED` 등). 호출자는 `LOG_READY` 이벤트의 파일을 대기열에
등록하고, 나머지는 원하는 방식으로 표시하면 됩니다.
