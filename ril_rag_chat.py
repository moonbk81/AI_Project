import os
import json
import glob
import chromadb
import torch
from sentence_transformers import SentenceTransformer

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
        self.embed_model = SentenceTransformer(embed_model_path)

        # 3. LLM 로드 (Gemma-2b)
        print(f" LLM 연결 준비 중...(Local Ollama - gemma:2b)")
        print(f"✅ 시스템 준비 완료! (사용 디바이스: {device})\n")

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
            embeddings = self.embed_model.encode(safe_documents, batch_size=2).tolist()
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

    def ask(self, user_query, current_file=None, chat_history=None):
        # 1. 질문 임베딩 생성 및 기본 필터
        query_embedding = self.embed_model.encode(user_query).tolist()
        user_query_lower = user_query.lower()
        base_filter = None
        
        if "call" in user_query_lower or "콜" in user_query_lower or "통화" in user_query_lower:
            base_filter = {"log_type": "Call_Session"}
        elif "oos" in user_query_lower or "이탈" in user_query_lower or "망" in user_query_lower or "out of service" in user_query_lower \
                or "no service" in user_query_lower or "signal lost" in user_query_lower:
            base_filter = {"log_type": "OOS_Event"}
        elif "radio" in user_query_lower or "전원" in user_query_lower or "power" in user_query_lower:
            base_filter = {"log_type": "Radio_Power_Event"}
        elif "crash" in user_query_lower or "fatal" in user_query_lower:
            base_filter = {"log_type": "App_Crash"}
        elif "anr" in user_query_lower or "not responding" in user_query_lower:
            base_filter = {"log_type": "App_ANR"}

        # 2. 투트랙 필터 구성
        where_current = None
        where_past = None

        if current_file:
            print(f"\n🎯 [투트랙 검색] 1. 현재 대상 파일 집중 분석: {current_file}")
            if base_filter:
                where_current = {"$and": [base_filter, {"source_file": current_file}]}
                where_past = {"$and": [base_filter, {"source_file": {"$ne": current_file}}]}
            else:
                where_current = {"source_file": current_file}
                where_past = {"source_file": {"$ne": current_file}}
        else:
            print(f"\n🔍 [일반 검색] 특정 대상 파일 없음. DB 전체 검색 실행")
            where_current = base_filter

        # 3. DB 쿼리 실행
        results_current = self.collection.query(
            query_embeddings=[query_embedding], n_results=5, where=where_current
        ) if where_current else None

        results_past = self.collection.query(
            query_embeddings=[query_embedding], n_results=5, where=where_past
        ) if where_past else None

        # 4. 결과물 조립
        current_context = ""
        past_context = ""
        retrieved_ids = []
        retrieved_metas = []

        if results_current and results_current['documents'] and results_current['documents'][0]:
            current_context = "\n\n".join(results_current['documents'][0])
            retrieved_ids.extend(results_current['ids'][0])
            retrieved_metas.extend(results_current['metadatas'][0])

        if results_past and results_past['documents'] and results_past['documents'][0]:
            past_context = "\n\n".join(results_past['documents'][0])
            retrieved_ids.extend(results_past['ids'][0])
            retrieved_metas.extend(results_past['metadatas'][0])

        if not current_context and not past_context:
            return "검색된 관련 로그가 없습니다.", [], []

        # 5. 투트랙 맞춤형 LLM 프롬프트

        system_prompt = (
            "너는 안드로이드 무선 통신(RIL) 로그를 분석하는 최고 수준의 수석 엔지니어다. "
            "주어진 지문은 [현재 분석 대상 로그]와 [과거 유사 발생 사례]로 나뉘어 있다.\n\n"
            "[답변 작성 규칙 - 반드시 지킬 것]\n"
            "1. 뼈대(Main Session) 먼저 요약: 질문에 해당하는 핵심 이벤트(Call Fail, OOS 등)의 발생 시간, 발생 슬롯(PHONE0/1), 통화 실패 원인(Fail Cause)을 가장 먼저 명확히 제시해라.\n"
            "2. 교차 분석 (Cross-Context): 그 다음, 지문에 동시간대 타 버퍼(main, system 등) 로그가 있다면, 그 시간대에 AP 단이나 시스템 프로세스에 어떤 이상(예: 메모리 부족, 특정 앱 에러)이 있었는지 연결해서 설명해라.\n"
            "3. [과거 유사 사례 참고]에 known_solution이 있다면 답변 마지막에 '💡 [과거 유사 사례 참고]: ~' 형식으로 덧붙여라.\n"
            "4. 무의미한 에러 코드만 나열하지 말고, 사람이 읽기 쉬운 엔지니어 리포트 형식으로 작성해라."
        )

        prompt = f"[현재 분석 대상 로그]\n{current_context}\n\n[과거 유사 발생 사례]\n{past_context}\n\n질문: {user_query}"

        # 6. Ollama API 호출 (Memory 구조 적용)
        import requests
        url = "http://localhost:11434/api/chat"
        
        # 시스템 메시지를 가장 먼저 배치
        messages = [{"role": "system", "content": system_prompt}]
        
        # 웹 앱에서 넘겨준 과거 대화 기록(chat_history)이 있다면 중간에 끼워 넣기
        if chat_history:
            for msg in chat_history:
                # Ollama가 헷갈려할 수 있는 Streamlit 전용 키(references)는 빼고 순수 대화만 전달
                messages.append({"role": msg["role"], "content": msg["content"]})
                
        # 마지막으로 이번 턴의 프롬프트(지문 + 질문) 추가
        messages.append({"role": "user", "content": prompt})

        payload = {
            "model": "gemma:2b",
            "messages": messages,
            "stream": False,
            "options": {"temperature": 0.1, "num_predict": 256}
        }

        try:
            response = requests.post(url, json=payload)
            response.raise_for_status()
            answer = response.json().get("message", {}).get("content", "응답 파싱 실패")
        except Exception as e:
            answer = f"❌ [오류] Ollama 통신 실패: {e}"

        return answer, retrieved_ids, retrieved_metas


    def save_knowledge(self, ids, analysis_result):
        """엔지니어가 컨펌한 분석 결과를 해당 로그들의 메타데이터에 업데이트합니다."""
        print(f"\n💾 총 {len(ids)}개의 로그 조각에 분석 결과를 박제하는 중...")

        existing = self.collection.get(ids=ids, include=["metadatas"])
        updated_metas = []

        for meta in existing["metadatas"]:
            # NoneType 방지 및 새 지식 추가
            current_meta = meta if meta is not None else {}
            current_meta["known_solution"] = analysis_result
            updated_metas.append(current_meta)

        self.collection.update(
            ids=ids,
            metadatas=updated_metas
        )
        print("✅ 지식 저장 완료! 이제 이 로그들은 '해결된 사례'로 분류됩니다.")

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
