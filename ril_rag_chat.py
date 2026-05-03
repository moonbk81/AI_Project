import os
import json
import glob
from turtle import st
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

        selected_tools = set()
        selected_log_types = set()
        threshold = 0.52

        print(f"\n[Semantic Router] {len(chunks)}개의 청크로 분할하여 검사 시작...")

        for category, data in self.routing_map.items():
            intent_vec = self.embed_model.encode(data["desc"])
            max_similarity = 0.0
            best_chunk = ""

            for chunk in chunks:
                chunk_vec = self.embed_model.encode(chunk)
                sim = np.dot(chunk_vec, intent_vec) / (np.linalg.norm(chunk_vec) * np.linalg.norm(intent_vec))
                if sim > max_similarity:
                    max_similarity = sim
                    best_chunk = chunk

            if max_similarity >= threshold:
                print(f"✅ 매칭됨: {category} (최고 점수: {max_similarity:.3f} | 원인 청크: '{best_chunk[:20]}...')")
                selected_tools.update(data["tools"])
                selected_log_types.update(data["log_types"])
            else:
                print(f" └─ 제외: {category} (최고 점수: {max_similarity:.3f})")

        return list(selected_tools), list(selected_log_types)

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
        selected_tools, target_log_types = self._get_semantic_routing(search_query)

        # 🚨 [버그 수정 1] TOOL_REGISTRY 도입: 동적 호출(getattr)의 불안정성 제거
        # 문자열을 실제 함수 객체와 1:1로 안전하게 매핑합니다.
        TOOL_REGISTRY = {
            "get_cs_call_analytics": agent_tools.get_cs_call_analytics,
            "get_ps_ims_call_analytics": agent_tools.get_ps_ims_call_analytics,
            "get_network_oos_analytics": agent_tools.get_network_oos_analytics,
            "get_dns_latency_analytics": agent_tools.get_dns_latency_analytics,
            "get_battery_thermal_analytics": getattr(agent_tools, 'get_battery_thermal_analytics', None), # 없는 함수 방어 로직
            "get_crash_anr_analytics": getattr(agent_tools, 'get_crash_anr_analytics', None),
            "get_radio_power_analytics": getattr(agent_tools, 'get_radio_power_analytics', None),
        }

        # [STAGE 3: Act - Tool Execution]
        tool_facts_list = []
        if current_base != "Unknown" and selected_tools:
            for tool_name in selected_tools:
                tool_fn = TOOL_REGISTRY.get(tool_name)
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

    def save_knowledge(self, log_name, issue_title, solution_text):
        """사내 지식 베이스(DB)에 우수 분석 사례를 저장하는 함수 (향후 고도화 예정)"""
        try:
            print(f"💾 [Knowledge Save] {issue_title} 지식 저장 시도 중...")
            # TODO: 향후 별도의 ChromaDB 컬렉션(knowledge_base)을 만들어 저장하는 로직 추가
            return True
        except Exception as e:
            print(f"❌ 지식 저장 실패: {e}")
            return False

if __name__ == "__main__":
    chat_system = RilRagChat()

    # 시작할 때 자동으로 payloads 폴더를 스캔해서 적재합니다.
    chat_system.ingest_folder()

    print("\n" + "="*60)
    print("🤖 RIL RAG 챗봇이 준비되었습니다. (종료: q, quit, exit)")
    print("="*60)

    while True:
        try:
            query = input("\n[사용자]: ")
            if query.lower() in ['exit', 'quit', 'q']:
                print("챗봇을 종료합니다. 수고하셨습니다!")
                break
            if not query.strip():
                continue

            # 1. AI 분석 요청
            answer, ids, metas = chat_system.ask(query)

            # 2. 결과 출력
            print("\n" + "="*60)
            print("💡 [분석 결과]")
            print(answer)
            print("\n" + "-"*60)
            print("🔎 [참고 원본 로그 (엔지니어 확인용)]")

            for i, meta in enumerate(metas):
                # 저장된 해결책이 있다면 함께 출력
                known_solution = meta.get('known_solution')
                solution_text = f" [💡과거 해결사례 존재]" if known_solution else ""

                print(f"\n--- 참고 자료 {i+1} (시간: {meta.get('time', 'N/A')}, 슬롯: {meta.get('slot', 'N/A')}){solution_text} ---")

                # 과거 해결 사례 출력
                if known_solution:
                    print(f"  👉 과거 분석 기록: {known_solution}")

                # Call/OOS 원본 로그 출력 로직 (다중 fallback)
                raw_data = meta.get('raw_logs', meta.get('raw_context', meta.get('raw_stack', '[]')))
                try:
                    raw_logs = json.loads(raw_data) if isinstance(raw_data, str) else []
                except json.JSONDecodeError:
                    raw_logs = []

                if raw_logs:
                    for log in raw_logs[:5]:
                        print(f"  {log}")
                    if len(raw_logs) > 5:
                        print("  ... (중략) ...")

                # RADIO_POWER 원본 로그 출력 로직
                raw_req = meta.get('raw_request')
                raw_resp = meta.get('raw_response')
                if raw_req or raw_resp:
                    if raw_req: print(f"  [REQ]  {raw_req}")
                    if raw_resp: print(f"  [RESP] {raw_resp}")

                if not raw_logs and not raw_req and not raw_resp:
                    print("  (원본 로그 데이터 없음)")

            print("="*60 + "\n")

            # 3. 지식 저장 (피드백 루프)
            if ids: # 검색된 데이터가 있을 때만 물어봄
                print("\n" + "-"*60)
                print("📝 [사내 지식 베이스(트랙 B) 업데이트]")
                print("이 에러에 대한 '원인'이나 '해결책'을 엔지니어의 시각으로 기록해두면,")
                print("추후 유사한 에러 발생 시 후배들이나 AI가 이 해결책을 참고할 수 있습니다.")

                # y/n이 아니라, 사용자의 주관적인 텍스트를 직접 입력받습니다.
                feedback = input("❓ 엔지니어 코멘트 입력 (저장하지 않으려면 그냥 Enter 입력): ").strip()

                if feedback:
                    # AI의 추출 결과(answer)가 아닌, 엔지니어의 코멘트(feedback)를 DB에 박제!
                    chat_system.save_knowledge(ids, feedback)
                else:
                    print("지식 저장을 건너뜁니다.")

        except KeyboardInterrupt:
            print("\n챗봇을 강제 종료합니다.")
            break
        except Exception as e:
            print(f"\n❌ 오류 발생: {e}")
