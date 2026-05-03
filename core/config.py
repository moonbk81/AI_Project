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