"""리랭커 (v3): 질문과 후보 청크를 한 쌍씩 모델에 넣어 관련도를 다시 채점

벡터 검색은 질문과 청크를 따로따로 숫자로 바꾼 뒤 비교하지만, 리랭커는 둘을 함께 읽고 판단하므로 더 정확합니다.
대신 느리기 때문에 1차 검색으로 추린 후보 20개에만 씁니다.
모델: BAAI/bge-reranker-v2-m3 (공개 모델, 내 컴퓨터에서 실행, 처음 실행할 때 약 2GB 내려받음)
"""
import logging

log = logging.getLogger(__name__)


class Reranker:
    def __init__(self, model_name: str, max_length: int = 512):
        self.model_name = model_name
        self.max_length = max_length
        self._model = None

    def _load(self):
        if self._model is None:
            from sentence_transformers import CrossEncoder   # 무거운 라이브러리라 필요할 때만 불러옴
            log.info("리랭커 모델 불러오는 중: %s", self.model_name)
            self._model = CrossEncoder(self.model_name, max_length=self.max_length, device="cpu")
        return self._model

    def score(self, query: str, texts: list[str]) -> list[float]:
        """관련도 점수 (0~1, 클수록 관련 있음)"""
        if not texts:
            return []
        scores = self._load().predict([(query, t) for t in texts], batch_size=8, show_progress_bar=False)
        return [float(s) for s in scores]
