"""
AI 에이전트 엔진
- Ollama gemma3:27b 사용
- 도구 호출 (번역, 인스타, 입시, RAG)
- 스킬(역할) 로드
"""
import re
from pathlib import Path
from typing import Callable

from langchain_community.llms import Ollama
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser

from ollama_settings import get_ollama_base_url

# 도구 임포트
from tools import browser, translator, admission

ROOT = Path(__file__).resolve().parent
SKILLS_DIR = ROOT / "skills"


def load_skill(name: str) -> str:
    """스킬 마크다운 파일 로드"""
    path = SKILLS_DIR / f"{name}.md"
    if path.exists():
        return path.read_text(encoding="utf-8")
    return ""


def load_all_skills() -> str:
    """모든 스킬 내용 합침"""
    parts = []
    for f in SKILLS_DIR.glob("*.md"):
        parts.append(f.read_text(encoding="utf-8"))
    return "\n\n".join(parts) if parts else ""


class AIAgent:
    def __init__(self, model: str = "gemma3:27b"):
        self.model = model
        self.llm = Ollama(model=model, base_url=get_ollama_base_url())
        self.skills = load_all_skills()

    def _invoke(self, prompt: str) -> str:
        return self.llm.invoke(prompt)

    # --- 번역 ---
    def translate_to_korean(self, text: str) -> str:
        return translator.translate_to_korean(text, self.model)

    def translate_to_chinese(self, text: str) -> str:
        return translator.translate_to_chinese(text, self.model)

    def translate_auto(self, text: str) -> str:
        return translator.translate_auto(text, self.model)

    # --- Instagram ---
    async def get_instagram_followers(self, user_id: str) -> dict:
        data = await browser.get_instagram_followers(user_id)
        if not data.get("error") and data.get("followers") is not None:
            browser.save_instagram_history(user_id, data)
        return data

    def get_instagram_trend(self, user_id: str) -> dict:
        return browser.get_instagram_trend(user_id)

    # --- 입시 ---
    def save_grade(self, user_id: str, subject: str, grade: str | float) -> str:
        admission.save_grade(user_id, subject, grade)
        return f"'{subject}' 성적이 저장되었습니다."

    def get_grades(self, user_id: str) -> dict:
        return admission.get_grades(user_id)

    def list_admission_docs(self) -> list:
        return admission.list_cached_docs()

    # --- RAG ---
    def query_admission(self, question: str) -> str:
        """입시 관련 일반 질문 (RAG 기반)"""
        try:
            from rag import query as rag_query
            context, sources = rag_query(question)
            if not context:
                return "입시 문서가 아직 인덱싱되지 않았거나, 관련 내용을 찾지 못했습니다. 먼저 대학 PDF를 다운로드하고 /index로 인덱스를 구축해주세요."
            prompt = f"""다음은 대학 입시 관련 문서에서 검색한 내용입니다. 이 내용을 바탕으로 질문에 답해주세요.
답변 시 출처(문서명)를 함께 알려주세요.

[검색된 문서 내용]
{context}

[질문]
{question}

[답변]"""
            return self._invoke(prompt)
        except Exception as e:
            return f"RAG 조회 중 오류: {e}"

    def summarize_admission_guide(self, univ_name: str) -> str:
        """학교별 입시요강 검색 후 요약 (별도 동작)"""
        try:
            from pathlib import Path
            from tools.admission import list_cached_docs
            DATA_DIR = Path(__file__).resolve().parent / "data"
            VECTOR_DIR = DATA_DIR / "vectorstore"
            FAISS_INDEX = VECTOR_DIR / "faiss_index"

            # 상태 확인 후 구체적 안내
            docs = list_cached_docs()
            has_index = (FAISS_INDEX / "index.faiss").exists()
            if not docs:
                return f"""'{univ_name}' 입시요강을 찾을 수 없습니다.

📌 1단계: /index 를 입력하세요.
   → PDF가 없으면 config의 pdf_urls에서 자동 다운로드 후 인덱스 구축됩니다.

📌 2단계: 완료 후 /guide {univ_name} 를 다시 시도하세요."""
            if not has_index:
                return f"""PDF는 {len(docs)}개 있지만 인덱스가 없습니다.

📌 /index 를 입력해 인덱스를 구축한 뒤, /guide {univ_name} 를 다시 시도하세요."""

            from rag import query as rag_query
            question = f"{univ_name} 2027학년도 입시요강 수시 정시 전형 요약"
            context, sources = rag_query(question, k=6)
            if not context:
                return f"""인덱스에 '{univ_name}' 관련 내용이 없습니다.

📌 /index 를 다시 실행해 인덱스를 재구축한 뒤 시도하세요."""
            prompt = f"""다음은 {univ_name} 입시 관련 문서에서 검색한 내용입니다.
이 내용을 바탕으로 **2027학년도 입시요강 요약**을 해주세요.

요약 형식:
- 수시 전형 개요 (전형별 비율, 전형요소 등)
- 정시 전형 개요 (반영과목, 배점 등)
- 주요 일정 (가능한 경우)
- 핵심 포인트 3~5개

[검색된 문서 내용]
{context}

[요약]"""
            return self._invoke(prompt)
        except Exception as e:
            return f"입시요강 요약 중 오류: {e}"

    def analyze_grade_possibility(self, user_id: int) -> str:
        """저장된 성적 기반 합격 가능성 분석 (별도 동작)"""
        grades = self.get_grades(user_id)
        if not grades:
            return "저장된 성적이 없습니다. 먼저 /grade [과목] [점수] 형식으로 성적을 저장해주세요.\n예: /grade 국어 1등급"
        try:
            from rag import query as rag_query
            grade_str = ", ".join(f"{k}: {v}" for k, v in grades.items())
            context, _ = rag_query("수시 정시 전형 반영비율 최저학력기준", k=4)
            prompt = f"""다음은 사용자가 입력한 성적입니다.
{grade_str}

{f'[참고: 저장된 입시 문서 내용]\n{context}' if context else ''}

위 성적을 바탕으로 **대학 합격 가능성**을 간단히 분석해주세요.
- 수시/정시 각각 어떤 전형이 유리한지
- 주의할 점 또는 보완이 필요한 부분
- 구체적 대학명은 언급하지 말고, 전반적인 조언 위주로"""
            return self._invoke(prompt)
        except Exception as e:
            return f"가능성 분석 중 오류: {e}"

    # --- 의도 분류 및 라우팅 ---
    async def route_and_respond(self, user_id: int, message: str) -> str:
        """
        사용자 메시지를 분석해 적절한 도구/스킬로 라우팅 후 응답.
        """
        msg = message.strip().lower()
        # 명령 패턴
        if msg.startswith("/번역 ") or msg.startswith("번역 ") or msg.startswith("/translate ") or msg.startswith("translate "):
            text = message.split(maxsplit=1)[1] if len(message.split(maxsplit=1)) > 1 else ""
            return self.translate_auto(text) if text else "번역할 텍스트를 입력해주세요."

        if msg.startswith("/인스타 ") or msg.startswith("인스타 ") or msg.startswith("/insta ") or msg.startswith("insta "):
            parts = message.split(maxsplit=1)
            if len(parts) < 2:
                return "사용법: 인스타 [instagram_user_id]"
            uid = parts[1].strip().split()[0] if parts[1] else ""
            data = await self.get_instagram_followers(uid)
            if data.get("error"):
                return f"오류: {data['error']}"
            followers = data.get("followers")
            return f"@{uid} 팔로워: {followers:,}" if followers is not None else f"@{uid} 팔로워: 조회 실패"

        # 입시요강 검색/요약: /guide 서울대, "서울대 요강 요약" 등
        if msg.startswith("guide ") or msg.startswith("/guide ") or ("요강" in msg and ("요약" in msg or "검색" in msg)):
            parts = message.replace("guide", "").replace("/guide", "").replace("요강", "").replace("요약", "").replace("검색", "").strip().split()
            univ = parts[0] if parts else ""
            if univ:
                return self.summarize_admission_guide(univ)
            return "사용법: /guide [대학명] 예: /guide 서울대"

        # 성적 저장 vs 가능성 분석 (별개 동작)
        if "성적" in msg or "가능성" in msg or msg.startswith("grade "):
            parts = message.replace("저장", "").replace("가능성", "").strip().split()
            if "성적" in parts:
                parts = [p for p in parts if p != "성적"]
            if "grade" in parts:
                parts = [p for p in parts if p != "grade"]
            # 인자 2개 이상: 성적 저장
            if len(parts) >= 2:
                subj, grade = parts[0], " ".join(parts[1:])
                self.save_grade(user_id, subj, grade)
                return f"'{subj}' {grade} 저장했습니다."
            # 인자 없음: 가능성 분석
            return self.analyze_grade_possibility(user_id)

        # 입시 문서 기반 Q&A (/ask 또는 입시 관련 키워드)
        admission_keywords = ["입시", "수시", "정시", "대학", "요강", "전형", "모집", "수능", "최저", "교과", "논술", "합격"]
        if msg.startswith("ask ") or msg.startswith("/ask ") or any(k in msg for k in admission_keywords):
            q = message.replace("ask", "").replace("/ask", "").strip() if ("ask" in msg[:5] or "/ask" in msg[:5]) else message
            if q:
                return self.query_admission(q)

        # 기본: 일반 대화 + 스킬 컨텍스트
        system = f"""당신은 가족용 AI 비서입니다. 다음 스킬을 참고하세요.

{self.skills}

사용 가능한 명령:
- "번역 [텍스트]" 또는 "/번역 [텍스트]": 중국어↔한국어 자동 번역
- "인스타 [user_id]" 또는 "/인스타 [user_id]": Instagram 팔로워 조회
- 입시/수시/정시/대학/요강 관련 질문: 저장된 입시 문서 기반 답변
- "성적 [과목] [점수]": 성적 저장

친절하고 도움이 되는 답변을 해주세요."""
        prompt = f"{system}\n\n[사용자] {message}\n\n[비서]"
        return self._invoke(prompt)
