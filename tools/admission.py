"""
입시 관련 도구
- 성적 저장/조회
- PDF 파싱 및 입시 데이터 추출
- RAG용 문서 인덱싱
"""
import json
from pathlib import Path
from datetime import datetime

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
STUDENT_INFO = DATA_DIR / "student_info.json"
UNIV_CACHE = DATA_DIR / "univ_cache"


def _ensure_data_dir():
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def save_grade(user_id: str, subject: str, grade: str | float) -> None:
    """사용자별 성적 저장 (가족 멤버 구분용)"""
    _ensure_data_dir()
    data = {}
    if STUDENT_INFO.exists():
        with open(STUDENT_INFO) as f:
            data = json.load(f)
    uid = str(user_id)
    if uid not in data:
        data[uid] = {}
    if "grades" not in data[uid]:
        data[uid]["grades"] = {}
    data[uid]["grades"][subject] = grade
    data[uid]["updated_at"] = datetime.now().isoformat()
    with open(STUDENT_INFO, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def get_grades(user_id: str) -> dict:
    """사용자 성적 조회"""
    if not STUDENT_INFO.exists():
        return {}
    with open(STUDENT_INFO) as f:
        data = json.load(f)
    return data.get(str(user_id), {}).get("grades", {})


def get_all_student_summary() -> str:
    """전체 성적 요약 (개인정보 최소화)"""
    if not STUDENT_INFO.exists():
        return "저장된 성적이 없습니다."
    with open(STUDENT_INFO) as f:
        data = json.load(f)
    lines = []
    for uid, info in data.items():
        grades = info.get("grades", {})
        if grades:
            lines.append(f"- 사용자 {uid[:4]}***: {list(grades.keys())}")
    return "\n".join(lines) if lines else "저장된 성적이 없습니다."


def extract_text_from_pdf(filepath: Path) -> str:
    """PDF에서 텍스트 추출 (RAG/파싱용)"""
    try:
        import pypdf
        reader = pypdf.PdfReader(filepath)
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    except ImportError:
        try:
            import pdfplumber
            with pdfplumber.open(filepath) as pdf:
                return "\n".join(p.extract_text() or "" for p in pdf.pages)
        except ImportError:
            return "[PDF 파싱을 위해 pypdf 또는 pdfplumber를 설치하세요: pip install pypdf]"


def list_cached_docs() -> list[dict]:
    """캐시된 입시 문서 목록"""
    manifest = UNIV_CACHE / "_manifest.json"
    if not manifest.exists():
        return []
    with open(manifest) as f:
        return json.load(f)
