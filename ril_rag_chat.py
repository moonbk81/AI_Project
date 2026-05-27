import os
import json
import glob
import chromadb
import torch
import numpy as np
import re
import agent_tools

from tools.eval_logger import log_rag_for_evaluation
from sentence_transformers import SentenceTransformer
from core.config import ROUTING_MAP, SYSTEM_PROMPTS, PROMPTS, MODEL_CONFIG

def _to_chroma_meta_value(value, max_chars=5000):
    """ChromaDB metadata accepts only scalar/list values, not dict.
    Convert dict/tuple/set and oversized values to safe strings.
    """
    import json
    if value is None or isinstance(value, (str, int, float, bool)):
        out = value
    elif isinstance(value, list):
        safe_list = []
        for item in value:
            if item is None or isinstance(item, (str, int, float, bool)):
                safe_list.append(item)
            else:
                safe_list.append(json.dumps(item, ensure_ascii=False, default=str))
        out = safe_list
    else:
        out = json.dumps(value, ensure_ascii=False, default=str)
    if isinstance(out, str) and len(out) > max_chars:
        out = out[:max_chars] + "\n...[TRUNCATED_BY_SYSTEM: TOO_LONG]"
    return out

def _sanitize_chroma_metadata(meta, max_chars=5000):
    safe = {}
    for k, v in (meta or {}).items():
        safe[str(k)] = _to_chroma_meta_value(v, max_chars=max_chars)
    return safe

