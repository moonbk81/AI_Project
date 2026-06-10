import os
import json
import warnings

# 1. Hugging Face Transformers의 불필요한 로그 레벨 강제 다운
os.environ["TRANSFORMERS_VERBOSITY"] = "error"
os.environ["TOKENIZERS_PARALLELISM"] = "false"  # 토크나이저 관련 잔여 워닝 방지

# 2. 파이썬 경고(Warning) 모듈을 사용해 __path__ 관련 메시지 싹쓸이 필터링
warnings.filterwarnings("ignore", message=".*Accessing `__path__`.*")
warnings.filterwarnings("ignore", category=FutureWarning, module="transformers.*")
warnings.filterwarnings("ignore", category=UserWarning, module="transformers.*")

import glob
import chromadb
from chromadb.config import Settings
import torch

import re
from agent_toolkit import (
    get_binder_warning_analytics,
    get_battery_thermal_analytics,
    get_crash_anr_analytics,
    get_cs_call_analytics,
    get_data_stall_and_recovery_analytics,
    get_dns_latency_analytics,
    get_internet_stall_analytics,
    get_network_oos_analytics,
    get_ntn_spacex_analytics,
    get_ps_ims_call_analytics,
    get_radio_power_analytics,
    get_recent_data_usage_analytics,
    get_tiantong_satellite_analytics,
)

from sentence_transformers import SentenceTransformer
from core.config import ROUTING_MAP, SYSTEM_PROMPTS, PROMPTS, MODEL_CONFIG
from rca import StructuredEventRenderer
from rag.chroma_utils import (
    sanitize_chroma_metadata,
)
from rag.routing import extract_json_object, get_hybrid_routing, get_llm_routing, get_semantic_routing
from rag.llm_client import call_llm
from rag.retrieval import build_where_filter, retrieve_and_rerank
from rag.ingest import (
    ingest_file as ingest_payload_file,
    get_all_files as get_all_ingested_files,
    reset_db as reset_collection_db,
)
from rag.prompt_builder import build_rag_prompt
from rag.answer_guardrails import try_build_guardrail_answer
from rag.prompt_template import get_domain_guidelines, format_system_wtf_stats

