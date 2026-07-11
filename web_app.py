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
    :root {
        --app-text: #242733;
        --app-muted: #6f7582;
        --app-border: #d8dde5;
        --app-soft-bg: #f7f8fa;
        --app-panel-bg: #ffffff;
        --app-primary: #2f5f9f;
        --app-danger: #d63f3f;
    }

    html, body, [class*="css"] {
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Noto Sans KR", "Apple SD Gothic Neo", sans-serif;
        color: var(--app-text);
    }

    .block-container {
        padding-top: 1.35rem;
        padding-bottom: 2rem;
        max-width: 1440px;
    }

    h1, h2, h3 {
        letter-spacing: 0;
        color: var(--app-text);
        font-weight: 700;
    }

    h1 {
        font-size: 1.55rem !important;
        line-height: 1.25 !important;
        margin-bottom: 0.35rem !important;
    }

    h2 {
        font-size: 1.25rem !important;
        line-height: 1.3 !important;
        margin-top: 0.45rem !important;
        margin-bottom: 0.65rem !important;
    }

    h3 {
        font-size: 1.05rem !important;
        line-height: 1.35 !important;
        margin-top: 0.35rem !important;
        margin-bottom: 0.55rem !important;
    }

    p, label, [data-testid="stMarkdownContainer"] {
        font-size: 0.92rem;
        line-height: 1.5;
    }

    [data-testid="stMarkdownContainer"] p {
        margin-bottom: 0.35rem;
    }

    hr {
        margin: 1.05rem 0 !important;
        border-color: var(--app-border) !important;
    }

    code {
        border-radius: 6px;
        padding: 0.12rem 0.38rem;
        background: #f2f5f7;
        color: #256a3b;
        font-size: 0.84rem;
    }

    [data-testid="stTabs"] [role="tablist"] {
        gap: 0.2rem;
        border-bottom: 1px solid var(--app-border);
    }

    [data-testid="stTabs"] [role="tab"] {
        height: 2.45rem;
        padding: 0 0.8rem;
        border-radius: 8px 8px 0 0;
        color: var(--app-muted);
        font-size: 0.9rem;
        font-weight: 600;
    }

    [data-testid="stTabs"] [role="tab"][aria-selected="true"] {
        color: var(--app-text);
        background: var(--app-panel-bg);
        border-bottom: 2px solid var(--app-primary);
    }

    div[data-testid="stButton"] > button {
        min-height: 2.35rem;
        border-radius: 8px;
        font-size: 0.9rem;
        font-weight: 600;
        padding: 0.38rem 0.8rem;
    }

    div[data-testid="stButton"] > button[kind="primary"] {
        background: var(--app-danger);
        border-color: var(--app-danger);
    }

    [data-testid="stAlert"] {
        border-radius: 8px;
        padding: 0.78rem 0.9rem;
    }

    [data-testid="stAlert"] p {
        line-height: 1.45;
        margin-bottom: 0;
    }

    [data-testid="stMetric"] {
        background: var(--app-panel-bg);
        border: 1px solid var(--app-border);
        border-radius: 8px;
        padding: 0.75rem 0.85rem;
    }

    [data-testid="stMetricLabel"] {
        color: var(--app-muted);
        font-size: 0.78rem;
    }

    [data-testid="stMetricValue"] {
        font-size: 1.15rem;
        font-weight: 700;
    }

    [data-testid="stDataFrame"] {
        border: 1px solid var(--app-border);
        border-radius: 8px;
        overflow: hidden;
    }

    [data-testid="stExpander"] {
        border: 1px solid var(--app-border);
        border-radius: 8px;
        background: var(--app-panel-bg);
    }

    [data-testid="stTextInput"] input,
    [data-testid="stSelectbox"] div[data-baseweb="select"] > div,
    [data-testid="stTextArea"] textarea {
        border-radius: 8px;
        font-size: 0.9rem;
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
st.caption("단말 로그를 업로드하고 주요 통신 이슈, 장애 이벤트, 관련 근거를 확인합니다.")

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
