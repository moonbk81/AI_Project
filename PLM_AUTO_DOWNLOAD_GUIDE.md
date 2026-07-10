# PLM Auto-Download & Log Extraction Pipeline

## 개요

PLM 결함 관리에서 다운로드한 파일을 자동으로 처리하는 시스템입니다.

### 주요 기능

1. **자동 다운로드** - 브라우저 다운로드 폴더에 자동 저장
2. **자동 압축 해제** - ZIP 파일에서 로그만 추출
3. **자동 분석 파이프라인** - 추출된 로그를 분석 큐에 추가
4. **큐 관리 대시보드** - 분석 대기 중인 로그 파일 모니터링

## 시스템 구조

### 핵심 모듈

- **`ui/plm_auto_download.py`** - 자동 다운로드 및 로그 추출 엔진
  - `LogFileExtractor` - 로그 파일 패턴 인식 및 추출
  - `LogAnalysisPipeline` - 분석 큐 관리
  - `AutoDownloadManager` - 자동 저장 기능
  - `PLMAutoDownloadFlow` - 전체 흐름 조율

- **`ui/plm_ui.py`** - PLM UI 통합
  - 다운로드 버튼 개선
  - 분석 큐 대시보드 추가

## 사용 방법

### 1. PLM 탭에서 파일 다운로드

1. "🔍 Quick Search" 또는 "🔍 검색 및 파일" 탭 선택
2. 검색 조건 입력 후 결함 선택
3. "📂 Load Files" 버튼으로 파일 목록 표시
4. 다운로드할 파일의 "⬇️ Download" 버튼 클릭

### 2. 자동 처리 시작

**다운로드된 파일이 표시되면:**

```
💾 Downloaded Files - Auto Processing
📥 **N file(s) ready**

📄 filename.zip (500.0 KB)
  [⬇️ Auto-Download] [💾 Save] [📂 Open]
```

**"⬇️ Auto-Download" 버튼 클릭:**

- **일반 파일** (JPG, TXT 등)
  - 자동으로 Downloads 폴더에 저장
  - ✅ "File saved to: /home/user/Downloads/filename"

- **ZIP 파일**
  - 자동으로 압축 해제
  - 로그 파일만 추출
  - ✅ "Extracted X log file(s)"
  - 추출된 파일이 분석 큐에 추가됨

### 3. 분석 큐 모니터링

"⚙️ Analysis Queue" 탭 선택:

```
📋 Analysis Queue Status
┌─────────┬─────────┬────────────┬─────────┬────────┐
│ Total   │ Pending │ Processing │ Completed │ Failed │
│ 5       │ 2       │ 1          │ 2         │ 0      │
└─────────┴─────────┴────────────┴─────────┴────────┘

📁 Queued Log Files
 # │ Status │ Filename │ Size │ Source
───┼────────┼──────────┼──────┼────────
 1 │ ⏳ Pending │ dumpstate.log │ 250.5 │ P190404
 2 │ ✅ Completed │ dumpState_SM8150_202607.log │ 1200.3 │ P190405
```

**파일 세부 정보 확인:**
- 파일명, 크기, 상태, 추출 시간 확인 가능
- 파일 내용 미리보기 (처음 2000자)

## 지원되는 로그 파일 패턴

다음 패턴의 파일이 자동으로 인식됩니다:

| 패턴 | 예시 |
|------|------|
| `dumpState_*_*.log` | `dumpState_SM8150_202607.log` |
| `dumpstate.log` | `dumpstate.log` |
| `dumpstate.txt` | `dumpstate.txt` |
| `dumpState.log` | `dumpState.log` |
| `act_dumpstate.txt` | `act_dumpstate.txt` |
| `*dumpstate*.log` | `device_dumpstate_backup.log` |
| `*dumpstate*.txt` | `quick_dumpstate_extract.txt` |

**대소문자 무시:**
- 모든 패턴은 대소문자를 구분하지 않습니다
- `DUMPSTATE.LOG`, `DumpState.log` 모두 인식

## 자동 저장 위치

### Windows
```
C:\Users\{USERNAME}\Downloads\
```

### macOS / Linux
```
~/Downloads/
```

**파일명 충돌 처리:**
- 같은 이름의 파일이 있으면 자동으로 번호 추가
- `dumpstate.log` → `dumpstate_1.log` → `dumpstate_2.log`

## 분석 파이프라인 통합

### 추가된 로그 자동 처리

분석 큐에 추가된 로그 파일은:

1. **상태 추적** - pending → processing → completed
2. **DB 저장** - 추출된 메타데이터와 함께 저장
3. **자동 분석** - 시스템이 자동으로 분석 시작

### 프로그래밍 인터페이스

