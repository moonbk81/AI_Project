import os
import json
import glob
import chromadb
import torch
import numpy as np
import re
import agent_tools

from sentence_transformers import SentenceTransformer
from core.config import ROUTING_MAP, SYSTEM_PROMPTS, PROMPTS

class RilRagChat:
    def __init__(self, db_path="./chroma_db", collection_name="ril_logs"):
        print("🚀 [시스템 초기화] RAG 시스템을 부팅합니다...")

        # 1. Vector DB 초기화
        self.chroma_client = chromadb.PersistentClient(path=db_path)
        self.collection = self.chroma_client.get_or_create_collection(name=collection_name)
        self.knowledge_collection = self.chroma_client.get_or_create_collection(name="engineer_knowledge_base")

        # Mac(MPS) 또는 Ubuntu(CUDA) 환경에 맞게 디바이스 자동 설정
        device = "cuda" if torch.cuda.is_available() else "mps" if torch.backends.mps.is_available() else "cpu"
        # 2. 임베딩 모델 로드 (오프라인 경로 또는 허깅페이스 repo)
        if device == "cuda" or device == "cpu":
            embed_model_path = "/home/bongki81/project/AI_Project/bge-m3-offline"
        else:
            embed_model_path = "BAAI/bge-m3"
        print(f"📦 임베딩 모델 로드 중... ({embed_model_path})")
        self.embed_model = SentenceTransformer(embed_model_path, device=device)

        # 3. LLM 로드 (Gemma-2b)
        self.llm_model_name = 'gemma2:9b'  # ✅ 외부에서 접근할 수 있도록 인스턴스 변수로 선언
        print(f" LLM 연결 준비 중...(Local Ollama - {self.llm_model_name})")
        print(f"✅ 시스템 준비 완료! (사용 디바이스: {device})\n")
        self._load_config()

        self.tool_registry = {
            "get_cs_call_analytics": agent_tools.get_cs_call_analytics,
            "get_ps_ims_call_analytics": agent_tools.get_ps_ims_call_analytics,
            "get_network_oos_analytics": agent_tools.get_network_oos_analytics,
            "get_dns_latency_analytics": agent_tools.get_dns_latency_analytics,
            "get_battery_thermal_analytics": getattr(agent_tools, 'get_battery_thermal_analytics', None), # 없는 함수 방어 로직
            "get_crash_anr_analytics": getattr(agent_tools, 'get_crash_anr_analytics', None),
            "get_radio_power_analytics": getattr(agent_tools, 'get_radio_power_analytics', None),
            "get_data_stall_and_recovery_analytics": getattr(agent_tools, 'get_data_stall_and_recovery_analytics', None),
        }

    def _load_config(self):
        try:
            self.routing_map = ROUTING_MAP
            self.system_role_prompt = SYSTEM_PROMPTS
            self.prompts = PROMPTS
        except Exception as e:
            self.routing_map = {}
            self.system_role_prompt = "시스템 프롬프트를 불러올 수 없습니다."
            self.rompts = {}

    def _get_semantic_routing(self, query):
        """config.yaml의 설정을 기반으로 지능형 라우팅 수행"""
        chunks = [chunk.strip() for chunk in re.split(r'[\n\.]', query) if len(chunk.strip()) > 5]
        if not chunks:
            chunks = [query]

        # 1. 각 카테고리별로 청크들과 비교하여 '최고 점수'를 계산
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

        # 2. 점수(max_sim)가 높은 순서대로 내림차순 정렬
        category_scores.sort(key=lambda x: x[1], reverse=True)

        selected_tools = set()
        selected_log_types = set()
        selected_intents = set()

        threshold = 0.52
        multi_threshold = 0.50

        # 점수 로그 저장용
        routing_scores = {
            category: float(score)
            for category, score, _ in category_scores
        }

        if not category_scores:
            print("⚠️ [Fallback] routing_map이 비어 있어 범용 기본 로그를 조회합니다.")
            selected_intents.add("Fallback_General")
            selected_tools.update([
                "get_cs_call_analytics", "get_network_oos_analytics", "get_dns_latency_analytics",
            ])
            selected_log_types.update([
                "Call_Session", "OOS_Event", "Signal_Level", "Network_Timeline_Stat", "Network_DNS_Issue",
            ])

            return {
                "intents": list(selected_intents), "tools": list(selected_tools), "log_types": list(selected_log_types), "scores": routing_scores, "top_matches": [],
            }

        top1_cat, top1_score, top1_data = category_scores[0]
        print(f"\n[Semantic Router] 🥇 Top-1 의도: {top1_cat} (유사도: {top1_score:.3f})")

        # 3. Top-1 점수가 낮으면 fallback
        # 기존처럼 여기서 바로 return 하지 말고, selected_*에 기본값을 넣고 마지막 dict return으로 보낸다.
        if top1_score < threshold:
            print("⚠️ [Fallback] 명확한 의도를 찾지 못해 범용 기본 로그(통화/망/타임라인)만 조회합니다.")

            selected_intents.add("Fallback_General")
            selected_tools.update([
                "get_cs_call_analytics", "get_network_oos_analytics", "get_dns_latency_analytics",
            ])
            selected_log_types.update([
                "Call_Session", "OOS_Event", "Signal_Level", "Network_Timeline_Stat", "Network_DNS_Issue",
            ])

        else:
            # 4. 1등 의도 채택
            selected_intents.add(top1_cat)
            selected_tools.update(top1_data["tools"])
            selected_log_types.update(top1_data["log_types"])

            # 5. Top-2 복합 의도 병합
            if len(category_scores) > 1:
                top2_cat, top2_score, top2_data = category_scores[1]

                if top2_score >= multi_threshold:
                    print(f"🔗 [Multi-Intent] 🥈 Top-2 복합 의도 병합: {top2_cat} (유사도: {top2_score:.3f})")
                    selected_intents.add(top2_cat)
                    selected_tools.update(top2_data["tools"])
                    selected_log_types.update(top2_data["log_types"])

        # 6. 혹시 selected가 비어 있으면 fallback
        if not selected_tools and not selected_log_types:
            print("⚠️ [Fallback] 명확한 의도를 찾지 못해 범용 기본 로그를 조회합니다.")

            selected_intents.add("Fallback_General")
            selected_tools.update([
                "get_cs_call_analytics", "get_network_oos_analytics", "get_dns_latency_analytics",
            ])
            selected_log_types.update([
                "Call_Session", "OOS_Event", "Signal_Level", "Network_Timeline_Stat", "Network_DNS_Issue",
            ])

                # 7. 키워드 override
        query_lower = query.lower()

        # [Radio Power 오버라이드]
        if any(keyword in query_lower for keyword in [
            "비행기 모드", "airplane mode", "flight mode",
            "radio power", "모뎀 전원", "라디오 파워",
        ]):
            selected_intents.add("Radio_Power")
            if "Radio_Power" in self.routing_map:
                selected_tools.update(self.routing_map["Radio_Power"].get("tools", []))
                selected_log_types.update(self.routing_map["Radio_Power"].get("log_types", []))

        # [DNS / Data Stall 오버라이드]
        if any(keyword in query_lower for keyword in [
            "dns", "데이터", "인터넷", "패킷", "ping", "핑", "스톨", "먹통",
        ]):
            selected_intents.add("DNS_Latency")
            if "DNS_Latency" in self.routing_map:
                # 하드코딩 대신 config.yaml(routing_map)의 배열을 그대로 가져와서 붙임!
                selected_tools.update(self.routing_map["DNS_Latency"].get("tools", []))
                selected_log_types.update(self.routing_map["DNS_Latency"].get("log_types", []))

        # 8. Top 점수 로그용 정리
        top_matches = [
            {
                "intent": category,
                "score": float(score),
            }
            for category, score, _ in category_scores[:3]
        ]

        # 9. 이제 tuple이 아니라 dict로 반환
        return {
            "intents": sorted(list(selected_intents)),
            "tools": sorted(list(selected_tools)),
            "log_types": sorted(list(selected_log_types)),
            "scores": routing_scores,
            "top_matches": top_matches,
        }

    def ingest_folder(self, folder_path="./payloads"):
        """payloads 폴더 내의 새로운 JSON 파일만 선별하여 적재합니다."""
        if not os.path.exists(folder_path):
            os.makedirs(folder_path, exist_ok=True)
            print(f"📂 '{folder_path}' 폴더가 생성되었습니다. 분석된 JSON 파일을 넣어주세요.")
            return

        json_files = glob.glob(os.path.join(folder_path, "*.json"))
        if not json_files:
            print(f"⚠️ '{folder_path}' 폴더에 적재할 데이터가 없습니다.")
            return

        # DB에서 이미 처리된 파일 목록(source_file) 조회
        existing_data = self.collection.get(include=["metadatas"])
        processed_files = set()
        if existing_data and existing_data["metadatas"]:
            for meta in existing_data["metadatas"]:
                if meta and "source_file" in meta:
                    processed_files.add(meta["source_file"])

        # 신규 파일만 필터링
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

            if not data:
                continue

            base_id = os.path.splitext(filename)[0]
            raw_documents = [item["document"] for item in data]
            raw_metadatas = [item["metadata"] for item in data]

            safe_documents = []
            safe_metadatas = []

            # ==========================================
            # 🚨 [최종 방어막] MPS 메모리 폭발 & DB 용량 초과 완벽 차단
            # ==========================================
            MAX_DOC_CHARS = 4000  # 약 1000 토큰 (BGE-M3 최적 효율 및 MPS 메모리 안전선)
            MAX_META_CHARS = 5000 # 메타데이터(원본 로그) 길이 제한 (ChromaDB SQLite 보호)

            for doc, meta in zip(raw_documents, raw_metadatas):
                # 1. 문서 길이 자르기 (O(N^2) 어텐션 메모리 폭발 완벽 차단)
                safe_documents.append(str(doc)[:MAX_DOC_CHARS])

                # 2. 메타데이터 안전 처리
                safe_meta = meta.copy() if meta else {}
                safe_meta['source_file'] = filename

                # 메타데이터 안의 텍스트(예: cross_context_logs)가 너무 길면 무조건 자름
                for k, v in safe_meta.items():
                    if isinstance(v, str) and len(v) > MAX_META_CHARS:
                        safe_meta[k] = v[:MAX_META_CHARS] + "\n...[TRUNCATED_BY_SYSTEM: TOO_LONG]"

                safe_metadatas.append(safe_meta)

            ids = [f"{base_id}_{i}" for i in range(len(data))]

            print(f"🔄 '{filename}' 임베딩 중... ({len(safe_documents)}개 지식, 강력한 길이 제한 적용됨)")
            # embeddings = self.embed_model.encode(documents).tolist()
            embeddings = self.embed_model.encode(safe_documents, batch_size=32).tolist()
            BATCH_SIZE = 100
            for i in range(0, len(safe_documents), BATCH_SIZE):
                self.collection.add(
                    embeddings=embeddings[i:i+BATCH_SIZE],
                    documents=safe_documents[i:i+BATCH_SIZE],
                    metadatas=safe_metadatas[i:i+BATCH_SIZE],
                    ids=ids[i:i+BATCH_SIZE]
                )
            total_docs += len(safe_documents)

        print(f"\n✅ 지식 창고 업데이트 완료! (총 {total_docs}개 조각 추가됨)")

    def ask(self, user_query, current_file=None, chat_history=None, top_k=15, health_kpi=None):
        """Semantic Router 기반의 Plan -> Act -> Retrieve -> Reason 파이프라인"""
        current_base = current_file.replace("_payload.json", "") if current_file else "Unknown"

        # [STAGE 1: Search Query Augmentation]
        search_query = user_query
        if len(user_query) < 15 and chat_history:
            last_msg = next((msg['content'] for msg in reversed(chat_history) if msg['role'] == 'user'), "")
            search_query = f"{last_msg} 관련 후속 질문: {user_query}"

        # [STAGE 2: Semantic Intent Routing]
        routing_result = self._get_semantic_routing(search_query)
        selected_tools = routing_result.get("tools", [])
        target_log_types = routing_result.get("log_types", [])

        # [STAGE 3: Act - Tool Execution]
        tool_facts_list = []
        if current_base != "Unknown" and selected_tools:
            for tool_name in selected_tools:
                tool_fn = self.tool_registry.get(tool_name)
                if tool_fn:
                    try:
                        tool_facts_list.append(f"[{tool_name} 분석 팩트]:\n{tool_fn(current_base)}")
                    except Exception as e:
                        print(f"Tool 실행 에러 ({tool_name}): {e}")
                else:
                    print(f"⚠️ 경고: {tool_name} 함수가 agent_tools에 구현되지 않았습니다.")

        tool_facts = "\n\n".join(tool_facts_list) if tool_facts_list else "매칭된 도구 분석 결과가 없습니다."

        # 🚨 [버그 수정 2] health_kpi를 LLM 프롬프트용 팩트에 합체!
        if health_kpi:
            tool_facts = f"=== [단말 전반 KPI 상태] ===\n{health_kpi}\n\n=== [세부 도구 분석 팩트] ===\n{tool_facts}"

        # [STAGE 4: Retrieve - Vector DB Filtered Search]
        conditions = []
        if current_file:
            conditions.append({"source_file": current_file})
        if target_log_types:
            if len(target_log_types) == 1:
                conditions.append({"log_type": target_log_types[0]})
            else:
                conditions.append({"log_type": {"$in": target_log_types}})

        where_filter = None
        if len(conditions) == 1:
            where_filter = conditions[0]
        elif len(conditions) > 1:
            where_filter = {"$and": conditions}

        query_embedding = self.embed_model.encode(search_query).tolist()
        results = self.collection.query(
            query_embeddings=[query_embedding],
            n_results=top_k,
            where=where_filter
        )

        # [STAGE 5: Reason - Prompt Construction & LLM Inference]
        formatted_logs = self._format_results(results)

        prompt = (
            f"{self.system_role_prompt}\n\n"
            f"=== [분석 팩트 모음] ===\n{tool_facts}\n\n"
            f"=== [검색된 관련 로그] ===\n{formatted_logs}\n\n"
            f"사용자 질문: {user_query}"
        )

        answer = self._call_llm(prompt)

        doc_ids = results['ids'][0] if results and results.get('ids') else []
        meta_list = results['metadatas'][0] if results and results.get('metadatas') else []

        return answer, doc_ids, meta_list

    def get_all_files(self):
        """DB에 적재된 모든 유니크한 파일 목록을 반환합니다."""
        results = self.collection.get(include=["metadatas"])
        if not results or not results["metadatas"]:
            return []
        # 메타데이터에서 source_file 이름만 추출하여 중복 제거
        files = set(m["source_file"] for m in results["metadatas"] if m and "source_file" in m)
        return sorted(list(files))

    def reset_db(self):
        try:
            results = self.collection.get()

            # 🚨 [수정] ids 리스트가 존재하고, 비어있지 않을 때만 삭제 실행
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
        """Vector DB 검색 결과를 LLM이 읽기 쉬운 구조로 포맷팅합니다."""
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
                    snippet = "\n".join(real_logs[-5:]) # 핵심 스니펫 5줄만 제공
            except:
                pass

            formatted.append(f"[자료 {i+1} - {meta.get('log_type')}]\n메타정보: {clean_meta}\n요약: {doc}\n원본 로그 스니펫:\n{snippet}")

        return "\n\n".join(formatted)

    def _call_llm(self, prompt: str) -> str:
        """Ollama API를 통해 실제 LLM 추론을 수행합니다."""
        import ollama
        try:
            res = ollama.chat(model=self.llm_model_name, messages=[
                {'role': 'user', 'content': prompt}
            ])
            return res['message']['content']
        except Exception as e:
            return f"LLM 추론 중 에러가 발생했습니다: {str(e)}"

    def save_knowledge(self, target_ids, feedback, severity="Normal", **kwargs):
        """
        웹 UI에서 전달받은 파라미터(대상 로그 ID, 엔지니어 코멘트, 심각도)를
        사내 지식 베이스(ChromaDB)에 영구 저장합니다.
        """
        try:
            import uuid
            doc_id = str(uuid.uuid4())

            # target_ids가 리스트일 경우 문자열로 합쳐서 저장
            if isinstance(target_ids, list):
                target_ids_str = ",".join(target_ids)
            else:
                target_ids_str = str(target_ids)

            # 메타데이터: UI에서 넘어온 severity와 타겟 로그 ID를 모두 보존
            metadata = {
                "target_ids": target_ids_str,
                "solution": feedback,
                "severity": severity,
                "type": "expert_knowledge"
            }

            # 지식 베이스 전용 컬렉션에 적재 (텍스트 본문은 엔지니어 피드백)
            self.knowledge_collection.add(
                documents=[feedback],
                metadatas=[metadata],
                ids=[doc_id]
            )

            print(f"💾 [Knowledge Save] 지식 DB 박제 완료! (ID: {doc_id}, Severity: {severity})")
            return True
        except Exception as e:
            print(f"❌ 지식 저장 실패: {e}")
            return False
