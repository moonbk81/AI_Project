import streamlit as st
import torch
import warnings

from app.pipeline import run_analysis_pipeline
from app.sidebar import render_sidebar
from app.tabs import (
    render_benchmark_tab,
    render_boot_tab,
    render_chat_tab,
    render_dashboard_tab,
    render_internet_tab,
    render_satellite_tab,
    render_knowledge_tab,
    render_plm_section_tab,
)
from ril_rag_chat import RilRagChat
from core.config import DEFAULT_MODEL_BY_DEVICE

warnings.filterwarnings("ignore")

st.set_page_config(page_title="Log Analysis Console", layout="wide")

st.markdown(
    """
    <style>
    html, body, [class*="css"] {
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Noto Sans KR", "Apple SD Gothic Neo", sans-serif;
    }
    .block-container {
        padding-top: 2rem;
    }
    h1, h2, h3 {
        letter-spacing: -0.02em;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

if 'active_model' not in st.session_state:
    if torch.cuda.is_available():
        device_type = "cuda"
    elif torch.backends.mps.is_available():
        device_type = "mps"
    else:
        device_type = "cpu"

    default_model = DEFAULT_MODEL_BY_DEVICE.get(
        device_type,
        "gemma4:12b"
    )

    st.session_state['active_model'] = default_model

if 'active_routing_mode' not in st.session_state:
    st.session_state['active_routing_mode'] = "semantic"

if 'last_loaded_at' not in st.session_state:
    st.session_state['last_loaded_at'] = "System Initializing..."

@st.cache_resource(show_spinner=False)
def load_rag_engine(model_name, routing_mode):
    return RilRagChat(
        model_name=model_name,
        routing_mode=routing_mode
    )

try:
    engine = load_rag_engine(
        st.session_state.get('active_model'),
        st.session_state.get('active_routing_mode')
    )
except Exception as e:
    st.error(f"엔진 초기화에 실패했습니다. Ollama 서비스 활성화 여부를 확인하십시오.\nError Details: {e}")
    st.stop()

def init_session_states():
    defaults = {
        "messages": [], "last_ids": [], "last_metas": [],
        "uploader_key": 0, "feedback_key": 0, "current_file": None
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value

init_session_states()

if "chat_history" not in st.session_state: st.session_state.chat_history = []

st.title("Android Log Analysis Console")
st.markdown("단말 로그를 업로드하고 주요 통신 이슈, 장애 이벤트, 관련 근거를 확인합니다.")

# Show notification if coming from PLM analysis
if st.session_state.get('navigate_to_chat', False):
    st.info("🚀 PLM 결함 분석 정보가 준비되었습니다. **'로그 분석' 탭**을 클릭하면 바로 분석이 시작됩니다!", icon="ℹ️")
    st.session_state.navigate_to_chat = False

tab_chat, tab_dash, tab_boot, tab_ntn, tab_internet, tab_benchmark, tab_knowledge, tab_plm = st.tabs([
    "로그 분석", "통계 대시보드", "부팅·Crash·ANR·NITZ",
    "위성 통신", "인터넷 품질", "평가 결과", "지식 베이스",
    "PLM 결함 관리"])

with st.sidebar:
    render_sidebar(engine, run_analysis_pipeline)

with tab_chat:
    render_chat_tab(engine)

with tab_dash:
    render_dashboard_tab(engine)

with tab_boot:
    render_boot_tab()

with tab_ntn:
    render_satellite_tab(engine)

with tab_internet:
    render_internet_tab()

with tab_benchmark:
    render_benchmark_tab()

with tab_knowledge:
    render_knowledge_tab(engine)

with tab_plm:
    render_plm_section_tab()