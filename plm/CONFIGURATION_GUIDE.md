# PLM Configuration Guide

## 두 가지 방식으로 설정하기

PLM API 인증 정보를 설정하는 방법은 **2가지**입니다.

### 방식 1: YAML 파일에 직접 설정 (개발/로컬 테스트)

**가장 간단한 방법입니다!**

`plm/plm_config.yaml` 파일을 열어서:

```yaml
plm:
  # Authentication
  # Option 1: Set directly in YAML (for development/local testing)
  knox_id: "your_knox_id"              # ← 여기에 당신의 Knox ID 입력
  app_id: "your_app_id"                # ← 여기에 당신의 App ID 입력

  # Option 2: Use environment variables (uncomment to use)
  # knox_id: "${PLM_KNOX_ID}"
  # app_id: "${PLM_APP_ID}"

  user_lang: "en"
```

**장점:**
- 🟢 가장 간단함
- 🟢 별도의 환경변수 설정 불필요
- 🟢 파일에서 바로 설정 완료

**단점:**
- 🔴 민감한 정보가 파일에 저장됨
- 🔴 Git에 커밋하면 안 됨 (보안)

---

### 방식 2: 환경변수 사용 (프로덕션/CI)

YAML 파일을 그대로 두고, 환경변수로 설정합니다:

```bash
# Shell에서 설정
export PLM_KNOX_ID="your_knox_id"
export PLM_APP_ID="your_app_id"
```

또는 `.env` 파일 사용:

```bash
# .env 파일
PLM_KNOX_ID=your_knox_id
PLM_APP_ID=your_app_id
```

`.env` 파일을 로드하는 코드:

```python
from dotenv import load_dotenv
load_dotenv()  # .env 파일 로드

# 이후 코드에서 자동으로 사용됨
from plm import create_plm_integration
integration = create_plm_integration()
```

`plm_config.yaml`:

```yaml
plm:
  # Use environment variables
  knox_id: "${PLM_KNOX_ID}"            # 환경변수에서 읽음
  app_id: "${PLM_APP_ID}"              # 환경변수에서 읽음
```

**장점:**
- 🟢 민감한 정보를 파일에 저장하지 않음
- 🟢 Git에 안전하게 커밋 가능
- 🟢 프로덕션 배포에 적합

**단점:**
- 🔴 환경변수 설정 필요
- 🔴 한 단계 더 필요

---

## 권장 방식

| 상황 | 추천 방식 | 이유 |
|------|---------|------|
| 로컬 개발 | **YAML 직접 설정** | 빠르고 편함 |
| 팀 협업 | **환경변수** | 보안, Git 안전 |
| 프로덕션 | **환경변수** | 보안 필수 |
| CI/CD | **환경변수** | 자동화 친화적 |

---

## 설정 방법 상세 가이드

### 방식 1: YAML 파일에서 설정하기

**Step 1: 파일 열기**

```bash
cd /home/bongki81/project/AI_Project/plm
nano plm_config.yaml
# 또는
code plm_config.yaml  # VSCode
```

**Step 2: 값 수정**

찾는 부분:
```yaml
  knox_id: "${PLM_KNOX_ID}"
  app_id: "${PLM_APP_ID}"
```

수정하기:
```yaml
  knox_id: "your_actual_knox_id"
  app_id: "your_actual_app_id"
```

**Step 3: 저장 후 테스트**

```python
from plm import create_plm_integration

integration = create_plm_integration()
print("✓ Configuration loaded successfully!")
```

---

### 방식 2: 환경변수에서 설정하기

**Option A: Shell에서 직접 설정**

```bash
# Bash / Zsh
export PLM_KNOX_ID="your_knox_id"
export PLM_APP_ID="your_app_id"

# 확인
echo $PLM_KNOX_ID
echo $PLM_APP_ID
```

**Option B: .env 파일 사용**

`.env` 파일 생성:

```bash
# .env (프로젝트 루트에)
PLM_KNOX_ID=your_knox_id
PLM_APP_ID=your_app_id
```

Python 코드에서:

```python
from dotenv import load_dotenv
import os

load_dotenv()  # .env 파일 읽기

knox_id = os.getenv('PLM_KNOX_ID')
app_id = os.getenv('PLM_APP_ID')

print(f"Knox ID: {knox_id}")
print(f"App ID: {app_id}")
```

**Option C: 파이썬 스크립트에서 설정**

