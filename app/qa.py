"""질의응답 전체 흐름 (구조도 3): 질문 → 하이브리드 검색 → 리랭킹 → 관련도 확인 → 답변

서버(app/main.py)는 이 파일의 ask()만 부릅니다.
"""
import threading
import time

from langchain_openai import ChatOpenAI, OpenAIEmbeddings

from app.config import settings
from app.db import repo
from app.generation.answer import generate_answer, is_unknown
from app.retrieval.hybrid import HybridRetriever
from app.retrieval.rerank import Reranker

NOT_FOUND = "선택한 문서에서 이 질문에 대한 근거를 찾을 수 없습니다."

_lock = threading.Lock()
_state = {"retriever": None, "version": -1}
_version = {"n": 0}
_reranker = Reranker(settings.reranker_model) if settings.use_reranker else None
_embeddings = None
_llm = None


def invalidate():
    """문서가 추가·삭제되면 호출 → 다음 질문 때 BM25 색인을 새로 만듦"""
    _version["n"] += 1


def _vector_search(query, k, document_ids):
    global _embeddings
    if _embeddings is None:
        _embeddings = OpenAIEmbeddings(model=settings.embedding_model)
    rows = repo.search_vector(_embeddings.embed_query(query), k, document_ids)
    return [r["id"] for r in rows]


def get_retriever() -> HybridRetriever:
    with _lock:
        if _state["retriever"] is None or _state["version"] != _version["n"]:
            _state["retriever"] = HybridRetriever(repo.all_chunks(), _vector_search, _reranker)
            _state["version"] = _version["n"]
        return _state["retriever"]


def ask(question: str, document_ids=None, top_k: int | None = None) -> dict:
    global _llm
    start = time.time()
    top_k = top_k or settings.rerank_top_k
    retriever = get_retriever()
    hits = retriever.retrieve(question, top_k=top_k, candidates=settings.retrieve_top_k,
                              document_ids=document_ids, use_reranker=_reranker is not None)
    sources = []
    for rank, h in enumerate(hits, start=1):
        c = retriever.chunks[h.index]
        sources.append({
            "rank": rank, "chunk_id": c["id"], "document_id": c["document_id"], "doc_code": c["doc_code"],
            "doc_title": c["doc_title"], "page": c["page"], "kind": c["kind"], "content": c["content"],
            "asset_id": c["asset_id"], "vector_rank": h.vector_rank, "bm25_rank": h.bm25_rank,
            "rerank_score": h.rerank_score,
        })

    # ④ 관련도 확인: 리랭커 1등 점수가 기준보다 낮으면 LLM을 부르지 않고 "찾을 수 없음"
    top_score = sources[0]["rerank_score"] if sources else None
    if not sources or (top_score is not None and top_score < settings.relevance_threshold):
        answer, unknown = NOT_FOUND, True
    else:
        if _llm is None:
            _llm = ChatOpenAI(model=settings.llm_model)
        answer = generate_answer(question, sources, _llm)
        unknown = is_unknown(answer)
        if unknown:
            answer = NOT_FOUND
    latency_ms = int((time.time() - start) * 1000)
    try:
        repo.log_qa(question, answer, [s["chunk_id"] for s in sources], unknown, latency_ms)
    except Exception:
        pass
    return {"answer": answer, "is_unknown": unknown, "sources": sources, "latency_ms": latency_ms}
