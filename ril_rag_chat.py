import json
import os
import torch
import chromadb
import ollama
from sentence_transformers import SentenceTransformer

class RilRagChatbot:
    def __init__(self, db_path="./ril_vector_db"):
        print("⚙️ 시스템 초기화 중...")
        # 1. BGE-M3 (임베딩 모델) 로드 - GPU 가속 사용 (MPS > CUDA > CPU 순서)
        if torch.backends.mps.is_available():
            self.device = "mps"
        elif torch.cuda.is_available():
            self.device = "cuda"
        else:
            self.device = "cpu"

        # offline BGE-M3 Model            
        # offline_model_path = "/home/bongki81/project/AI_Sepcialist/bge-m3-offline"

        print(f"🚀 BGE-M3 모델 로딩... (가속 장치: {self.device})")
        # SentenceTransformer가 알아서 최적화하여 BGE-M3를 로드합니다.
        self.embed_model = SentenceTransformer('BAAI/bge-m3', device=self.device)
        # self.embed_model = SentenceTransformer(offline_model_path, device=self.device)

        # 2. ChromaDB 로드
        self.client = chromadb.PersistentClient(path=db_path)
        self.collection = self.client.get_or_create_collection(name="ril_knowledge")
        print("✅ 초기화 완료!\n")

    def ingest_folder(self, folder_path="./payloads"):
        """payloads 폴더 내의 새로운 JSON 파일만 선별하여 적재합니다."""
        import glob
        if not os.path.exists(folder_path):
            os.makedirs(folder_path, exist_ok=True)
            print(f"📂 '{folder_path}' 폴더가 생성되었습니다. 분석된 JSON 파일을 넣어주세요.")
            return

        # 1. payloads 폴더 내의 모든 json 파일 찾기
        json_files = glob.glob(os.path.join(folder_path, "*.json"))
        if not json_files:
            print(f"⚠️ '{folder_path}' 폴더에 적재할 데이터가 없습니다.")
            return

        # 2. DB에서 이미 처리된 파일 목록(source_file 메타데이터) 조회
        # 전체 데이터를 가져와서 이미 저장된 파일명 세트(Set)를 만듭니다.
        existing_data = self.collection.get(include=["metadatas"])
        processed_files = set()
        if existing_data and existing_data["metadatas"]:
            for meta in existing_data["metadatas"]:
                if "source_file" in meta:
                    processed_files.add(meta["source_file"])

        # 3. 신규 파일만 필터링
        new_files = [f for f in json_files if os.path.basename(f) not in processed_files]

        if not new_files:
            print("✨ 모든 파일이 이미 최신 상태입니다. (추가 적재 없음)")
            return

        print(f"📦 총 {len(new_files)}개의 새로운 로그 파일을 발견했습니다. 적재 시작...")

        for file_path in new_files:
            filename = os.path.basename(file_path)
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)

            # 파일명을 ID의 접두어로 사용하여 고유성 보장
            base_id = os.path.splitext(filename)[0]
            documents = [item["document"] for item in data]
            metadatas = [item["metadata"] for item in data]
            
            # 모든 메타데이터에 파일명 기록 (이게 있어야 나중에 중복 체크가 가능합니다)
            for m in metadatas:
                m['source_file'] = filename

            ids = [f"{base_id}_{i}" for i in range(len(data))]

            print(f"🔄 '{filename}' 임베딩 중... ({len(documents)}개 지식 추출)")
            embeddings = self.embed_model.encode(documents).tolist()

            # Batch 적재
            self.collection.add(
                embeddings=embeddings,
                documents=documents,
                metadatas=metadatas,
                ids=ids
            )

        print(f"\n✅ 지식 창고 업데이트 완료!")

    def ask(self, user_query):
        """질문을 받아 검색하고 Gemma-2B에게 물어봅니다."""
        print("\n🔍 관련 로그 검색 중...")
        
        # 1. 질문 임베딩 생성 
        # (반드시 문자열인 'user_query'가 들어가야 합니다!)
        query_embedding = self.embed_model.encode(user_query).tolist()

        # 2. ChromaDB 검색
        # (위에서 뽑아낸 숫자리스트를 DB에 던져서 유사한 로그 3개를 찾습니다)
        results = self.collection.query(
            query_embeddings=[query_embedding],
            n_results=3
        )
        
        if not results['documents'][0]:
            return "검색된 관련 로그가 없습니다."

        # 2. 검색된 Document(본문)를 컨텍스트로 구성
        retrieved_docs = "\n\n".join(results['documents'][0])
        retrieved_metas = results['metadatas'][0]

        # 3. Gemma-2B 프롬프트 구성
        system_prompt = (
            "너는 안드로이드 무선 통신(RIL, Telephony) 로그 분석 전문가야. "
            "아래에 제공된 '검색된 로그 요약'을 바탕으로 사용자의 질문에 답변해줘. "
            "원인과 해결책을 논리적이고 명확하게 설명해야 해."
        )
        
        user_prompt = f"[검색된 로그 요약]\n{retrieved_docs}\n\n[질문]\n{user_query}"

        print("🤖 Gemma-2B가 분석 중입니다...\n")
        # 4. Ollama를 통해 Gemma-2B 호출
        response = ollama.chat(model='gemma:2b', messages=[
            {'role': 'system', 'content': system_prompt},
            {'role': 'user', 'content': user_prompt}
        ])

        answer = response['message']['content']

        # 5. 결과 출력 (분석 + 증거 원본)
        print("="*60)
        print("💡 [분석 결과]")
        print(answer)
        print("\n" + "="*60)
        print("🔎 [참고 원본 로그 (엔지니어 확인용)]")
        for i, meta in enumerate(retrieved_metas):
            print(f"\n--- 참고 자료 {i+1} (시간: {meta.get('time', 'N/A')}, 슬롯: {meta.get('slot', 'N/A')}) ---")
            # 메타데이터에 숨겨둔 원본 로그(raw_logs) 출력
            raw_logs = json.loads(meta.get('raw_logs', '[]'))
            if raw_logs:
                for log in raw_logs[:5]: # 너무 길면 첫 5줄만 출력
                    print(f"  {log}")
                if len(raw_logs) > 5:
                    print("  ... (중략) ...")
            else:
                print("  (원본 로그 데이터 없음)")
        print("="*60 + "\n")

# --- 실행부 ---
if __name__ == "__main__":
    payload_path = "/Users/moonbk/Project/AI_Project/payloads" # 회사에서 가져온 파일 경로
    
    chatbot = RilRagChatbot()
    chatbot.ingest_folder(payload_path)
    
    print("💬 RIL RAG 챗봇이 준비되었습니다! (종료하려면 'quit' 또는 'exit' 입력)")
    while True:
        query = input("사용자: ")
        if query.lower() in ['quit', 'exit']:
            print("챗봇을 종료합니다.")
            break
        
        chatbot.ask(query)