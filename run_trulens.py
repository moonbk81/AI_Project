import json
from trulens.core import Tru
from trulens.core import Feedback
from trulens.providers.litellm import LiteLLM
from trulens.core.app import App

# 1. TruLens 초기화 및 이전 세션 정리
tru = Tru()
tru.reset_database()

# 2. 채점관(Judge) LLM 설정
# VRAM 한계가 있으므로, 평가(채점) 만큼은 GPT-4o-mini 같은 아주 저렴한 외부 API를
# 활용하는 것이 정확도 면에서 가장 좋습니다. (또는 가벼운 로컬 모델 연결 가능)
# provider = OpenAI(api_key="YOUR_OPENAI_API_KEY")

provider = LiteLLM(
    model_engine="ollama/gemma3:4b",
    api_base="http://localhost:11434"
)

# 3. 평가 지표(RAG Triad) 세팅
# 지표 1: 사실 기반성 (Hallucination 탐지)


# 지표 2: 답변 적합성
f_answer_relevance = (
    Feedback(provider.relevance, name="Answer Relevance")
    .on_input_output()
)

# 4. 저장된 로그 파일 불러오기 및 가상 실행(Offline Evaluation)
def evaluate_offline_logs(log_file="eval_logs/rag_eval_dataset.jsonl"):
    with open(log_file, "r", encoding="utf-8") as f:
        for line in f:
            data = json.loads(line.strip())

            # TruLens에 레코드 수동 기록
            with tru.VirtualApp(app_id="Telephony_RAG_v1") as app:
                # 컨텍스트와 질문을 매핑하고, 답변을 기록하여 채점 트리거
                app.record(
                    query=data["query"],
                    context=data["context"],
                    answer=data["answer"]
                )

evaluate_offline_logs()

# 5. 대망의 Streamlit 대시보드 실행
tru.run_dashboard()