class RilRagChat:
    def __init__(self, db_path="./chroma_db", collection_name="ril_logs", model_name=None, routing_mode="semantic"):
        print("🚀 [시스템 초기화] RAG 시스템을 부팅합니다...")

        # 1. Vector DB 초기화
        self.chroma_client = chromadb.PersistentClient(path=db_path)
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

        # 3. LLM 로드 (Gemma4-e4b 적용)
        self.llm_model_name = 'gemma4:e4b'  # ✅ 외부에서 접근할 수 있도록 인스턴스 변수로 선언
        if device == "cuda":
            self.llm_model_name = 'batiai/gemma4-e2b:q4'

        if model_name is not None:
            self.llm_model_name = model_name
        self.routing_mode = routing_mode
        print(f" LLM 연결 준비 중...(Local Ollama - {self.llm_model_name})")
        print(f"✅ 시스템 준비 완료! (사용 디바이스: {device})\n")
        self._load_config()

        self.tool_registry = {
            "get_cs_call_analytics": agent_tools.get_cs_call_analytics,
            "get_ps_ims_call_analytics": agent_tools.get_ps_ims_call_analytics,
            "get_network_oos_analytics": agent_tools.get_network_oos_analytics,
            "get_dns_latency_analytics": agent_tools.get_dns_latency_analytics,
            "get_battery_thermal_analytics": getattr(agent_tools, 'get_battery_thermal_analytics', None),
            "get_crash_anr_analytics": getattr(agent_tools, 'get_crash_anr_analytics', None),
            "get_radio_power_analytics": getattr(agent_tools, 'get_radio_power_analytics', None),
            "get_data_stall_and_recovery_analytics": getattr(agent_tools, 'get_data_stall_and_recovery_analytics', None),
            "get_internet_stall_analytics": getattr(agent_tools, 'get_internet_stall_analytics', None),
            "get_ntn_spacex_analytics": getattr(agent_tools, 'get_ntn_spacex_analytics', None),
            "get_tiantong_satellite_analytics": getattr(agent_tools, 'get_tiantong_satellite_analytics', None),
            "get_recent_data_usage_analytics": getattr(agent_tools, 'get_recent_data_usage_analytics', None)
        }

    def _load_config(self):
        try:
            self.routing_map = ROUTING_MAP
            self.system_role_prompt = SYSTEM_PROMPTS.get(
                "main_engineer_role",
                "당신은 Android RIL/Telephony 로그 분석 전문가입니다."
            )
            self.prompts = PROMPTS
            self.model_config_registry = MODEL_CONFIG
        except Exception as e:
            self.routing_map = {}
            self.system_role_prompt = "시스템 프롬프트를 불러올 수 없습니다."
            self.prompts = {}
            self.model_config_registry = {
                "default": {
                    "num_ctx": 16384,
                    "num_predict": 2048,
                    "temperature": 0.1,
                    "repeat_penalty": 1.15,
                    "stop": ["<eos>"]
                }
            }

    def _get_semantic_routing(self, query):
        chunks = [chunk.strip() for chunk in re.split(r'[\n\.]', query) if len(chunk.strip()) > 5]
        if not chunks:
            chunks = [query]

        category_scores = []
        for category, data in self.routing_map.items():
            intent_vec = self.embed_model.encode(data["desc"])
            max_sim = 0.0

            for chunk in chunks:
                chunk_vec = self.embed_model.encode(chunk)
                sim = np.dot(chunk_vec, intent_vec) / (
                    np.linalg.norm(chunk_vec) * np.linalg.norm(intent_vec)
                )
                if sim > max_sim:
                    max_sim = sim
            category_scores.append((category, float(max_sim), data))

        category_scores.sort(key=lambda x: x[1], reverse=True)

        selected_tools = set()
        selected_log_types = set()
        selected_intents = set()

        threshold = 0.52
        multi_threshold = 0.50

        routing_scores = {category: float(score) for category, score, _ in category_scores}

        if not category_scores:
            selected_intents.add("Fallback_General")
            selected_tools.update(["get_cs_call_analytics", "get_network_oos_analytics", "get_dns_latency_analytics"])
            selected_log_types.update(["Call_Session", "OOS_Event", "Signal_Level", "Network_Timeline_Stat", "Network_DNS_Issue"])
            return {"intents": list(selected_intents), "tools": list(selected_tools), "log_types": list(selected_log_types), "scores": routing_scores, "top_matches": []}

        top1_cat, top1_score, top1_data = category_scores[0]
        if top1_score < threshold:
            selected_intents.add("Fallback_General")
            selected_tools.update(["get_cs_call_analytics", "get_network_oos_analytics", "get_dns_latency_analytics"])
            selected_log_types.update(["Call_Session", "OOS_Event", "Signal_Level", "Network_Timeline_Stat", "Network_DNS_Issue"])
        else:
            selected_intents.add(top1_cat)
            selected_tools.update(top1_data["tools"])
            selected_log_types.update(top1_data["log_types"])

            if len(category_scores) > 1:
                top2_cat, top2_score, top2_data = category_scores[1]
                if top2_score >= multi_threshold:
                    selected_intents.add(top2_cat)
                    selected_tools.update(top2_data["tools"])
                    selected_log_types.update(top2_data["log_types"])

        if not selected_tools and not selected_log_types:
            selected_intents.add("Fallback_General")
            selected_tools.update(["get_cs_call_analytics", "get_network_oos_analytics", "get_dns_latency_analytics"])
            selected_log_types.update(["Call_Session", "OOS_Event", "Signal_Level", "Network_Timeline_Stat", "Network_DNS_Issue"])

        query_lower = query.lower()
        if any(keyword in query_lower for keyword in ["비행기 모드", "airplane mode", "flight mode", "radio power", "모뎀 전원", "라디오 파워"]):
            selected_intents.add("Radio_Power")
            if "Radio_Power" in self.routing_map:
                selected_tools.update(self.routing_map["Radio_Power"].get("tools", []))
                selected_log_types.update(self.routing_map["Radio_Power"].get("log_types", []))

        if any(keyword in query_lower for keyword in ["인터넷", "먹통", "웹페이지", "데이터 안됨", "데이터가 안", "데이터 안 되고", "데이터가 안 되고", "데이터 멈춤", "데이터가 멈", "데이터 먹통", "데이터 접속 안", "data stall", "스톨", "validation", "validation failed", "no internet", "partial connectivity", "private dns", "tcp timeout", "tls handshake", "라우팅", "default network"]):
            selected_intents.add("Internet_Stall")
            if "Internet_Stall" in self.routing_map:
                selected_tools.update(self.routing_map["Internet_Stall"].get("tools", []))
                selected_log_types.update(self.routing_map["Internet_Stall"].get("log_types", []))

        if any(keyword in query_lower for keyword in ["dns", "패킷", "ping", "핑", "네트워크 지연", "데이터 느림"]):
            selected_intents.add("DNS_Latency")
            if "DNS_Latency" in self.routing_map:
                selected_tools.update(self.routing_map["DNS_Latency"].get("tools", []))
                selected_log_types.update(self.routing_map["DNS_Latency"].get("log_types", []))

        if any(keyword in query_lower for keyword in ["anr", "crash/anr", "crash", "크래시", "강제종료", "응답 없음", "응답없음", "application not responding", "fatal exception", "watchdog", "프리징", "먹통", "바인더", "binder", "transaction"]):
            selected_intents.add("Crash_ANR")
            if "Crash_ANR" in self.routing_map:
                selected_tools.update(self.routing_map["Crash_ANR"].get("tools", []))
                selected_log_types.update(self.routing_map["Crash_ANR"].get("log_types", []))

        if any(keyword in query_lower for keyword in ["spacex", "starlink", "ntn", "스페이스엑스"]):
            selected_intents.add("NTN_SpaceX")
            if "NTN_SpaceX" in self.routing_map:
                selected_tools.update(self.routing_map["NTN_SpaceX"].get("tools", []))
                selected_log_types.update(self.routing_map["NTN_SpaceX"].get("log_types", []))

        if any(keyword in query_lower for keyword in ["tiantong", "티엔통", "천통", "at command", "위성 모뎀"]):
            selected_intents.add("Tiantong_Satellite")
            if "Tiantong_Satellite" in self.routing_map:
                selected_tools.update(self.routing_map["Tiantong_Satellite"].get("tools", []))
                selected_log_types.update(self.routing_map["Tiantong_Satellite"].get("log_types", []))

        if any(keyword in query_lower for keyword in ["ril", "rilj", "모뎀", "명령어", "타임아웃", "딜레이", "지연", "응답"]):
            selected_log_types.update(["RILJ_Transaction"])

        top_matches = [{"intent": category, "score": float(score)} for category, score, _ in category_scores[:3]]
        return {"intents": sorted(list(selected_intents)), "tools": sorted(list(selected_tools)), "log_types": sorted(list(selected_log_types)), "scores": routing_scores, "top_matches": top_matches}

    def ingest_file(self, file_path, force=False):
        if not os.path.exists(file_path):
            print(f"❌ payload 파일 없음: {file_path}")
            return
        filename = os.path.basename(file_path)
        base_id = os.path.splitext(filename)[0]

        if force:
            old = self.collection.get(where={"source_file": filename}, include=[])
            if old and old.get("ids"):
                self.collection.delete(ids=old["ids"])

        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not data:
            print(f"⚠️ 비어있는 payload: {filename}")
            return

        MAX_DOC_CHARS = 4000
        MAX_META_CHARS = 5000
        BATCH_SIZE = 100
        import gc

        docs, metas, ids = [], [], []
        for i, item in enumerate(data):
            docs.append(str(item["document"])[:MAX_DOC_CHARS])
            meta = item.get("metadata", {}).copy()
            meta["source_file"] = filename
            metas.append(_sanitize_chroma_metadata(meta, max_chars=MAX_META_CHARS))
            ids.append(f"{base_id}_{i}")

        print(f"🔄 '{filename}' 배치 임베딩 시작... (총 {len(docs)} docs)")
        for i in range(0, len(docs), BATCH_SIZE):
            batch_docs = docs[i : i + BATCH_SIZE]
            batch_metas = metas[i : i + BATCH_SIZE]
            batch_ids = ids[i : i + BATCH_SIZE]
            batch_embeddings = self.embed_model.encode(batch_docs, batch_size=16, convert_to_numpy=True, normalize_embeddings=True).tolist()
            self.collection.add(ids=batch_ids, documents=batch_docs, metadatas=batch_metas, embeddings=batch_embeddings)
            del batch_embeddings
            gc.collect()
            if torch.cuda.is_available(): torch.cuda.empty_cache()
            elif torch.backends.mps.is_available(): torch.mps.empty_cache()
        print(f"✅ {filename} 단일 파일 재적재 완료: {len(docs)} docs")

    def ingest_folder(self, folder_path="./payloads"):
        if not os.path.exists(folder_path):
            os.makedirs(folder_path, exist_ok=True)
            print(f"📂 '{folder_path}' 폴더가 생성되었습니다. 분석된 JSON 파일을 넣어주세요.")
            return

        json_files = glob.glob(os.path.join(folder_path, "*.json"))
        if not json_files:
            print(f"⚠️ '{folder_path}' 폴더에 적재할 데이터가 없습니다.")
            return

        existing_data = self.collection.get(include=["metadatas"])
        processed_files = set()
        if existing_data and existing_data["metadatas"]:
            for meta in existing_data["metadatas"]:
                if meta and "source_file" in meta:
                    processed_files.add(meta["source_file"])

        new_files = [f for f in json_files if os.path.basename(f) not in processed_files]
        if not new_files:
            print("✨ 모든 파일이 이미 최신 상태입니다. (추가 적재 없음)")
            return

        print(f"📦 총 {len(new_files)}개의 새로운 로그 파일을 발견했습니다. 적재 시작...")
        total_docs = 0
        for file_path in new_files:
            filename = os.path.basename(file_path)
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            if not data: continue

            base_id = os.path.splitext(filename)[0]
            raw_documents = [item["document"] for item in data]
            raw_metadatas = [item["metadata"] for item in data]

            safe_documents = []
            safe_metadatas = []
            MAX_DOC_CHARS = 4000
            MAX_META_CHARS = 5000

            for doc, meta in zip(raw_documents, raw_metadatas):
                safe_documents.append(str(doc)[:MAX_DOC_CHARS])
                safe_meta = meta.copy() if meta else {}
                safe_meta['source_file'] = filename
                safe_metadatas.append(_sanitize_chroma_metadata(safe_meta, max_chars=MAX_META_CHARS))

            ids = [f"{base_id}_{i}" for i in range(len(data))]
            print(f"🔄 '{filename}' 임베딩 중... ({len(safe_documents)}개 지식)")

            BATCH_SIZE = 100
            import gc
            for i in range(0, len(safe_documents), BATCH_SIZE):
                batch_docs = safe_documents[i:i+BATCH_SIZE]
                batch_metas = safe_metadatas[i:i+BATCH_SIZE]
                batch_ids = ids[i:i+BATCH_SIZE]
                batch_embeddings = self.embed_model.encode(batch_docs, batch_size=16, convert_to_numpy=True).tolist()
                self.collection.add(embeddings=batch_embeddings, documents=batch_docs, metadatas=batch_metas, ids=batch_ids)
                del batch_embeddings
                gc.collect()
                if torch.cuda.is_available(): torch.cuda.empty_cache()
                elif torch.backends.mps.is_available(): torch.mps.empty_cache()

            total_docs += len(safe_documents)
            del raw_documents, raw_metadatas, safe_documents, safe_metadatas, ids
            gc.collect()
        print(f"\n✅ 지식 창고 업데이트 완료! (총 {total_docs}개 조각 추가됨)")

    def _get_domain_specific_guideline(self, query, intents, target_log_types):
        guidelines = []
        query_lower = query.lower()

        if any(k in query_lower for k in ["spacex", "starlink", "ntn", "스페이스엑스"]):
            spacex_rule = self.prompts.get('SpaceX', "")
            if spacex_rule: guidelines.append(f"### [🚨 위성 통신 특수 규칙 - SpaceX]\n{spacex_rule}")
        elif any(k in query_lower for k in ["tiantong", "티엔통", "천통", "at command"]):
            tiantong_rule = self.prompts.get('Tiantong', "")
            if tiantong_rule: guidelines.append(f"### [🚨 위성 통신 특수 규칙 - Tiantong]\n{tiantong_rule}")
        else:
            base_p = self.prompts.get('base_persona', "")
            if base_p: guidelines.append(f"### [기본 분석 원칙]\n{base_p}")

        return "\n\n".join(guidelines)

    # 🚨 [신규 추가] 소형 LLM 인지 과부하 차단용 텍스트 다이어트 헬퍼 함수
    def _clean_log_payload(self, text: str) -> str:
        """JSON 특수문자, 대괄호 노이즈 및 과도한 raw_logs 블록을 날려
        2B 모델의 어텐션 붕괴를 영구 방어합니다.
        """
        if not text:
            return ""

        # 1. raw_logs 패턴 원천 차단 (배열/오브젝트 구조 통째로 제거)
        text = re.sub(r'"raw_logs"\s*:\s*\[.*?\]', '"raw_logs": "[OMITTED_FOR_LLM_DIET]"', text, flags=re.DOTALL)
        text = re.sub(r'"raw_logs"\s*:\s*\{.*?\}', '"raw_logs": "[OMITTED_FOR_LLM_DIET]"', text, flags=re.DOTALL)

        # 2. 분석 팩트 등 날것의 JSON 형태가 잔존할 경우 가독성 높은 텍스트화
        try:
            # 완벽한 JSON일 경우 가볍게 파싱 후 - key: value 전환
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

    def ask(self, user_query, current_file=None, chat_history=None, top_k=8, health_kpi=None, is_bench=False):
        current_base = current_file.replace("_payload.json", "") if current_file else "Unknown"

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

        if "Tiantong_Satellite" in intents:
            selected_tools = ["get_tiantong_satellite_analytics"]
            target_log_types = ["Satellite_AT_Command"]

        domain_guidelines = self._get_domain_specific_guideline(search_query, intents, target_log_types)

        tool_facts_list = []
        if current_base != "Unknown" and selected_tools:
            for tool_name in selected_tools:
                tool_fn = self.tool_registry.get(tool_name)
                if tool_fn:
                    try: tool_facts_list.append(f"[{tool_name} 분석 팩트]:\n{tool_fn(current_base)}")
                    except Exception as e: print(f"Tool 실행 에러 ({tool_name}): {e}")

        tool_facts = "\n\n".join(tool_facts_list) if tool_facts_list else "매칭된 도구 분석 결과가 없습니다."

        # 🚨 [방어 코드 주입 1] 도구 분석 결과 팩트 다이어트
        tool_facts = self._clean_log_payload(tool_facts)

        if health_kpi:
            # KPI 스탯도 안전하게 정제해서 병합
            sanitized_kpi = self._clean_log_payload(health_kpi)
            tool_facts = f"=== [단말 전반 KPI 상태] ===\n{sanitized_kpi}\n\n=== [세부 도구 분석 팩트] ===\n{tool_facts}"

        conditions = []
        if current_file: conditions.append({"source_file": current_file})
        if target_log_types:
            if len(target_log_types) == 1: conditions.append({"log_type": target_log_types[0]})
            else: conditions.append({"log_type": {"$in": target_log_types}})

        where_filter = None
        if len(conditions) == 1: where_filter = conditions[0]
        elif len(conditions) > 1: where_filter = {"$and": conditions}

        query_embedding = self.embed_model.encode(search_query).tolist()
        results = self.collection.query(
            query_embeddings=[query_embedding],
            n_results=top_k,
            where=where_filter
        )

        formatted_logs = self._format_results(results)

        # 🚨 [방어 코드 주입 2] ChromaDB에서 서치해온 원본 로그 스니펫 및 뭉텅이 데이터 정제
        formatted_logs = self._clean_log_payload(formatted_logs)

        prompt = (
            f"{self.system_role_prompt}\n\n"
            f"{domain_guidelines}\n\n"
            f"=== [분석 팩트 모음] ===\n{tool_facts}\n\n"
            f"=== [검색된 관련 로그] ===\n{formatted_logs}\n\n"
            f"사용자 질문: {user_query}"
        )

        # 🚨 튜플 반환으로 생각 과정(Thinking) 함께 받기
        answer, thinking = self._call_llm(prompt, is_bench=is_bench)

        doc_ids = results['ids'][0] if results and results.get('ids') else []
        meta_list = results['metadatas'][0] if results and results.get('metadatas') else []

        try:
            combined_context = f"=== [분석 팩트 모음] ===\n{tool_facts}\n\n=== [검색된 관련 로그]===\n{formatted_logs}"
            log_rag_for_evaluation(query=user_query, context=combined_context, answer=answer, guideline=domain_guidelines, model_name=self.llm_model_name)
        except Exception: pass

        # 🚨 UI 전달을 위해 4번째 인자로 thinking 반환
        return answer, doc_ids, meta_list, thinking

    def get_all_files(self):
        results = self.collection.get(include=["metadatas"])
        if not results or not results["metadatas"]: return []
        files = set(m["source_file"] for m in results["metadatas"] if m and "source_file" in m)
        return sorted(list(files))

    def reset_db(self):
        try:
            results = self.collection.get()
            if results and results.get("ids"):
                self.collection.delete(ids=results["ids"])
                print("[DEBUG] DB 초기화 완료: 기존 데이터 삭제됨")
            else:
                print("[DEBUG] DB가 이미 비어있어 삭제를 건너뜁니다.")
            return True
        except Exception as e:
            print(f"[ERROR] DB 초기화 중 오류 발생: {e}")
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
                if real_logs: snippet = "\n".join(real_logs[-5:])
            except: pass
            formatted.append(f"[자료 {i+1} - {meta.get('log_type')}]\n메타정보: {clean_meta}\n요약: {doc}\n원본 로그 스니펫:\n{snippet}")
        return "\n\n".join(formatted)

    def _call_llm(self, prompt: str, is_bench=False) -> tuple[str, str]:
        """Ollama API를 호출하고 최종 답변과 생각 과정(Thinking)을 분리하여 반환합니다."""
        import ollama
        cfg = self.model_config_registry.get(
            self.llm_model_name,
            self.model_config_registry.get("default")
        ).copy()

        if is_bench:
            cfg["num_ctx"] = 8192
        is_think = False
        if self.llm_model_name.startswith("gemma4"): is_think = True
        try:
            res = ollama.chat(
                model=self.llm_model_name,
                messages=[{'role': 'user', 'content': prompt}],
                options=cfg,
                think=is_think
            )

            raw_content = res['message'].get('content', '').strip()
            thinking = res['message'].get('reasoning', '')
            clean_content = raw_content

            if not thinking:
                think_match = re.search(r'<think>(.*?)</think>', raw_content, flags=re.DOTALL | re.IGNORECASE)
                if think_match:
                    thinking = think_match.group(1).strip()
                    clean_content = re.sub(r'<think>.*?</think>', '', raw_content, flags=re.DOTALL | re.IGNORECASE).strip()
                else:
                    channel_match = re.search(r'<\|channel>thought(.*?)(<channel\|>|<\/|\|>|$)', raw_content, flags=re.DOTALL)
                    if channel_match:
                        thinking = channel_match.group(1).strip()
                        clean_content = re.sub(r'<\|channel>thought.*?<channel\|>', '', raw_content, flags=re.DOTALL).strip()

            if clean_content.startswith('<unused'):
                clean_content = "분석 결과 생성 중 모델이 일찍 종료되었습니다. (Context 가 부족할 수 있습니다.)"

            if not clean_content and thinking:
                clean_content = "분석 과정(Thinking)은 완료되었으나, 최종 답변이 비어있습니다. AI의 생각 과정을 참고해주세요."

            return clean_content, thinking

        except Exception as e:
            return f"LLM 추론 중 에러가 발생했습니다: {str(e)}", ""

    def save_knowledge(self, target_ids, feedback, severity="Normal", **kwargs):
        try:
            import uuid
            doc_id = str(uuid.uuid4())
            target_ids_str = ",".join(target_ids) if isinstance(target_ids, list) else str(target_ids)
            metadata = {"target_ids": target_ids_str, "solution": feedback, "severity": severity, "type": "expert_knowledge"}
            self.knowledge_collection.add(documents=[feedback], metadatas=[metadata], ids=[doc_id])
            print(f"💾 [Knowledge Save] 지식 DB 박제 완료! (ID: {doc_id}, Severity: {severity})")
            return True
        except Exception as e:
            print(f"❌ 지식 저장 실패: {e}")
            return False

    def _extract_json_object(self, text: str) -> dict:
        if not text: raise ValueError("empty LLM routing response")
        text = text.strip()
        text = re.sub(r"^```json\s*", "", text)
        text = re.sub(r"^```\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match: raise ValueError(f"No JSON object found in response: {text[:300]}")
        return json.loads(match.group(0))

    def _get_llm_routing(self, query: str) -> dict:
        import ollama
        allowed_tools = set()
        allowed_log_types = set()
        allowed_intents = set(self.routing_map.keys())

        for intent, data in self.routing_map.items():
            allowed_tools.update(data.get("tools", []))
            allowed_log_types.update(data.get("log_types", []))

        prompt = f"""
    너는 Android Telephony 로그 분석 라우터다.
    사용자 질문을 보고 필요한 intent/tools/log_types를 JSON으로만 반환하라.
    사용 가능한 intent: {sorted(list(allowed_intents))}
    사용 가능한 tools: {sorted(list(allowed_tools))}
    사용 가능한 log_types: {sorted(list(allowed_log_types))}
    반드시 JSON만 출력: {{"intents": [], "tools": [], "log_types": [], "reason": ""}}
    사용자 질문: {query}
    """
        try:
            res = ollama.chat(
                model=self.llm_model_name,
                messages=[{"role": "user", "content": prompt}],
                format="json",
                options={"num_ctx": 4096, "temperature": 0.0},
            )
            content = res["message"]["content"].strip()
            content = re.sub(r'<think>.*?</think>', '', content, flags=re.DOTALL | re.IGNORECASE).strip()

            parsed = self._extract_json_object(content)
            return {
                "intents": sorted(list(set(parsed.get("intents", [])) & allowed_intents)),
                "tools": sorted(list(set(parsed.get("tools", [])) & allowed_tools)),
                "log_types": sorted(list(set(parsed.get("log_types", [])) & allowed_log_types)),
                "reason": parsed.get("reason", ""),
                "raw": content,
            }
        except Exception as e:
            return {"intents": [], "tools": [], "log_types": [], "reason": f"LLM routing failed: {e}", "raw": content if "content" in locals() else ""}

    def _get_hybrid_routing(self, query: str) -> dict:
        semantic = self._get_semantic_routing(query)
        llm_route = self._get_llm_routing(query)
        merged_intents = set(semantic.get("intents", []))
        merged_tools = set(semantic.get("tools", []))
        merged_log_types = set(semantic.get("log_types", []))
        merged_intents.update(llm_route.get("intents", []))
        merged_tools.update(llm_route.get("tools", []))
        merged_log_types.update(llm_route.get("log_types", []))
        return {
            "intents": sorted(merged_intents), "tools": sorted(merged_tools), "log_types": sorted(merged_log_types),
            "semantic_routing": semantic, "llm_routing": llm_route, "routing_mode": "hybrid",
        }