```python
from ui.plm_auto_download import LogAnalysisPipeline

# 로그 파일을 분석 큐에 추가
LogAnalysisPipeline.add_log_to_queue(
    filename="dumpstate.log",
    content=file_bytes,
    source_defect="P190404-00007"
)

# 큐 상태 조회
status = LogAnalysisPipeline.get_queue_status()
print(f"Pending: {status['pending']}")

# 큐 비우기
LogAnalysisPipeline.clear_queue()
```

## 문제 해결

### ZIP 파일이 열리지 않음
```
Error: Failed to list ZIP or ZIP is empty
```
**해결:**
- ZIP 파일이 손상되었을 가능성
- "💾 Save" 버튼으로 수동 저장 후 확인

### 로그 파일이 추출되지 않음
```
⚠️ No log files found in ZIP
```
**원인:**
- 로그 파일 이름이 지원 패턴과 맞지 않음
- ZIP 내부 디렉토리 구조 확인

**해결:**
- ZIP의 "📂 Open" 버튼으로 파일 목록 확인
- 패턴 추가 필요시 `plm_auto_download.py` 수정

### 다운로드 폴더를 찾을 수 없음
```
Error: Could not find Downloads folder
```
**해결:**
- Downloads 폴더가 없으면 생성됨
- 경로: `~/.local/share/` 등의 기본 위치 확인

## 성능 특성

| 작업 | 시간 |
|------|------|
| ZIP 열기 (메타데이터만) | < 0.5초 |
| 로그 추출 | < 1초 |
| 분석 큐 추가 | < 0.1초 |
| Downloads 폴더 저장 | < 1초 |

**메모리 효율:**
- ZIP 메타데이터만 메모리에 로드 (전체 파일 X)
- 파일은 필요할 때만 추출

## API 참조

### LogFileExtractor

```python
# 파일이 로그 패턴과 일치하는지 확인
is_log = LogFileExtractor.is_log_file("dumpstate.log")

# ZIP에서 로그 파일만 추출
logs = LogFileExtractor.extract_logs_from_zip(zip_data)
# returns: {"dumpstate.log": b"content..."}

# ZIP에서 특정 파일 추출
content = LogFileExtractor.extract_single_log(zip_data, "dumpstate.log")
```

### LogAnalysisPipeline

```python
# 로그를 분석 큐에 추가
success = LogAnalysisPipeline.add_log_to_queue(
    filename="dumpstate.log",
    content=b"...",
    source_defect="P190404"
)

# 큐 상태 조회
status = LogAnalysisPipeline.get_queue_status()
# returns: {
#   'total': 5,
#   'pending': 2,
#   'processing': 1,
#   'completed': 2,
#   'failed': 0,
#   'queue': [...]
# }

# 큐 비우기
LogAnalysisPipeline.clear_queue()
```

### AutoDownloadManager

```python
# Downloads 폴더 경로 조회
path = AutoDownloadManager.get_downloads_folder()

# 파일 자동 저장
success, result = AutoDownloadManager.save_to_downloads(
    filename="dumpstate.log",
    content=b"..."
)
# returns: (True, "/home/user/Downloads/dumpstate.log")
# or: (False, "Error message")
```

### PLMAutoDownloadFlow

```python
# 전체 처리 흐름 실행
result = PLMAutoDownloadFlow.process_downloaded_file(
    filename="archive.zip",
    file_content=zip_bytes,
    source_defect="P190404-00007",
    auto_save=True,           # Downloads 폴더에 저장
    auto_extract_logs=True    # ZIP에서 로그 추출
)
# returns: {
#   'filename': 'archive.zip',
#   'success': True,
#   'saved_path': '/home/user/Downloads/archive.zip',
#   'is_zip': True,
#   'extracted_logs': ['dumpstate.log', 'dumpState_SM8150.log'],
#   'messages': ['✅ dumpstate.log added to analysis queue', ...]
# }
```

## 확장 방법

### 새로운 로그 패턴 추가

`ui/plm_auto_download.py`의 `LogFileExtractor` 클래스:

```python
LOG_PATTERNS = [
    # ... 기존 패턴들 ...
    r'^my_custom_log\.txt$',      # 새로운 패턴 추가
]
```

### 커스텀 처리 로직 추가

```python
result = PLMAutoDownloadFlow.process_downloaded_file(
    filename="custom.zip",
    file_content=zip_bytes,
)

# 처리 결과에 따라 추가 작업
if result['extracted_logs']:
    for log_name in result['extracted_logs']:
        # 커스텀 처리 로직
        process_log_further(log_name)
```

## 버전 정보

- **작성일**: 2026-07-10
- **상태**: 프로덕션 준비 완료
- **의존성**: Streamlit, Python 3.8+
