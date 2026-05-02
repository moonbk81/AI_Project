import os
import json
import glob
import chromadb
import torch
from sentence_transformers import SentenceTransformer
from agent_tools import (
    get_cs_call_analytics,
    get_ps_ims_call_analytics,
    get_network_oos_analytics,
    get_dns_latency_analytics,
    get_battery_thermal_analytics,
    get_crash_anr_analytics,
    get_radio_power_analytics
)

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

        # 4. 동적 프롬프트 관리를 위한 템플릿 딕셔너리
        self.prompts = {
            "base_persona": (
                "너는 안드로이드 무선 통신(RIL/Telephony) 및 Network Stack을 분석하는 최고 수준의 수석 엔지니어다.\n"
                "🚨 [데이터 분석 및 과거 대화 참조 규칙]\n"
                "1. 원칙적으로 새롭게 검색된 [현재 분석 대상 로그]를 최우선으로 분석해라.\n"
                "2. 단, 사용자가 '방금 찾은 데이터', '앞서 말한' 등 이전 대화의 후속 분석(이상징후 탐지 등)을 요구하는 경우, [참고용 과거 대화 내역]에 남아있는 이전 수치 데이터를 적극적으로 재분석하여 대답해도 좋다.\n"
                "3. '궁금한 점이 있으신가요?' 같은 역질문은 절대 금지한다."),
            "log_guidelines": {
                "Call_Session": "- [Call_Session (통화)]: status가 'FAIL/DROP'인 세션을 찾고, 'fail_reason'을 반드시 읽어서 실패 원인을 설명해라. (통화 에러 분석 시 OOS와 혼동 금지)",
                "OOS_Event": "- [OOS_Event (망 이탈)]: voice_reg/data_reg 값이 0이면 '정상', 1 이상이면 '망 이탈/음영'으로 판단해라.",
                "Battery_Drain_Report": "- [Battery_Drain_Report (배터리)]: stats_period와 신호 세기 분포(none/poor 비중)를 바탕으로 배터리 광탈 원인을 진단해라.",
                "Network_Timeline_Stat": (
                    "- [Network_Timeline_Stat (긴급)]: 절대 '시간 기준/방식 기준' 같은 이론적인 설명을 하지 마라.\n"
                    "- 로그에 적힌 netId별 dns_avg(ms), dns_err_rate(%), tcp_avg_loss(%) 수치를 직접 나열해라.\n"
                    "- 예: '14:20:05 시점에 netId=117의 DNS 평균 지연은 3005ms였으며, 손실률은 1.5%입니다.'와 같이 "
                    "시간대별로 구체적인 팩트만 보고해라."
                    "- 🚨 [이상징후 판단 기준]: 전체 데이터의 평균(보통 0~100ms)과 비교하여 혼자 수백~수천 ms로 비정상적으로 급증한(튀는) 지점이 있다면, 그것이 바로 이상징후다.\n"
                    "- '09:50:00에 DNS 지연이 3049ms로 비정상적으로 급증했습니다.' 처럼 팩트를 짚어내라."
                ),
                "Network_DNS_Issue": (
                    "- [Network_DNS_Issue (긴급)]: 앱이 차단된 원인을 '이론'이 아닌 '팩트'로 말해라.\n"
                    "- effective_policy가 BATTERY_SAVER라면 '절전 모드 때문'이라고 한 줄로 요약해라."
                ),
                "Signal_Level": (
                    "- [Signal_Level]: Slot별 안테나 수신 레벨(0~5)의 시간대별 변화를 분석해라.\n"
                    "- 수신 레벨이 0이나 1로 뚝 떨어지는 지점을 찾아내어 '음영/수신 저하 구간'으로 팩트만 보고해라.\n"
                    "- 절대 OOS 로그와 헷갈리지 마라."
                )
            }
        }
        # 1. prompts["log_guidelines"]에 SIP 분석 규칙 추가
        self.prompts["log_guidelines"]["IMS_SIP_Message"] = (
            "- [IMS_SIP_Message (VoLTE)]: SipReq(요청)와 SipResp(응답)의 흐름을 분석해라.\n"
            "- 🚨 [에러 판단]: 4xx(Client Error), 5xx(Server Error) 응답이 있다면 즉시 지적하고 원인을 추론해라.\n"
            "- INVITE 후 200 OK까지의 지연이 크다면 '호 설정 지연'으로 판단해라."
        )

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
        """Plan -> Act -> Retrieve -> Reason 파이프라인"""
        current_base = current_file.replace("_payload.json", "") if current_file else "Unknown"

        # [STAGE 1: Act] 의도 기반 도구 매핑 및 팩트 획득
        tool_facts = self._get_tool_facts(user_query, current_base)

        # [STAGE 2: Retrieve] 쿼리 증강 및 Vector DB 검색
        search_query = user_query
        if len(user_query) < 15 and chat_history:
            last_msg = next((msg['content'] for msg in reversed(chat_history) if msg['role'] == 'user'), "")
            search_query = f"{last_msg} 관련 후속 질문: {user_query}"

        query_embedding = self.embed_model.encode(search_query).tolist()
        results = self.collection.query(query_embeddings=[query_embedding], n_results=top_k)

        # [STAGE 3: Prompt Construction] 팩트와 검색 결과의 융합
        formatted_logs = self._format_results(results)

        system_role = (
            "당신은 15년 차 안드로이드 통신 프로토콜 수석 엔지니어입니다.\n"
            "제공된 [도구 분석 팩트]를 절대적인 진실(Ground Truth)로 삼아 [검색된 관련 로그]를 교차 검증하십시오.\n"
            "🚨 [엄격한 분석 규칙]:\n"
            "1. 팩트에 'NORMAL_RELEASE'나 에러 카운트 0이 명시되어 있다면, 로그 스니펫의 ERROR나 BYE 키워드에 속지 말고 정상 동작으로 판정하십시오.\n"
            "2. 팩트와 로그가 일치하는 지점(예: 팩트의 Cause Code와 로그의 메시지)을 찾아 인과관계를 설명하십시오.\n"
            "3. 답변은 요약, 주요 분석, 엔지니어 소견, 권장 사항 순으로 작성하십시오."
        )

        prompt = (
            f"{system_role}\n\n"
            f"=== [도구 분석 팩트] ===\n{tool_facts}\n\n"
            f"=== [검색된 관련 로그] ===\n{formatted_logs}\n\n"
            f"사용자 질문: {user_query}"
        )

        # [STAGE 4: Reason] LLM 추론 실행
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

    def _get_tool_facts(self, query: str, base_name: str) -> str:
        """질문을 분석하여 필요한 agent_tools를 실행하고 결과를 취합합니다."""
        if base_name == "Unknown":
            return "분석 대상 파일이 지정되지 않아 팩트 조회를 생략합니다."

        facts = []
        q = query.lower()

        # 1. 배터리 및 발열
        if any(kw in q for kw in ["battery", "배터리", "전력", "광탈", "발열", "온도", "thermal"]):
            try: facts.append(f"[Battery/Thermal Fact]: {get_battery_thermal_analytics(base_name)}")
            except Exception as e: print(f"Tool Error: {e}")

        # 2. 통화 (CS/PS 통합)
        if any(kw in q for kw in ["call", "콜", "통화", "전화", "끊김", "drop"]):
            try:
                facts.append(f"[CS Call Fact]: {get_cs_call_analytics(base_name)}")
                facts.append(f"[PS Call Fact]: {get_ps_ims_call_analytics(base_name)}")
            except Exception as e: print(f"Tool Error: {e}")

        # 3. 라디오 전원
        if any(kw in q for kw in ["radio", "전원", "power"]):
            try: facts.append(f"[Radio Power Fact]: {get_radio_power_analytics(base_name)}")
            except Exception as e: print(f"Tool Error: {e}")

        # 4. 망 이탈 및 안테나 신호
        if any(kw in q for kw in ["oos", "이탈", "망", "음영", "서비스", "안테나", "시그널", "신호", "signal", "level", "수신"]):
            try: facts.append(f"[Network/OOS Fact]: {get_network_oos_analytics(base_name)}")
            except Exception as e: print(f"Tool Error: {e}")

        # 5. 시스템 크래시 및 ANR
        if any(kw in q for kw in ["crash", "fatal", "크래시", "죽었어", "anr", "응답없음", "멈춤"]):
            try: facts.append(f"[Crash/ANR Fact]: {get_crash_anr_analytics(base_name)}")
            except Exception as e: print(f"Tool Error: {e}")

        # 6. 네트워크 지연 및 DNS (SIP 포함)
        if any(kw in q for kw in ["dns", "네트워크", "차단", "앱", "인터넷", "지연", "이상", "징후", "튀는", "sip", "ims", "volte"]):
            try:
                facts.append(f"[DNS/Network Fact]: {get_dns_latency_analytics(base_name)}")
                # SIP/IMS 관련 질문이면 통화 분석 도구도 방어적으로 호출
                if not any("PS Call Fact" in f for f in facts):
                    facts.append(f"[PS Call Fact]: {get_ps_ims_call_analytics(base_name)}")
            except Exception as e: print(f"Tool Error: {e}")

        return "\n".join(facts) if facts else "질문과 매칭되는 명시적 팩트 도구가 없습니다."

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