```python
import os

os.environ['PLM_KNOX_ID'] = 'your_knox_id'
os.environ['PLM_APP_ID'] = 'your_app_id'

from plm import create_plm_integration
integration = create_plm_integration()
```

---

## 두 가지 방식을 섞어서 사용하기

우선순위:

1. **YAML에 직접 값이 있으면** → YAML 값 사용 ✅
2. **YAML이 ${ENV_VAR} 형식이면** → 환경변수에서 찾기 ✅
3. **환경변수도 없으면** → 에러 메시지 표시 ❌

예시:

```yaml
plm:
  # 이건 직접 값이므로 환경변수 무시하고 이 값 사용
  knox_id: "fixed_knox_id"
  
  # 이건 환경변수 템플릿이므로 환경변수에서 찾음
  app_id: "${PLM_APP_ID}"
```

---

## 보안 권장사항

### ✅ 안전한 방식

```yaml
# plm_config.yaml
plm:
  # 직접 값을 넣지 말고 환경변수 사용
  knox_id: "${PLM_KNOX_ID}"
  app_id: "${PLM_APP_ID}"
```

```bash
# .env (Git에 커밋 안 함)
PLM_KNOX_ID=your_knox_id
PLM_APP_ID=your_app_id
```

`.gitignore`:
```
.env
.env.local
```

### ❌ 피해야 할 방식

```yaml
# 절대 하지 마세요!
plm:
  knox_id: "my_real_knox_id"  # 민감한 정보 노출
  app_id: "my_real_app_id"     # 민감한 정보 노출
```

---

## 문제 해결

### 문제 1: "PLM knox_id not configured" 에러

**원인:**
- YAML에 `${PLM_KNOX_ID}`로 설정되어 있는데
- 환경변수 `PLM_KNOX_ID`가 설정되지 않음

**해결방법:**

**방법 A: YAML에 직접 설정**
```yaml
knox_id: "your_actual_knox_id"
app_id: "your_actual_app_id"
```

**방법 B: 환경변수 설정**
```bash
export PLM_KNOX_ID="your_knox_id"
export PLM_APP_ID="your_app_id"
```

### 문제 2: 로컬에서는 YAML, 프로덕션에서는 환경변수 사용

**해결방법: plm_config.yaml을 다르게 설정**

개발용 (`plm_config.yaml`):
```yaml
plm:
  knox_id: "dev_knox_id"
  app_id: "dev_app_id"
```

프로덕션에서 실행 전:
```bash
export PLM_KNOX_ID="prod_knox_id"
export PLM_APP_ID="prod_app_id"
```

코드는 자동으로 환경변수를 먼저 확인합니다!

### 문제 3: 어느 방식을 사용하는지 확인하려면?

```python
from plm.plm_rag_integration import PLMConfigManager
import logging

# 로깅 활성화
logging.basicConfig(level=logging.INFO)

# 설정 로드
config = PLMConfigManager()
client = config.get_plm_client()

# 로그에 "Using PLM_KNOX_ID from environment variable" 또는
# 값이 직접 사용됨을 볼 수 있음
```

---

## 설정 검증

설정이 올바른지 확인하기:

```python
from plm import create_plm_integration
import sys

try:
    integration = create_plm_integration()
    print("✓ PLM configuration is valid!")
    
    # 추가 테스트
    response = integration.client.get_defect_code_list()
    if response.is_success():
        print("✓ PLM API connection successful!")
    else:
        print(f"✗ API error: {response.get_error_message()}")
        sys.exit(1)
        
except ValueError as e:
    print(f"✗ Configuration error: {e}")
    sys.exit(1)
except Exception as e:
    print(f"✗ Unexpected error: {e}")
    sys.exit(1)
```

---

## 결론

| 상황 | 설정 방법 |
|------|---------|
| 🏠 개발 중 빠르게 테스트 | **YAML에 직접 입력** |
| 👥 팀과 협업 | **환경변수 사용** |
| 🚀 프로덕션 배포 | **환경변수 + .env** |
| 🔄 CI/CD 파이프라인 | **환경변수 (Secret 사용)** |

**기본 추천:** 
- 개발: YAML 직접 설정으로 빠르게 시작
- 배포: 환경변수로 보안성 확보

```bash
# 빠른 시작 (개발)
# plm_config.yaml에서 knox_id, app_id 수정

# 프로덕션 (배포)
export PLM_KNOX_ID="..."
export PLM_APP_ID="..."
python app.py
```
