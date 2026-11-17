"""BM25 키워드 검색 (v3)

BM25는 "질문의 단어가 청크에 얼마나 들어 있나"로 점수를 매기는 전통적인 검색입니다.
한국어는 '청년층의', '청년층은'처럼 조사가 붙어 단어가 달라지므로, 형태소 분석기(kiwipiepy)로
'청년층 / 의'처럼 잘라 의미 있는 조각(명사·숫자·영문 등)만 씁니다.
"""
import re

from rank_bm25 import BM25Okapi

# 남길 품사: 명사(NN*), 숫자(SN), 외국어(SL), 한자(SH), 어근(XR), 동사·형용사(VV, VA)
_KEEP = ("NN", "SN", "SL", "SH", "XR", "VV", "VA")
_kiwi = None


def _get_kiwi():
    global _kiwi
    if _kiwi is None:
        from kiwipiepy import Kiwi
        _kiwi = Kiwi()
    return _kiwi


def tokenize(text: str) -> list[str]:
    try:
        kiwi = _get_kiwi()
    except ImportError:   # 형태소 분석기가 없으면(가벼운 테스트 환경) 글자·숫자 덩어리로 자름
        return re.findall(r"[가-힣]+|[A-Za-z]+|\d+(?:[.,]\d+)*", text.lower())
    return [t.form.lower() for t in kiwi.tokenize(text) if t.tag.startswith(_KEEP)]


class BM25Index:
    def __init__(self, texts: list[str]):
        self.tokens = [tokenize(t) for t in texts]
        self.bm25 = BM25Okapi(self.tokens) if texts else None

    def search(self, query: str, k: int = 20, allowed=None) -> list[tuple[int, float]]:
        """(청크 위치 번호, 점수) 상위 k개. allowed: 이 위치 번호들 안에서만 찾기"""
        if self.bm25 is None:
            return []
        scores = self.bm25.get_scores(tokenize(query))
        order = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
        if allowed is not None:
            order = [i for i in order if i in allowed]
        return [(i, float(scores[i])) for i in order[:k] if scores[i] > 0]
