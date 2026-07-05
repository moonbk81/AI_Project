import json
import os
import re
import torch
import torch.nn.functional as F

class DynamicGoldenMatcher:
    def __init__(self, embed_model, json_path="./eval_golden_dataset.json"):
        self.embed_model = embed_model
        self.golden_data = []

        self._load_golden_dataset(json_path)

        if self.golden_data:
            print(f"🎯 [Golden Matcher] {len(self.golden_data)}개의 골든셋 쿼리 임베딩 중...")
            # 💡 에러 원인 수정 완료: 'query_key' 로 정확히 매칭합니다.
            queries = [item['query_key'] for item in self.golden_data]
            self.golden_embeddings = self.embed_model.encode(queries, convert_to_tensor=True)
            print("✅ [Golden Matcher] 초기화 완료!")

    def _extract_entities(self, text: str):
        """사용자 질문에서 시간, 패키지, 프로세스명 등 구체적 데이터를 추출합니다."""
        entities = {"time": None, "package": None, "net_id": None}

        # 1. 시간 추출
        time_pattern = r'(?:\d{1,2}[-월/]\d{1,2}일?\s*)?\d{1,2}시(?:\s*\d{1,2}분)?(?:\s*\d{1,2}초)?|\d{2}:\d{2}(?::\d{2})?'
        time_match = re.search(time_pattern, text)
        if time_match and time_match.group(0).strip():
            entities["time"] = time_match.group(0).strip()

        # 2. 패키지명/프로세스명 추출
        pkg_match = re.search(r'([a-zA-Z0-9_]+\.[a-zA-Z0-9_]+\.[a-zA-Z0-9_]+)', text)
        if pkg_match:
            entities["package"] = pkg_match.group(1).strip()

        # 3. NetID 추출
        netid_match = re.search(r'netId\s*=?\s*(\d+)', text, re.I)
        if netid_match:
            entities["net_id"] = netid_match.group(1).strip()

        return entities

    def _has_explicit_cs_scope(self, text: str) -> bool:
        text_lower = text.lower()
        return any(k in text_lower for k in [
            "cs call", "cs콜", "cs 콜", "cs 통화", "cscall"
        ])

    def _has_explicit_ps_scope(self, text: str) -> bool:
        text_lower = text.lower()
        return any(k in text_lower for k in [
            "ps call", "ps콜", "ps 콜", "ps 통화", "volte", "ims", "sip"
        ])

    def _is_scope_narrowing_match(self, user_query: str, template: str) -> bool:
        """골든셋 템플릿이 사용자의 일반 질의를 특정 통화 도메인으로 좁히는지 확인."""
        template_lower = template.lower()
        requires_cs = any(k in template_lower for k in ["cs call", "cs 통화"])
        requires_ps = any(k in template_lower for k in ["ps(", "ps call", "volte", "ims"])

        return (
            (requires_cs and not self._has_explicit_cs_scope(user_query))
            or (requires_ps and not self._has_explicit_ps_scope(user_query))
        )

    def _generalize_golden_query(self, golden_query):
        """골든셋 쿼리 내의 구체적 데이터를 변수({time}, {package} 등)로 템플릿화합니다."""
        gen_q = golden_query

        time_pattern = r'(?:\d{1,2}[-월/]\d{1,2}일?\s*)?\d{1,2}시(?:\s*\d{1,2}분)?(?:\s*\d{1,2}초)?|\d{2}:\d{2}(?::\d{2})?'
        gen_q = re.sub(time_pattern, '{time}', gen_q)

        pkg_pattern = r'([a-zA-Z0-9_]+\.[a-zA-Z0-9_]+\.[a-zA-Z0-9_]+)'
        gen_q = re.sub(pkg_pattern, '{package}', gen_q)

        netid_pattern = r'(?<=netId=)\d+'
        gen_q = re.sub(netid_pattern, '{net_id}', gen_q)

        return gen_q

    def _load_golden_dataset(self, json_path):
        if not os.path.exists(json_path):
            print(f"⚠️ [Golden Matcher] {json_path} 파일을 찾을 수 없습니다.")
            return

        try:
            with open(json_path, 'r', encoding='utf-8') as f:
                dataset = json.load(f)
        except json.JSONDecodeError as e:
            print(f"❌ [Golden Matcher] JSON 파일 문법 오류: {e}")
            return

        for tc in dataset:
            original_query = tc.get("query", "")
            if not original_query:
                continue

            # 질문 일반화 (템플릿 생성)
            generalized_template = self._generalize_golden_query(original_query)

            # 사용자 변형 쿼리와 원본 쿼리 병합
            variations = tc.get("user_query_variations", [])
            all_queries = list(variations)
            all_queries.append(original_query)

            for var_query in set(all_queries):
                if var_query.strip():
                    self.golden_data.append({
                        "query_key": var_query.strip(),
                        "query_template": generalized_template,
                        "tc_id": tc.get("test_id", "Unknown")
                    })

    def align_query(self, user_query: str, threshold: float = 0.75) -> str:
        """유사도 매칭 후 변수를 치환하여 최종 프롬프트를 만듭니다."""
        if not self.golden_data:
            return user_query

        # 1. 유사도 계산
        query_emb = self.embed_model.encode(user_query, convert_to_tensor=True)
        cos_scores = F.cosine_similarity(query_emb.unsqueeze(0), self.golden_embeddings)
        best_score, best_idx = torch.max(cos_scores, dim=0)

        score_val = best_score.item()

        # 2. 임계치 통과 시 동적 치환
        if score_val >= threshold:
            matched = self.golden_data[best_idx]
            template = matched["query_template"]

            if self._is_scope_narrowing_match(user_query, template):
                print(f"\n🎯 [Golden Match 후보 제외] 유사도: {score_val:.2f} (기반 TC: {matched['tc_id']})")
                print(f"   -> 원본 질문: {user_query}")
                print(f"   -> 제외 사유: 사용자 질문에 없는 통화 도메인(CS/PS)을 확장 지시가 강제함\n")
                return user_query

            # 사용자 질문에서 추출
            user_entities = self._extract_entities(user_query)

            if user_entities["time"]:
                template = template.replace('{time}', user_entities["time"])
            else:
                template = re.sub(r'\{time\}\s*(무렵에|에|경)?\s*', '해당 시간대 ', template)

            if user_entities["package"]:
                template = template.replace('{package}', user_entities["package"])
            else:
                template = template.replace('{package}', '해당 앱')

            if user_entities["net_id"]:
                template = template.replace('{net_id}', user_entities["net_id"])
            else:
                template = re.sub(r'netId=\{net_id\}\(?[a-zA-Z]*\)?\s*에서', '해당 네트워크에서 ', template)

            print(f"\n🎯 [Golden Match] 유사도: {score_val:.2f} (기반 TC: {matched['tc_id']})")
            print(f"   -> 원본 질문: {user_query}")
            print(f"   -> 확장 지시: {template}\n")

            return f"사용자 원본 질문: {user_query}\n[시스템 요구사항(상세): {template}]"

        return user_query