class RilRagChat:
    def __init__(self, db_path="./chroma_db", collection_name="ril_logs", model_name=None, routing_mode="semantic"):
        print("🚀 [시스템 초기화] RAG 시스템을 부팅합니다...")

        # 1. Vector DB 초기화
        self.chroma_client = chromadb.PersistentClient(path=db_path, settings=Settings(anonymized_telemetry=False))
        self.collection = self.chroma_client.get_or_create_collection(name=collection_name)
        self.knowledge_collection = self.chroma_client.get_or_create_collection(name="engineer_knowledge_base")

        # Mac(MPS) 또는 Ubuntu(CUDA) 환경에 맞게 디바이스 자동 설정
        device = "cuda" if torch.cuda.is_available() else "mps" if torch.backends.mps.is_available() else "cpu"
        # 2. 임베딩 모델 로드 (오프라인 경로 또는 허깅페이스 repo)
        if device == "cuda" or device == "cpu":
            current_path = os.path.dirname(os.path.abspath(__file__))
            embed_model_path = os.path.join(current_path, "bge-m3-offline")
        else:
            embed_model_path = "BAAI/bge-m3"
        print(f"📦 임베딩 모델 로드 중... ({embed_model_path})")
        self.embed_model = SentenceTransformer(embed_model_path, device=device)

        # 3. LLM 로드 (Gemma4:12b 적용)
        self.llm_model_name = 'gemma4:12b'  # ✅ 외부에서 접근할 수 있도록 인스턴스 변수로 선언
        if device == "cuda":
            self.llm_model_name = 'gemma3:4b'

        if model_name is not None:
            self.llm_model_name = model_name
        self.routing_mode = routing_mode
        print(f" LLM 연결 준비 중...(Local Ollama - {self.llm_model_name})")
        print(f"✅ 시스템 준비 완료! (사용 디바이스: {device})\n")
        self._load_config()

        self.tool_registry = {
            "get_cs_call_analytics": get_cs_call_analytics,
            "get_ps_ims_call_analytics": get_ps_ims_call_analytics,
            "get_network_oos_analytics": get_network_oos_analytics,
            "get_dns_latency_analytics": get_dns_latency_analytics,
            "get_battery_thermal_analytics": get_battery_thermal_analytics,
            "get_crash_anr_analytics": get_crash_anr_analytics,
            "get_radio_power_analytics": get_radio_power_analytics,
            "get_data_stall_and_recovery_analytics": get_data_stall_and_recovery_analytics,
            "get_internet_stall_analytics": get_internet_stall_analytics,
            "get_ntn_spacex_analytics": get_ntn_spacex_analytics,
            "get_tiantong_satellite_analytics": get_tiantong_satellite_analytics,
            "get_recent_data_usage_analytics": get_recent_data_usage_analytics,
            "get_binder_warning_analytics": get_binder_warning_analytics,
        }

    def _load_config(self):
        try:
            self.routing_map = ROUTING_MAP
            self.system_role_prompt = SYSTEM_PROMPTS.get(
                "main_engineer_role",
                "당신은 Android RIL/Telephony 로그 분석 전문가입니다."
            )
            self.prompts = PROMPTS
            self.log_guidelines = self._load_log_guidelines_from_yaml()
            self.model_config_registry = MODEL_CONFIG
        except Exception as e:
            self.routing_map = {}
            self.system_role_prompt = "시스템 프롬프트를 불러올 수 없습니다."
            self.prompts = {}
            self.log_guidelines = {}
            self.model_config_registry = {
                "default": {
                    "num_ctx": 16384,
                    "num_predict": 2048,
                    "embed_batch_size": 32,
                    "add_batch_size": 128,
                    "temperature": 0.1,
                    "repeat_penalty": 1.15,
                    "stop": ["<eos>"]
                }
            }

    def _load_log_guidelines_from_yaml(self):
        """config.yaml 최상위 log_guidelines를 로드한다."""
        try:
            import yaml
            current_path = os.path.dirname(os.path.abspath(__file__))
            config_path = os.path.join(current_path, "config.yaml")
            if not os.path.exists(config_path):
                return PROMPTS.get("log_guidelines", {}) if isinstance(PROMPTS, dict) else {}

            with open(config_path, "r", encoding="utf-8") as f:
                config_data = yaml.safe_load(f) or {}

            return config_data.get("log_guidelines", {}) or PROMPTS.get("log_guidelines", {}) or {}
        except Exception as e:
            print(f"[WARN] log_guidelines 로드 실패: {e}")
            return PROMPTS.get("log_guidelines", {}) if isinstance(PROMPTS, dict) else {}

    def _get_semantic_routing(self, query):
        return get_semantic_routing(query, self.routing_map, self.embed_model)

    def ingest_file(self, file_path, force=False, model_name="default"):
        return ingest_payload_file(
            collection=self.collection,
            embed_model=self.embed_model,
            file_path=file_path,
            force=force,
            model_name=self.llm_model_name
        )

    def ingest_folder(self, folder_path="./payloads"):
        if not os.path.exists(folder_path):
            os.makedirs(folder_path, exist_ok=True)
            print(f"'{folder_path}' 폴더가 생성되었습니다. 분석된 JSON 파일을 넣어주세요.")
            return

        json_files = glob.glob(os.path.join(folder_path, "*.json"))
        if not json_files:
            print(f"'{folder_path}' 폴더에 적재할 데이터가 없습니다.")
            return

        processed_files = set(self.get_all_files())

        new_files = [f for f in json_files if os.path.basename(f) not in processed_files]
        if not new_files:
            print("✨ 모든 파일이 이미 최신 상태입니다. (추가 적재 없음)")
            return

        print(f"총 {len(new_files)}개의 새로운 로그 파일을 발견했습니다. 적재 시작...")
        total_docs = 0
        for file_path in new_files:
            filename = os.path.basename(file_path)
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            if not data: continue

            global_metas = {}
            if isinstance(data, dict):
                global_metas = data.get("global_metadata", {}) or {}
                data = data.get("payloads", [])

            base_id = os.path.splitext(filename)[0]
            raw_documents = [item["document"] for item in data]
            raw_metadatas = [item["metadata"] for item in data]

            safe_documents = []
            safe_metadatas = []
            model_config = self.model_config_registry.get(self.llm_model_name, self.model_config_registry.get("default", {}))
            MAX_DOC_CHARS = model_config.get("max_doc_chars", 1200)
            MAX_META_CHARS = model_config.get("max_meta_chars", 2000)

            for doc, meta in zip(raw_documents, raw_metadatas):
                safe_documents.append(str(doc)[:MAX_DOC_CHARS])
                safe_meta = meta.copy() if meta else {}
                safe_meta['source_file'] = filename
                safe_metadatas.append(sanitize_chroma_metadata(safe_meta, max_chars=MAX_META_CHARS))

            ids = [f"{base_id}_{i}" for i in range(len(data))]
            print(f"'{filename}' 임베딩 중... ({len(safe_documents)}개 지식)")

            import gc
            model_config = self.model_config_registry.get(self.llm_model_name, self.model_config_registry.get("default", {}))
            for i in range(0, len(safe_documents), model_config["add_batch_size"]):
                batch_docs = safe_documents[i:i+model_config["add_batch_size"]]
                batch_metas = safe_metadatas[i:i+model_config["add_batch_size"]]
                batch_ids = ids[i:i+model_config["add_batch_size"]]
                batch_embeddings = self.embed_model.encode(
                    batch_docs,
                    batch_size=model_config["embed_batch_size"],
                    convert_to_numpy=True,
                    show_progress_bar=False,
                ).tolist()
                self.collection.add(
                    embeddings=batch_embeddings,
                    documents=batch_docs,
                    metadatas=batch_metas,
                    ids=batch_ids,
                )
                del batch_embeddings

            gc.collect()
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
            elif torch.backends.mps.is_available():
                torch.mps.empty_cache()
            total_docs += len(safe_documents)
            del raw_documents, raw_metadatas, safe_documents, safe_metadatas, ids
            gc.collect()
        print(f"\nVector DB 업데이트 완료! (총 {total_docs}개 조각 추가됨)")

    def _get_domain_specific_guideline(self, query, intents, retrieved_log_types):
        query_lower = query.lower()
        log_guidelines_dict = getattr(self, "log_guidelines", {}) or self.prompts.get('log_guidelines', {})

        # 1. prompt_template.py에서 도메인 규칙 가져오기
        guidelines = get_domain_guidelines(query_lower, self.log_guidelines, self.prompts)

        # 2. 검색된 로그 기반 템플릿 주입 (자동화)
        for log_type in retrieved_log_types:
            if log_type in log_guidelines_dict:
                guidelines.append(f"### [{log_type} 전용 출력 템플릿]\n{log_guidelines_dict[log_type]}")

        # 3. SYSTEM_WTF 통계 주입 (self._temp_tool_facts 활용)
        if hasattr(self, '_temp_tool_facts') and self._temp_tool_facts:
            wtf_stats = self._temp_tool_facts.get("wtf_stats_detailed", {})
            wtf_guideline = format_system_wtf_stats(wtf_stats)
            if wtf_guideline:
                guidelines.append(wtf_guideline)

        # 4. 근본 원인 종합 분석
        root_cause_synthesis = self.prompts.get('root_cause_synthesis', "")
        if root_cause_synthesis:
            guidelines.append(f"### [근본 원인 종합 분석]\n{root_cause_synthesis}")

        return "\n\n".join(guidelines)

    def _clean_log_payload(self, text: str) -> str:
        """JSON 특수문자, 대괄호 노이즈 및 LLM의 어텐션을 붕괴시키는 대량의 반복 로그를 영구 방어합니다."""
        if not text:
            return ""

        # 1. raw_logs 패턴 원천 차단
        text = re.sub(r'"raw_logs"\s*:\s*\[.*?\]', '"raw_logs": "[OMITTED_FOR_LLM_DIET]"', text, flags=re.DOTALL)
        text = re.sub(r'"raw_logs"\s*:\s*\{.*?\}', '"raw_logs": "[OMITTED_FOR_LLM_DIET]"', text, flags=re.DOTALL)

        # 💡 [어텐션 붕괴 방어벽 1] 가장 심각한 토큰 도둑: Internet Stall의 key_related_events 다이어트
        # 대괄호 안의 무수한 이벤트 나열을 한 줄로 압축합니다.
        stall_pattern = r"(?:\'|\")?key_related_events(?:\'|\")?\s*:\s*\[.*?\]"
        text = re.sub(stall_pattern, "key_related_events: [OMITTED_FOR_COMPRESSION]", text, flags=re.DOTALL)

        # 💡 [어텐션 붕괴 방어벽 2] SYSTEM_WTF 찌꺼기 완벽 제거
        # 괄호나 따옴표 유무에 상관없이 매칭
        wtf_pattern = r"(?:\'|\"|\[)?\d{2}-\d{2}\s\d{2}:\d{2}:\d{2}\.\d{3}(?:\]|\'|\")?\s*SYSTEM_WTF:\s*.*?교차 확인해야 합니다\.(?:\'|\")?"
        wtf_count = len(re.findall(wtf_pattern, text))
        if wtf_count > 0:
            text = re.sub(wtf_pattern, "", text)
            # 메인 summary에 이미 압축 내용이 있으므로 본문에서는 완전히 날립니다.

        # 💡 [어텐션 붕괴 방어벽 3] NITZ 시간 보정 로그 완벽 제거
        # log_time: 2026-03... 부터 dst_status: 미적용 까지 포맷 무관하게 매칭
        nitz_pattern = r"(?:\{|\'|\")?\s*log_time\s*:\s*.*?dst_status\s*:\s*[^\,\}\]]+(?:\}|\'|\"|\])*"
        nitz_count = len(re.findall(nitz_pattern, text, flags=re.DOTALL))
        if nitz_count > 0:
            text = re.sub(nitz_pattern, "", text, flags=re.DOTALL)
            text = f"💡 [RAG_DIET_SYSTEM] NITZ 시간 보정 로그 {nitz_count}건이 감지되어 압축 처리되었습니다.\n" + text

        # 🧹 [진공 청소기] 요소가 삭제되고 남은 빈 콤마( , , , ) 찌꺼기 싹쓸이
        text = re.sub(r'(\s*,\s*){2,}', ', ', text)  # 여러 개의 콤마를 하나로
        text = re.sub(r'\[\s*,\s*', '[', text)       # [, 형태 정리
        text = re.sub(r'\s*,\s*\]', ']', text)       # ,] 형태 정리
        text = re.sub(r'\{\s*,\s*', '{', text)       # {, 형태 정리

        # 2. 분석 팩트 등 날것의 JSON 형태가 잔존할 경우 가독성 높은 텍스트화
        try:
            data = json.loads(text)
            if isinstance(data, dict):
                return "\n".join([f"- {k}: {v}" for k, v in data.items() if v and k != "raw_logs"])
        except:
            pass

        # 3. 중괄호, 큰따옴표 등 2B 모델 토큰 지연 유발하는 구조물 정제
        text = text.replace("{", "").replace("}", "").replace('"', "").replace("'", "")

        # 4. 연속된 줄바꿈 및 의미 없는 공백 압축
        text = re.sub(r'\n\s*\n', '\n', text).strip()

        return text

    def ask(self, user_query, current_file=None, chat_history=None, top_k=None, health_kpi=None, is_bench=False):
        current_base = current_file.replace("_payload.json", "") if current_file else "Unknown"
        model_config = self.model_config_registry.get(
            self.llm_model_name,
            self.model_config_registry.get("default", {})
        )
        if top_k is None:
            top_k = int(model_config.get("top_k", 3))
            print(f"top_k가 지정되지 않아 모델 설정의 기본값 {top_k}를 사용합니다.")
        else:
            top_k = int(top_k)

        search_query = user_query
        if len(user_query) < 15 and chat_history:
            last_msg = next((msg['content'] for msg in reversed(chat_history) if msg['role'] == 'user'), "")
            search_query = f"{last_msg} 관련 후속 질문: {user_query}"

        if self.routing_mode == "llm": routing_result = self._get_llm_routing(search_query)
        elif self.routing_mode == "hybrid": routing_result = self._get_hybrid_routing(search_query)
        else: routing_result = self._get_semantic_routing(search_query)

        selected_tools = routing_result.get("tools", [])
        target_log_types = routing_result.get("log_types", [])
        intents = routing_result.get("intents", [])

        if "Call_Drop_Trap" in intents:
            intents = ["Call_Drop_Trap"]
            selected_tools = self.routing_map.get("Call_Drop_Trap", {}).get(
                "tools",
                ["get_ps_ims_call_analytics", "get_cs_call_analytics"],
            )
            target_log_types = self.routing_map.get("Call_Drop_Trap", {}).get(
                "log_types",
                ["Call_Session"],
            )

        elif "Time_Context_Inference" in intents:
            intents = ["Time_Context_Inference"]
            selected_tools = self.routing_map.get("Time_Context_Inference", {}).get("tools", ["get_ps_ims_call_analytics", "get_cs_call_analytics", "get_radio_power_analytics", "get_network_oos_analytics"])
            target_log_types = self.routing_map.get("Time_Context_Inference", {}).get("log_types", ["Call_Session", "Radio_Power_Event", "OOS_Event", "Device_Property_State"])

        if "Tiantong_Satellite" in intents:
            selected_tools = ["get_tiantong_satellite_analytics"]
            target_log_types = ["Satellite_AT_Command"]

        # 1. DB 검색 (retrieval.py 내에서 is_datacall_failure_query에 의해 자동 확장됨)
        results = retrieve_and_rerank(
            collection=self.collection,
            embed_model=self.embed_model,
            search_query=search_query,
            top_k=top_k,
            current_file=current_file,
            target_log_types=target_log_types,
        )

        retrieved_log_types = {meta.get("log_type") for meta in results.get('metadatas', [[]])[0] if meta and meta.get("log_type")}
        if len(target_log_types) <= 3:
            retrieved_log_types.update(target_log_types)

        past_knowledge_context = self._get_past_knowledge_context(search_query, top_k=2)

        # 2. 팩트 데이터 추출
        tool_facts_list = []
        if current_base != "Unknown" and selected_tools:
            for tool_name in selected_tools:
                tool_fn = self.tool_registry.get(tool_name)
                if tool_fn:
                    try: tool_facts_list.append(f"[{tool_name} 분석 팩트]:\n{tool_fn(current_base)}")
                    except Exception as e: print(f"Tool 실행 에러 ({tool_name}): {e}")

        tool_facts = "\n\n".join(tool_facts_list) if tool_facts_list else "매칭된 도구 분석 결과가 없습니다."
        tool_facts = self._clean_log_payload(tool_facts)

        if past_knowledge_context:
            tool_facts = f"{past_knowledge_context}\n\n{tool_facts}"

        # 💡 [핵심] JSON 데이터 임시 보관 (prompt_template 주입용)
        self._temp_tool_facts = json.loads(tool_facts) if tool_facts.startswith('{') else {}

        if health_kpi:
            sanitized_kpi = self._clean_log_payload(health_kpi)
            tool_facts = f"=== [단말 전반 KPI 상태] ===\n{sanitized_kpi}\n\n=== [세부 도구 분석 팩트] ===\n{tool_facts}"

        # 3. 가이드라인 및 프롬프트 생성
        domain_guidelines = self._get_domain_specific_guideline(search_query, intents, retrieved_log_types)
        self._temp_tool_facts = None # 완료 후 초기화

        # 4. 구조화된 분석 결론 주입 (StructuredEventRenderer)
        direct_structured_answer = StructuredEventRenderer.render(results, user_query)
        if direct_structured_answer:
            from rag.prompt_template import format_structured_analysis
            tool_facts = f"{format_structured_analysis(direct_structured_answer)}\n\n{tool_facts}"

        formatted_logs = self._format_results(results)
        formatted_logs = self._clean_log_payload(formatted_logs)

        # 5. 가드레일 검사
        guardrail_answer = try_build_guardrail_answer(user_query, results, tool_facts=tool_facts)
        if guardrail_answer:
            guardrail_injection = (
                f"🚨 [가드레일 최고 등급 강제 지시사항 - 절대 준수]:\n"
                f"{guardrail_answer}\n"
                f"(※ 위 내용은 시스템이 검증한 '절대 팩트'입니다. 어떤 경우에도 위 결론을 뒤집거나 부정하지 말고, 사용자의 요청 양식과 페르소나에 맞게 자연스러운 문장으로만 다듬어서 최종 답변을 생성하십시오.)"
            )
            tool_facts = f"{guardrail_injection}\n\n{tool_facts}"
            print("[RAG_INFO] 가드레일 확정 답변을 LLM 프롬프트 최우선 순위로 주입했습니다.")

        # 6. 시스템 프롬프트 빌드 및 디버깅 로그 저장
        system_prompt = build_rag_prompt(self.system_role_prompt, domain_guidelines, tool_facts, formatted_logs)

        if os.getenv("RAG_DEBUG_PROMPT", "0") == "1":
            try:
                debug_dir = os.path.join(os.getcwd(), "debug_prompts")
                os.makedirs(debug_dir, exist_ok=True)
                safe_base = re.sub(r"[^a-zA-Z0-9_.-]+", "_", current_base or "Unknown")
                debug_path = os.path.join(debug_dir, f"{safe_base}_last_prompt.txt")
                debug_meta_path = os.path.join(debug_dir, f"{safe_base}_last_retrieval.json")
                with open(debug_path, "w", encoding="utf-8") as f: f.write(system_prompt + f"\n\n사용자 질문: {user_query}")

                debug_payload = {
                    "user_query": user_query, "search_query": search_query, "current_file": current_file,
                    "routing_result": routing_result, "selected_tools": selected_tools,
                    "target_log_types": target_log_types, "intents": intents,
                    "doc_ids": results['ids'][0] if results and results.get('ids') else [],
                    "retrieved_meta": results['metadatas'][0] if results and results.get('metadatas') else [],
                }
                with open(debug_meta_path, "w", encoding="utf-8") as f: json.dump(debug_payload, f, ensure_ascii=False, indent=2, default=str)
            except Exception as e: print(f"[RAG_DEBUG] failed: {e}")

        # 7. LLM 호출 및 결과 반환
        answer, thinking = self._call_llm(system_prompt=system_prompt, user_query=user_query, is_bench=is_bench)
        return answer, results.get('ids', [[]])[0], results.get('metadatas', [[]])[0], thinking

    def get_all_files(self):
        return get_all_ingested_files(self.collection)

    def reset_db(self):
        print("[시스템] Vector DB (로그 & 지식 베이스) 강제 초기화를 시작합니다...")
        try:
            try:
                self.chroma_client.delete_collection(self.collection.name)
            except Exception:
                pass # 이미 없으면 무시
            self.collection = self.chroma_client.create_collection(name="ril_logs")

            try:
                self.chroma_client.delete_collection(self.knowledge_collection.name)
            except Exception:
                pass
            self.knowledge_collection = self.chroma_client.create_collection(name="engineer_knowledge_base")

            print("✅ 모든 Vector DB 컬렉션이새로 생성되었습니다.")
            return True # 💡 정상적으로 True가 리턴되어야 UI가 폴더를 지웁니다!

        except Exception as e:
            print(f"❌ DB 초기화 중 치명적 오류 발생: {e}")
            return False

    def _format_results(self, results) -> str:
        if not results or not results.get('documents') or not results['documents'][0]:
            return "관련된 로그를 DB에서 찾지 못했습니다."
        formatted = []
        for i, (doc, meta) in enumerate(zip(results['documents'][0], results['metadatas'][0])):
            clean_meta = {k: v for k, v in meta.items() if not k.startswith("raw_") and k != "source_file"}
            snippet = "(DB에 원본 로그가 없습니다)"
            raw_data = meta.get("raw_logs", meta.get("raw_context", "[]"))
            try:
                raw_list = json.loads(raw_data) if isinstance(raw_data, str) else []
                real_logs = [l for l in raw_list if "중략됨" not in l and l.strip()]
                if real_logs:
                    if len(real_logs) > 30:
                        snippet = "\n".join(real_logs[:15]) + "\n...[중략]...\n" + "\n".join(real_logs[-15:])
                    else:
                        snippet = "\n".join(real_logs)
            except: pass

            meta_lines = "\n".join([f"  - {k}: {v}" for k, v in clean_meta.items()])
            formatted.append(f"[자료 {i+1} - {meta.get('log_type')}]\n[메타정보]\n{meta_lines}\n\n[요약]\n{doc}\n\n[원본 로그 스니펫]\n{snippet}")
        return "\n\n".join(formatted)

    def _get_past_knowledge_context(self, query: str, top_k: int = 2) -> str:
        """사용자 질의와 유사한 과거 장애 사례를 사내 지식 베이스에서 검색하여 포맷팅합니다."""
        past_knowledge_context = ""
        try:
            # 사용자의 질문을 벡터로 변환
            query_vector = self.embed_model.encode([query], convert_to_numpy=True).tolist()

            # 지식 베이스 컬렉션에서 유사한 과거 사례 상위 n개 조회
            kb_results = self.knowledge_collection.query(
                query_embeddings=query_vector,
                n_results=top_k
            )

            if kb_results and kb_results.get('documents') and kb_results['documents'][0]:
                kb_docs = kb_results['documents'][0]
                kb_metas = kb_results['metadatas'][0]

                kb_lines = []
                for idx, (doc, meta) in enumerate(zip(kb_docs, kb_metas)):
                    kb_lines.append(
                        f"[과거 유사 사례 {idx+1}]\n"
                        f"- 대상 모델: {meta.get('model_name', 'Unknown')}\n"
                        f"- 심각도: {meta.get('severity', 'Normal')}\n"
                        f"- 해결 방안 및 엔지니어 코멘트: {doc}"
                    )

                past_knowledge_context = "=== [💡 참조할 사내 과거 장애 유사 사례] ===\n" + "\n\n".join(kb_lines)
                print(f"🔍 [RAG_INFO] 과거 사내 지식 베이스에서 {len(kb_lines)}건의 연관 사례를 찾아 컨텍스트에 주입했습니다.")
        except Exception as e:
            print(f"[WARN] 사내 지식 베이스 조회 중 에러 발생: {e}")

        return past_knowledge_context

    # 💡 [핵심 수정 3] 래퍼 함수 파라미터 분리 동기화
    def _call_llm(self, system_prompt: str, user_query: str, is_bench=False) -> tuple[str, str]:
        return call_llm(
            system_prompt=system_prompt,
            user_query=user_query,
            model_name=self.llm_model_name,
            model_config_registry=self.model_config_registry,
            is_bench=is_bench,
        )

    def save_knowledge(self, target_ids, feedback, severity="Normal", build_info=None, **kwargs):
        try:
            import uuid
            doc_id = str(uuid.uuid4())
            target_ids_str = ",".join(target_ids) if isinstance(target_ids, list) else str(target_ids)

            # 1. 기본 메타데이터 세팅
            metadata = {
                "target_ids": target_ids_str,
                "solution": feedback,
                "severity": severity,
                "type": "expert_knowledge"
            }

            # 2. 💡 [추가] 단말기 빌드/하드웨어 정보가 넘어왔다면 메타데이터에 병합
            if build_info and isinstance(build_info, dict):
                for k in ["model_name", "hardware", "android_sdk", "radio", "kernel"]:
                    if k in build_info:
                        metadata[k] = build_info[k]

            # 3. 💡 [핵심 해결] 오프라인 환경의 SSL 에러 방지를 위해 직접 임베딩 벡터 생성
            embedding = self.embed_model.encode([feedback], convert_to_numpy=True).tolist()

            # 4. Vector DB에 임베딩 값과 함께 저장
            self.knowledge_collection.add(
                embeddings=embedding,
                documents=[feedback],
                metadatas=[metadata],
                ids=[doc_id]
            )
            print(f"💾 [Knowledge Save] 지식 DB 박제 완료! (ID: {doc_id}, Severity: {severity})")
            return True
        except Exception as e:
            print(f"❌ 지식 저장 실패: {e}")
            return False

    def _extract_json_object(self, text: str) -> dict:
        return extract_json_object(text)

    def _get_llm_routing(self, query: str) -> dict:
        return get_llm_routing(query, self.routing_map, self.llm_model_name)

    def _get_hybrid_routing(self, query: str) -> dict:
        return get_hybrid_routing(query, self.routing_map, self.embed_model, self.llm_model_name)