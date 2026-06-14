# core/config.py
import yaml
import os

def load_all_config():
    """프로젝트 전체 설정을 로드하여 반환합니다."""
    config_path = os.path.join(os.path.dirname(__file__), '../config.yaml')
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            return yaml.safe_load(f)
    except Exception as e:
        print(f"⚠️ 설정 로드 실패: {e}")
        return {}

# 싱글톤처럼 전역 변수에 한 번 로드해둡니다.
CONFIG = load_all_config()

# 필요한 섹션별로 상수로 정의해두면 가져다 쓰기 편합니다.
ROUTING_MAP = CONFIG.get('routing_map', {})
SYSTEM_PROMPTS = CONFIG.get('system_prompts', {})
QUICK_PROMPTS = CONFIG.get('quick_prompts', {})
PROMPTS = CONFIG.get('prompts', {})
SATELLITE_PROMPTS = CONFIG.get('satellite_prompts', {})

# core/config.py 파일 맨 아래에 추가

# 디바이스별 기본 모델 매핑
DEFAULT_MODEL_BY_DEVICE = {
    "cpu": "gemma4:12b",
    "mps": "gemma4:12b",
    "cuda": "gemma3:4b",  # 회사 8GB VRAM 환경
}

MODEL_CONFIG = {
    "qwen3.5:9b": {
        "num_ctx": 32768,       # 집 맥북 환경 또는 넉넉한 추론용
        "num_predict": 8192,   # Thinking과 리포트가 끊기지 않도록 충분히 확보
        "embed_batch_size": 16,
        "add_batch_size": 256,
        "temperature": 0.1,
        "repeat_penalty": 1.15,
        # "stop": ["<unused", "<|im_end|>", "<eos>"],
        "max_doc_chars": 1800,
        "max_meta_chars": 3000,
        "top_k": 4,
    },
    "gemma4:12b": {
        "num_ctx": 32768,       # 집 맥북 환경 또는 넉넉한 추론용
        "num_predict": 8192,   # Thinking과 리포트가 끊기지 않도록 충분히 확보
        "embed_batch_size": 32,
        "add_batch_size": 256,
        "temperature": 0.1,
        # "repeat_penalty": 1.15,
        # "stop": ["<unused", "<|im_end|>", "<eos>"],
        "max_doc_chars": 1200,
        "max_meta_chars": 2000,
        "top_k": 4,
    },
    "gemma3:12b": {
        "num_ctx": 32768,       # 집 맥북 환경 또는 넉넉한 추론용
        "num_predict": 8192,   # Thinking과 리포트가 끊기지 않도록 충분히 확보
        "embed_batch_size": 32,
        "add_batch_size": 256,
        "temperature": 0.1,
        # "repeat_penalty": 1.15,
        # "stop": ["<unused", "<|im_end|>", "<eos>"],
        "max_doc_chars": 1200,
        "max_meta_chars": 2000,
        "top_k": 4,
    },
    "gemma4:e4b": {
        "num_ctx": 32768,       # 집 맥북 환경 또는 넉넉한 추론용
        "num_predict": 8192,   # Thinking과 리포트가 끊기지 않도록 충분히 확보
        "embed_batch_size": 32,
        "add_batch_size": 256,
        "temperature": 0.1,
        # "repeat_penalty": 1.15,
        # "stop": ["<unused", "<|im_end|>", "<eos>"],
        "max_doc_chars": 1200,
        "max_meta_chars": 2000,
        "top_k": 4,
    },
    "batiai/gemma4-e2b:q4": {
        "num_ctx": 8192,
        "num_predict": 1024,
        "embed_batch_size": 16,
        "add_batch_size": 128,
        "temperature": 0.0,
        "repeat_penalty": 1.25,
        # "stop": ["<unused", "<|im_end|>", "<eos>"],
        "max_doc_chars": 1000,
        "max_meta_chars": 1500,
        "top_k": 4,
    },
    "gemma3:12b": {
        "num_ctx": 32768,
        "num_predict": 4096,
        "embed_batch_size": 32,
        "add_batch_size": 256,
        "temperature": 0.1,
        # "repeat_penalty": 1.15,
        # "stop": ["<unused", "<|im_end|>", "<eos>"],
        "max_doc_chars": 1500,
        "max_meta_chars": 2500,
        "top_k": 4,
    },
    "gemma3:4b": {
        "num_ctx": 8192,       # 회사 PC 8GB VRAM 최적화 크기
        "num_predict": 2048,
        "embed_batch_size": 16,
        "add_batch_size": 128,
        "temperature": 0.0,
        # "repeat_penalty": 1.15,
        # "stop": ["<unused", "<|im_end|>", "<eos>"],
        "max_doc_chars": 1200,
        "max_meta_chars": 2000,
        "top_k": 4,
    },
    "qwen2.5-coder:7b": {
        "num_ctx": 4096,        # 회사 제한 사양 반영
        "num_predict": 2048,
        "embed_batch_size": 32,
        "add_batch_size": 128,
        "temperature": 0.0,     # 코딩/정규식 모델은 0.0에 가까울수록 정확함
        "repeat_penalty": 1.1,
        "stop": ["<|im_end|>", "<|endoftext|>"],
        "max_doc_chars": 1200,
        "max_meta_chars": 2000,
        "top_k": 3,
    },
    "deepseek-r1:7b": {
        "num_ctx": 32768,
        "num_predict": 8192,
        "embed_batch_size": 64,
        "add_batch_size": 256,
        "temperature": 0.6,     # DeepSeek 추론 모델 권장 온도 적용
        "repeat_penalty": 1.1,
        "stop": ["<｜end of sentence｜>", "<｜User｜>", "<｜Assistant｜>"],
        "max_doc_chars": 800,
        "max_meta_chars": 1500,
        "top_k": 3,
    },
    # 리스트에 없는 새 모델을 위한 안전장치 (Fallback)
    "default": {
        "num_ctx": 16384,
        "num_predict": 2048,
        "embed_batch_size": 16,
        "add_batch_size": 128,
        "temperature": 0.1,
        # "repeat_penalty": 1.15,
        # "stop": ["<eos>"],
        "max_doc_chars": 1200,
        "max_meta_chars": 2000,
        "top_k": 3,
    }
}