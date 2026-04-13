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
        # 1. 질문 임베딩 생성
        query_embedding = self.embed_model.encode(user_query).tolist()
        user_query_lower = user_query.lower()
        
        # 2. 검색 필터 구성 (현재 활성 파일 기준)
        where_filter = {}
        if current_file:
            where_filter["source_file"] = current_file
            
        # 질문 내용에 따른 로그 타입 필터 (배터리, 통화 등)
        if "battery" in user_query_lower or "배터리" in user_query_lower or "전력" in user_query_lower:
            if current_file:
                where_filter = {"$and": [{"source_file": current_file}, {"log_type": "Battery_Drain_Report"}]}
            else:
                where_filter["log_type"] = "Battery_Drain_Report"
        elif "call" in user_query_lower or "콜" in user_query_lower or "통화" in user_query_lower:
            if current_file:
                where_filter = {"$and": [{"source_file": current_file}, {"log_type": "Call_Session"}]}
            else:
                where_filter["log_type"] = "Call_Session"
        elif "radio" in user_query_lower or "전원" in user_query_lower or "power" in user_query_lower:
            if current_file:
                where_filter = {"$and": [{"source_file": current_file}, {"log_type": "Radio_Power_Event"}]}
            else:
                where_filter["log_type"] = "Radio_Power_Event"
        elif "oos" in user_query_lower or "이탈" in user_query_lower or "망" in user_query_lower or "out of service" in user_query_lower \
                or "no service" in user_query_lower or "signal lost" in user_query_lower:
            if current_file:
                where_filter = {"$and": [{"source_file": current_file}, {"log_type": "OOS_Event"}]}
            else:
                where_filter["log_type"] = "OOS_Event"
        elif "crash" in user_query_lower or "fatal" in user_query_lower:
            if current_file:
                where_filter = {"$and": [{"source_file": current_file}, {"log_type": "App_Crash"}]}
            else:
                where_filter["log_type"] = "App_Crash"
        elif "anr" in user_query_lower or "not responding" in user_query_lower:
            if current_file:
                where_filter = {"$and": [{"source_file": current_file}, {"log_type": "App_ANR"}]}
            else:
                where_filter["log_type"] = "App_ANR"

        # 3. Vector DB 검색 실행
        results = self.collection.query(
            query_embeddings=[query_embedding],
            n_results=5,
            where=where_filter if where_filter else None
        )
        
        found_count = len(results['documents'][0]) if results and results.get('documents') else 0
        print(f"\n[DEBUG] 현재 필터: {where_filter}")
        print(f"[DEBUG] DB가 가져온 문서 개수: {found_count} 개\n")

        # 4. 🚀 [핵심] LLM 토큰 폭발 방지: 원본 로그 제거하고 컨텍스트 조립
        context_blocks = []
        if results and results['documents'] and len(results['documents'][0]) > 0:
            for doc, meta in zip(results['documents'][0], results['metadatas'][0]):
                
                # 메타데이터에서 'raw_' 로 시작하는 거대한 원본 로그를 싹 쳐냅니다.
                clean_meta = {
                    k: v for k, v in meta.items() 
                    if not k.startswith("raw_") and k != "source_file"
                }
                
                # LLM에게는 다이어트된 텍스트만 먹입니다.
                context_blocks.append(
                    f"[요약 본문]\n{doc}\n"
                    f"[핵심 메타정보]\n{clean_meta}"
                )
        else:
            context_blocks.append("관련된 로그를 DB에서 찾지 못했습니다.")

        final_context = "\n\n---\n\n".join(context_blocks)

        # 5. 시스템 프롬프트 구성 (과거 대화 기억 허용 + 배터리/통화 맞춤 가이드)
        system_prompt = (
            "너는 안드로이드 무선 통신(RIL) 로그를 분석하는 최고 수준의 수석 엔지니어다.\n\n"
            "[답변 작성 규칙]\n"
            "1. 기본적으로 제공된 [현재 분석 대상 로그]를 바탕으로 답변해라.\n"
            "2. 만약 제공된 로그에 당장 관련된 정보가 부족하더라도, 사용자의 질문이 이전 대화의 연장선이라면 [과거 대화 내역]을 적극적으로 참고하여 흐름에 맞게 답변해라.\n"
            "3. 절대 '정보가 없다'고 성급하게 말하지 말고, 문맥을 추론해서 아는 선까지 최대한 설명해라.\n"
            "4. 배터리 소모 분석 시: 측정 기간, 화면 켜짐/꺼짐 시간, 신호 세기 분포를 정리하고 'telephony_drain_evaluation'을 바탕으로 광탈 여부를 브리핑해라.\n"
            "5. 통화/망 에러 분석 시: 핵심 이벤트 발생 시간, 슬롯, 실패 원인을 명확히 제시하고 동시간대 교차 로그를 연결해라."
        )

        import requests
        url = "http://localhost:11434/api/chat"

        # 6. 이전 대화 내역(Chat History) 조립
        history_text = ""
        if chat_history:
            # 최근 3번의 대화만 가져와서 문맥 유지 (토큰 절약)
            recent_history = chat_history[-3:]
            history_lines = []
            for msg in recent_history:
                role = "User" if msg["role"] == "user" else "AI"
                history_lines.append(f"{role}: {msg['content']}")
            history_text = "\n".join(history_lines)

        # 7. 최종 LLM 프롬프트 생성 (현재 로그 + 과거 대화 + 질문)
        prompt = (
            f"{system_prompt}\n\n"
            f"[과거 대화 내역]\n{history_text if history_text else '없음'}\n\n"
            f"[현재 분석 대상 로그]\n{final_context}\n\n"
            f"질문: {user_query}"
        )

        # 8. LLM 호출 (Gemma 또는 사용 중인 모델의 API 호출부에 맞게 조정하세요)
        # (※ 이 부분은 Mr. 문님의 기존 모델 호출 방식과 동일하게 유지하시면 됩니다.)
        try:
            import ollama
            res = ollama.chat(model='gemma:2b', messages=[{'role': 'user', 'content':prompt}])
            answer = res['message']['content']
        except Exception as e:
            answer = f"LLM 답변 생성 중 에러가 발생했습니다: {str(e)}"

        doc_ids = results['ids'][0] if results and results.get('ids') else []
        meta_list = results['metadatas'][0] if results and results.get('metadatas') else []

        return answer, doc_ids, meta_list

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
