"""
RAG (Retrieval-Augmented Generation) 모듈
입시 문서 기반 질의응답. FAISS + Ollama embeddings 사용.
(ChromaDB 대신 FAISS 사용 - DuckDB 호환성 이슈 회피)
"""
from pathlib import Path

from ollama_settings import get_ollama_base_url

DATA_DIR = Path(__file__).resolve().parent / "data"
UNIV_CACHE = DATA_DIR / "univ_cache"
VECTOR_DIR = DATA_DIR / "vectorstore"
FAISS_INDEX = VECTOR_DIR / "faiss_index"


def _ensure_dirs():
    VECTOR_DIR.mkdir(parents=True, exist_ok=True)


def build_index(force: bool = False) -> bool:
    """
    univ_cache 내 PDF들을 파싱해 벡터 인덱스 구축.
    force=True면 기존 인덱스 무시하고 재구축.
    """
    _ensure_dirs()
    import sys
    root = Path(__file__).resolve().parent
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from tools.admission import list_cached_docs, extract_text_from_pdf

    docs = list_cached_docs()
    if not docs:
        return False

    try:
        from langchain_community.embeddings import OllamaEmbeddings
        from langchain_community.vectorstores import FAISS
        from langchain_text_splitters import RecursiveCharacterTextSplitter
    except ImportError:
        return False

    texts = []
    metadatas = []
    for d in docs:
        fp = Path(d.get("filepath", ""))
        if not fp.exists():
            continue
        raw = extract_text_from_pdf(fp)
        if not raw or raw.startswith("["):
            continue
        splitter = RecursiveCharacterTextSplitter(chunk_size=800, chunk_overlap=100)
        chunks = splitter.split_text(raw)
        for c in chunks:
            texts.append(c)
            metadatas.append({"source": d.get("title", ""), "url": d.get("url", "")})

    if not texts:
        return False

    embeddings = OllamaEmbeddings(
        model="nomic-embed-text",
        base_url=get_ollama_base_url(),
    )
    if force and FAISS_INDEX.exists():
        import shutil
        shutil.rmtree(FAISS_INDEX)
    FAISS_INDEX.mkdir(parents=True, exist_ok=True)

    vs = FAISS.from_texts(texts, embeddings, metadatas=metadatas)
    vs.save_local(str(FAISS_INDEX))
    return True


def query(question: str, k: int = 4) -> tuple[str, list[dict]]:
    """
    입시 문서에서 관련 내용 검색 후 컨텍스트 반환.
    Returns: (검색된 컨텍스트 문자열, 소스 메타데이터 리스트)
    """
    _ensure_dirs()
    index_file = FAISS_INDEX / "index.faiss"
    if not index_file.exists():
        return "", []

    try:
        from langchain_community.embeddings import OllamaEmbeddings
        from langchain_community.vectorstores import FAISS
    except ImportError:
        return "", []

    embeddings = OllamaEmbeddings(
        model="nomic-embed-text",
        base_url=get_ollama_base_url(),
    )
    vs = FAISS.load_local(str(FAISS_INDEX), embeddings, allow_dangerous_deserialization=True)
    results = vs.similarity_search_with_score(question, k=k)
    if not results:
        return "", []

    context_parts = []
    sources = []
    for doc, score in results:
        context_parts.append(doc.page_content)
        sources.append({"title": doc.metadata.get("source", ""), "url": doc.metadata.get("url", "")})
    return "\n\n---\n\n".join(context_parts), sources
