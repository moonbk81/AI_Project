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

        # 4. 동적 프롬프트 관리를 위한 템플릿 딕셔너리
        self.prompts = {
            "base_persona": (
                "너는 안드로이드 무선 통신(RIL/Telephony) 및 Network Stack을 분석하는 최고 수준의 수석 엔지니어다.\n\n"
                "[답변 작성 공통 규칙]\n"
                "1. 절대 지어내지 말고, 제공된 [현재 분석 대상 로그]와 메타정보 안에서만 팩트로 답변해라.\n"
                "2. 사용자가 '로그를 보여달라'고 하면 원본 로그 스니펫을 단 한 글자도 바꾸지 말고 그대로 출력해라.\n"
                "3. 과거 해결 사례(known_solution)가 있다면 최우선적으로 참고하여 답변에 반영해라.\n"
            ),
            "log_guidelines": {
                "Call_Session": "- [Call_Session (통화)]: status가 'FAIL/DROP'인 세션을 찾고, 'fail_reason'을 반드시 읽어서 실패 원인을 설명해라. (통화 에러 분석 시 OOS와 혼동 금지)",
                "OOS_Event": "- [OOS_Event (망 이탈)]: voice_reg/data_reg 값이 0이면 '정상', 1 이상이면 '망 이탈/음영'으로 판단해라.",
                "Battery_Drain_Report": "- [Battery_Drain_Report (배터리)]: stats_period와 신호 세기 분포(none/poor 비중)를 바탕으로 배터리 광탈 원인을 진단해라.",
                "Network_Timeline_Stat": "- [Network_Timeline_Stat (시계열)]: DNS 지연 시간(avg)이 평소보다 급증하거나 에러율이 높은 구간을 특정해라.",
                "Network_DNS_Issue": "- [Network_DNS_Issue (DNS 차단)]: is_blocked가 true일 경우 effective_policy를 확인해라. 'BATTERY_SAVER'나 'APP_BACKGROUND'가 포함되어 있다면, 단말의 절전 모드나 백그라운드 데이터 제한 정책에 의해 강제 차단되었음을 명확히 설명해라."
            }
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
         # 1. 🚨 질문 임베딩 생성 (짧은 후속 질문 대응력 강화)
        search_query = user_query
        if len(user_query) < 15 and chat_history:
            # "그럼 배터리는?" 같은 짧은 질문에 이전 문맥을 붙여 벡터 검색 품질을 높입니다.
            last_msg = next((msg['content'] for msg in reversed(chat_history) if msg['role'] == 'user'), "")
            search_query = f"{last_msg} 관련 후속 질문: {user_query}"

        # 1. 질문 임베딩 생성
        query_embedding = self.embed_model.encode(user_query).tolist()
        user_query_lower = user_query.lower()

        # ==========================================
        # 2. 스마트 검색 필터 구성 (조건 자동 조립기)
        # ==========================================
        conditions = []

        # (1) 현재 활성 파일 고정
        if current_file:
            conditions.append({"source_file": current_file})

        # (2) 사용자 의도(질문)에 따른 로그 타입 필터링
        target_log_types = []
        if any(kw in user_query_lower for kw in ["battery", "배터리", "전력", "광탈"]):
            target_log_types.append("Battery_Drain_Report")
        if any(kw in user_query_lower for kw in ["call", "콜", "통화", "전화", "끊김"]):
            target_log_types.append("Call_Session")
        if any(kw in user_query_lower for kw in ["radio", "전원", "power"]):
            target_log_types.append("Radio_Power_Event")
        if any(kw in user_query_lower for kw in ["oos", "이탈", "망", "음영", "서비스"]):
            target_log_types.append("OOS_Event")
        if any(kw in user_query_lower for kw in ["crash", "fatal", "크래시", "죽었어"]):
            target_log_types.append("Crash_Event")
        if any(kw in user_query_lower for kw in ["anr", "응답없음", "멈춤"]):
            target_log_types.append("ANR_Context")
        if any(kw in user_query_lower for kw in ["dns", "네트워크", "차단", "앱", "인터넷", "지연"]):
            target_log_types.extend(["Network_DNS_Issue", "Network_Timeline_Stat"])

        if target_log_types:
            if len(target_log_types) == 1:
                conditions.append({"log_type": target_log_types[0]})
            else:
                # 💡 핵심: 여러 주제가 나오거나 전환될 때, $in 연산자를 통해 모두 DB에서 꺼내올 수 있게 열어둠
                conditions.append({"log_type": {"$in": target_log_types}})

        where_filter = None
        if len(conditions) == 1:
            where_filter = conditions[0]
        elif len(conditions) > 1:
            where_filter = {"$and": conditions}

        # 3. Vector DB 검색 실행
        results = self.collection.query(
            query_embeddings=[query_embedding],
            n_results=20,
            where=where_filter
        )

        # [디버깅 출력] 터미널에서 제대로 가져오는지 숫자를 꼭 확인하세요!
        found_count = len(results['documents'][0]) if results and results.get('documents') else 0
        print(f"\n[DEBUG] 현재 필터: {where_filter}")
        print(f"[DEBUG] DB가 가져온 문서 개수: {found_count} 개\n")

        # 4. 🚀 [핵심] LLM 토큰 폭발 방지 & 원본 스니펫 제한적 제공
        context_blocks = []
        if results and results['documents'] and len(results['documents'][0]) > 0:
            for doc, meta in zip(results['documents'][0], results['metadatas'][0]):

                clean_meta = {k: v for k, v in meta.items() if not k.startswith("raw_") and k != "source_file"}

                # 🚨 [개선] OOS는 raw_context에, Call은 raw_logs에 있으므로 둘 다 확인합니다!
                snippet = "(DB에 원본 로그가 없습니다)"
                raw_data = meta.get("raw_logs", meta.get("raw_context", "[]"))
                try:
                    raw_list = json.loads(raw_data)
                    if isinstance(raw_list, list) and len(raw_list) > 0:
                        # '중략됨' 같은 안내 문구를 제외하고 진짜 로그 5줄만 제공
                        real_logs = [l for l in raw_list if "중략됨" not in l and l.strip()]
                        if real_logs:
                            snippet = "\n".join(real_logs[-5:])
                except:
                    pass

                context_blocks.append(
                    f"[요약 본문]\n{doc}\n"
                    f"[핵심 메타정보]\n{clean_meta}\n"
                    f"[원인 지점 실제 로그 스니펫]\n{snippet}"
                )
        else:
            context_blocks.append("관련된 로그를 DB에서 찾지 못했습니다.")

        final_context = "\n\n---\n\n".join(context_blocks)

        # 5. 동적 프롬프트 조립 (Dynamic Prompt Routing)
        retrieved_log_types = set()
        if results and results.get('metadatas') and results['metadatas'][0]:
            for meta in results['metadatas'][0]:
                if meta and 'log_type' in meta:
                    retrieved_log_types.add(meta['log_type'])

        dynamic_prompt = self.prompts["base_persona"] + "\n[검색된 로그 기반 맞춤형 분석 가이드라인]\n"

        if retrieved_log_types:
            for l_type in retrieved_log_types:
                if l_type in self.prompts["log_guidelines"]:
                    dynamic_prompt += self.prompts["log_guidelines"][l_type] + "\n"
        else:
            dynamic_prompt += "- 현재 관련된 특정 로그 타입을 찾지 못했습니다. 주어진 문맥 내에서 최선을 다해 답변해라.\n"

        import requests
        url = "http://localhost:11434/api/chat"

        # 6. 이전 대화 내역(Chat History) 조립 최적화
        history_text = ""
        if chat_history:
            # 🚨 최신 문맥만 유지하기 위해 과거 3개가 아닌 '최근 2개'로 줄임
            recent_history = chat_history[-2:]
            history_lines = []
            for msg in recent_history:
                role = "User" if msg["role"] == "user" else "AI"
                history_lines.append(f"{role}: {msg['content']}")
            history_text = "\n".join(history_lines)

        # 7. 프롬프트에 '주제 전환' 강제 인식 규칙 추가
        dynamic_prompt += (
            "\n4. 🚨 [주제 전환 주의]: 과거 대화 내역은 이전 맥락을 파악하는 용도일 뿐이다. "
            "사용자가 새로운 주제(예: 통화 -> 배터리)를 물어보면 과거 대화에 얽매이지 말고, "
            "무조건 새롭게 제공된 [현재 분석 대상 로그]만을 바탕으로 독립적인 답변을 생성해라."
        )

        # 7. 최종 LLM 프롬프트 생성 (현재 로그 + 과거 대화 + 질문)
        system_prompt = dynamic_prompt
        prompt = (
            f"{system_prompt}\n\n"
            f"=== [참고용 과거 대화 내역] ===\n"
            f"{history_text if history_text else '없음'}\n\n"
            f"========================================\n\n"
            f"=== [새로 검색된 현재 분석 대상 로그] ===\n"
            f"{final_context}\n"
            f"========================================\n\n"
            f"🚨 [최종 지시사항]: 과거 대화 내용은 문맥 파악용일 뿐이다. "
            f"위의 [새로 검색된 로그]만을 바탕으로, 아래의 [사용자 질문]에 대해 새롭고 독립적인 답변을 작성해라.\n\n"
            f"사용자 질문: {user_query}"
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

    def get_all_files(self):
        """DB에 적재된 모든 유니크한 파일 목록을 반환합니다."""
        results = self.collection.get(include=["metadatas"])
        if not results or not results["metadatas"]:
            return []
        # 메타데이터에서 source_file 이름만 추출하여 중복 제거
        files = set(m["source_file"] for m in results["metadatas"] if m and "source_file" in m)
        return sorted(list(files))

    def reset_db(self):
        # """현재 컬렉션의 모든 데이터를 삭제합니다."""
        results = self.collection.get()
        if results and results["ids"]:
            self.collection.delete(ids=results["ids"])
            return True
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
