# core/config.py
import logging
import yaml
import os

logger = logging.getLogger(__name__)

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

# 디바이스별 기본 모델 매핑
DEFAULT_MODEL_BY_DEVICE = {
    "cpu": "gemma4:12b",
    "mps": "gemma4:12b",
    "cuda": "gemma4-31b",  # 게이트웨이 서빙명 (하이픈)
}

# 모델별 추론/임베딩 배치 설정입니다.
# 키는 Ollama 모델명 또는 vLLM served model name과 그대로 맞춥니다.
MODEL_CONFIG = {
    "Qwen/Qwen2.5-72B-Instruct": {
        "num_ctx": 32768,
        "num_predict": 8192,
        "embed_batch_size": 64,
        "add_batch_size": 256,
        "temperature": 0.1,
        "top_p": 0.9,
        "repeat_penalty": 1.05,
        "stop": ["<|im_end|>", "<|endoftext|>"],
        "max_doc_chars": 2200,
        "max_meta_chars": 3600,
        "top_k": 5,
    },
    "qwen2.5-72b-instruct": {
        "num_ctx": 32768,
        "num_predict": 8192,
        "embed_batch_size": 64,
        "add_batch_size": 256,
        "temperature": 0.1,
        "top_p": 0.9,
        "repeat_penalty": 1.05,
        "stop": ["<|im_end|>", "<|endoftext|>"],
        "max_doc_chars": 2200,
        "max_meta_chars": 3600,
        "top_k": 5,
    },
    "qwen72b": {  # vLLM served model name
        "num_ctx": 32768,
        "num_predict": 8192,   # 리포트가 중간에 잘리지 않도록 확보 (max_tokens로 전달됨)
        "embed_batch_size": 64,
        "add_batch_size": 256,
        "temperature": 0.1,
        "top_p": 0.9,
        "repeat_penalty": 1.05,
        "stop": ["<|im_end|>", "<|endoftext|>"],
        "max_doc_chars": 2200,
        "max_meta_chars": 3600,
        "top_k": 5,
    },
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
    # 게이트웨이(10.253.68.95:3000) 서빙명 그대로. /api/models 로 확인한 이름과
    # 정확히 일치해야 하며, 불일치 시 default 로 조용히 폴백된다.
    "gemma4-31b": {
        "num_ctx": 32768,       # vLLM served model - 대용량 추론 환경
        "num_predict": 8192,    # Thinking과 리포트가 끊기지 않도록 충분히 확보
        "embed_batch_size": 64, # 로컬 GPU 8GB 전체 할당 (LLM은 원격 vLLM)
        "add_batch_size": 256,
        "temperature": 0.1,
        "top_p": 0.9,
        # "repeat_penalty": 1.15,   # Gemma 계열은 1.05 초과 시 조기 종료가 잦아 미사용
        # "stop": ["<unused", "<|im_end|>", "<eos>"],
        "max_doc_chars": 1500,
        "max_meta_chars": 2500,
        "top_k": 5,
    },
    "qwen3.8-27b": {  # 게이트웨이 서빙명
        "num_ctx": 32768,
        "num_predict": 8192,
        "embed_batch_size": 64,
        "add_batch_size": 256,
        "temperature": 0.1,
        "top_p": 0.9,
        "repeat_penalty": 1.05,
        "stop": ["<|im_end|>", "<|endoftext|>"],
        "max_doc_chars": 2200,
        "max_meta_chars": 3600,
        "top_k": 5,
    },
    "gemma4:12b": {
        "num_ctx": 32768,       # 집 맥북 환경 또는 넉넉한 추론용
        "num_predict": 8192,   # Thinking과 리포트가 끊기지 않도록 충분히 확보
        "embed_batch_size": 64,
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
        "embed_batch_size": 64,
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
        "embed_batch_size": 64,
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
        "embed_batch_size": 64,
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


def _normalize_model_key(name) -> str:
    """Fold naming differences between gateway and Ollama style names.

    The gateway serves hyphenated names (``gemma4-31b``) while Ollama-style keys
    use a colon (``gemma4:31b``). Both should resolve to the same tuning.
    """
    return str(name or "").strip().lower().replace(":", "-")


def get_model_config(model_name, registry=None):
    """Return per-model tuning for ``model_name``.

    An exact-key-only lookup used to fall back to ``default`` silently whenever the
    served model name was spelled differently (``gemma4-31b`` vs ``gemma4:31b``).
    That quietly dropped ``embed_batch_size`` to 16 and ``num_predict`` to 2048,
    which shows up as slow embedding and truncated reports rather than an error —
    so log loudly when we really have no entry.
    """
    reg = MODEL_CONFIG if registry is None else registry

    if model_name in reg:
        return reg[model_name]

    normalized = _normalize_model_key(model_name)
    for key, cfg in reg.items():
        if key != "default" and _normalize_model_key(key) == normalized:
            return cfg

    fallback = reg.get("default", {})
    logger.warning(
        "MODEL_CONFIG has no entry for %r; using 'default' "
        "(num_predict=%s, embed_batch_size=%s). Check the served name via "
        "/api/models and add a matching key.",
        model_name,
        fallback.get("num_predict"),
        fallback.get("embed_batch_size"),
    )
    return fallback
