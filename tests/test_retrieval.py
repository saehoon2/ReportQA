"""검색 함수 테스트 (OpenAI·DB·리랭커 모델 없이)"""
from app.generation.answer import format_context, is_unknown
from app.retrieval.bm25 import BM25Index, tokenize
from app.retrieval.hybrid import HybridRetriever, rrf

CHUNKS = [
    {"id": 10, "document_id": 1, "doc_code": "D01", "page": 9, "content": "청년층 고용률은 43.8%로 하락"},
    {"id": 11, "document_id": 2, "doc_code": "D02", "page": 45, "content": "음악 산업 매출액 2025년 4분기 4,056,713"},
    {"id": 12, "document_id": 2, "doc_code": "D02", "page": 46, "content": "게임 산업 수출액 증가"},
    {"id": 13, "document_id": 3, "doc_code": "D03", "page": 16, "content": "건강보험 부담 2015년 64.5%"},
]


def test_rrf_prefers_items_found_by_both():
    scores = rrf([["a", "b", "c"], ["c", "d"]])
    assert max(scores, key=scores.get) == "c"        # 두 방식 모두에서 찾은 것이 1등
    assert scores["a"] > scores["d"]                 # 같은 1~2등이라도 순위가 높을수록 점수가 큼


def test_tokenize_removes_particles():
    tokens = tokenize("청년층의 고용률은?")
    assert "청년" in tokens and "고용" in tokens     # 형태소 분석기는 '청년'+'층', '고용'+'률'로 자름
    assert "의" not in tokens and "은" not in tokens and "?" not in tokens


def test_bm25_finds_keyword_match():
    index = BM25Index([c["content"] for c in CHUNKS])
    top = index.search("음악 매출액", k=2)
    assert top[0][0] == 1


def test_bm25_respects_allowed():
    index = BM25Index([c["content"] for c in CHUNKS])
    assert index.search("게임 수출액", k=5)[0][0] == 2
    assert index.search("게임 수출액", k=5, allowed={0, 1}) == []    # 허용한 청크 밖은 돌려주지 않음


def test_hybrid_retriever_without_reranker():
    def fake_vector(query, k, document_ids):
        return [13, 10, 11, 12][:k]                  # 벡터 검색은 엉뚱한 순서라고 가정

    r = HybridRetriever(CHUNKS, fake_vector, reranker=None)
    hits = r.retrieve("음악 산업 매출액", top_k=2, candidates=4)
    assert r.chunks[hits[0].index]["id"] == 11       # BM25 1등 + 벡터 3등 → 합치면 1등
    assert hits[0].bm25_rank == 1 and hits[0].vector_rank == 3


def test_hybrid_retriever_with_fake_reranker():
    class FakeReranker:
        def score(self, query, texts):
            return [1.0 if "건강보험" in t else 0.0 for t in texts]

    r = HybridRetriever(CHUNKS, lambda q, k, d: [10, 11, 12, 13][:k], FakeReranker())
    hits = r.retrieve("2015년 건강보험 부담", top_k=1, candidates=4)
    assert r.chunks[hits[0].index]["id"] == 13
    assert hits[0].rerank_score == 1.0


def test_document_filter():
    r = HybridRetriever(CHUNKS, lambda q, k, d: [c["id"] for c in CHUNKS if c["document_id"] in d][:k], None)
    hits = r.retrieve("산업", top_k=5, candidates=4, document_ids=[2])
    assert {r.chunks[h.index]["document_id"] for h in hits} == {2}


def test_is_unknown():
    assert is_unknown("모름")
    assert is_unknown("모름. [D01 p.3]")
    assert not is_unknown("모름이라는 단어가 포함된 다른 답 43.8%")


def test_format_context():
    text = format_context(CHUNKS[:1])
    assert text.startswith("[D01 p.9]\n청년층")
