"""하이브리드 검색 + 리랭킹 (v3, 구조도 3의 ②③)

  ② 1차 검색: 벡터 검색(뜻이 비슷한 청크)과 BM25(단어가 일치하는 청크)를 각각 하고,
     RRF(순위 합치기)로 합쳐 후보 20개를 만듦
  ③ 리랭킹: 후보 20개를 리랭커로 다시 채점해 상위 5개를 고름

벡터 검색 방법은 함수로 받습니다. 서버는 pgvector, 평가 노트북은 FAISS를 넘겨 같은 코드를 씁니다.
"""
from dataclasses import dataclass, field

from app.retrieval.bm25 import BM25Index


def rrf(rank_lists, k: int = 60) -> dict:
    """Reciprocal Rank Fusion: 여러 순위 목록을 합침. 각 목록에서 r등이면 1/(k+r)점을 더함
    점수 크기가 서로 다른 검색 방식(코사인 거리 vs BM25 점수)을 순위만으로 공평하게 합칠 수 있음"""
    scores = {}
    for ranks in rank_lists:
        for r, key in enumerate(ranks, start=1):
            scores[key] = scores.get(key, 0.0) + 1.0 / (k + r)
    return scores


@dataclass
class Hit:
    index: int                      # chunks 목록 안의 위치
    vector_rank: int | None = None
    bm25_rank: int | None = None
    rrf_score: float = 0.0
    rerank_score: float | None = None
    extra: dict = field(default_factory=dict)


class HybridRetriever:
    def __init__(self, chunks: list[dict], vector_search, reranker=None):
        """chunks: [{'id':…, 'content':…, 'document_id':…, …}]
        vector_search(query, k, allowed_ids) → 청크 id 목록 (가까운 순)"""
        self.chunks = chunks
        self.pos = {c["id"]: i for i, c in enumerate(chunks)}
        self.bm25 = BM25Index([c["content"] for c in chunks])
        self.vector_search = vector_search
        self.reranker = reranker

    def retrieve(self, query: str, top_k: int = 5, candidates: int = 20, document_ids=None,
                 use_bm25: bool = True, use_reranker: bool = True) -> list[Hit]:
        allowed = None
        if document_ids:
            allowed = {i for i, c in enumerate(self.chunks) if c["document_id"] in set(document_ids)}
        vec_ids = self.vector_search(query, candidates, document_ids)
        vec_pos = [self.pos[i] for i in vec_ids if i in self.pos]
        bm_pos = [i for i, _ in self.bm25.search(query, candidates, allowed)] if use_bm25 else []

        fused = rrf([vec_pos, bm_pos])
        order = sorted(fused, key=fused.get, reverse=True)[:candidates]
        hits = [Hit(index=i, rrf_score=fused[i],
                    vector_rank=vec_pos.index(i) + 1 if i in vec_pos else None,
                    bm25_rank=bm_pos.index(i) + 1 if i in bm_pos else None) for i in order]

        if use_reranker and self.reranker is not None and hits:
            scores = self.reranker.score(query, [self.chunks[h.index]["content"] for h in hits])
            for h, s in zip(hits, scores):
                h.rerank_score = s
            hits.sort(key=lambda h: h.rerank_score, reverse=True)
        return hits[:top_k]
