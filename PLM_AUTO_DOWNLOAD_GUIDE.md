# PLM 첨부 자동 처리 & 로그 추출

PLM 결함에 붙은 첨부 파일에서 분석 가능한 로그를 뽑아 분석 대기열에 넣는 경로를 설명합니다.

## 무슨 일이 일어나는가

1. 결함을 선택하면 첨부 목록을 조회하고, **압축 첨부(ZIP·7z)를 자동으로 내려받습니다**.
2. 압축 파일 안(중첩 포함, 형식이 섞여도 됨)에서 **로그 파일 이름 패턴에 맞는 파일만** 추출합니다.
3. 추출된 로그는 `st.session_state.plm_pending_logs` 에 쌓입니다.
4. 실제 분석과 DB 적재는 **사이드바의 "분석 및 DB 적재 시작"** 을 눌러야 시작합니다.
   결함을 고르는 동작이 긴 분석에 붙잡히지 않도록 일부러 분리해 둔 것입니다.

파일이 서버 디스크에 저장되지는 않습니다. 원본 파일이 필요하면 목록의
**"다운로드" 버튼**으로 브라우저를 통해 내려받습니다.

## 구성

| 모듈 | 역할 |
|---|---|
| `core/log_archive.py` | 압축 파일 열기/목록/추출(ZIP·7z), 로그 파일 이름 패턴. 형식은 매직 바이트로 판별한다(확장자가 틀려도 열린다). Streamlit·파일시스템·네트워크 의존 없음 |
| `plm/log_pipeline.py` | 첨부 다운로드 → 추출 파이프라인. `download` 콜러블을 주입받고 진행 상황을 이벤트로 내보냄 |
| `ui/plm_ui.py` | 이벤트를 화면 문구로 옮기고, 추출된 로그를 분석 대기열에 등록 |

파이프라인이 Streamlit 을 모르기 때문에 브라우저 없이 테스트할 수 있습니다
(`tests/test_log_archive.py`, `tests/test_plm_log_pipeline.py`).

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
