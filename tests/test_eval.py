"""평가(채점) 규칙 테스트: LLM 없이 규칙으로 정해지는 부분"""
import pandas as pd

from eval.rag_eval import judge_answer, recall_hit, summarize


def row(**kw):
    base = {"id": "X1", "type": "table", "chart_kind": "", "doc_id": "D01", "question": "q", "answer": "1", "pages": [9]}
    return pd.Series(base | kw)


def test_recall_hit_page_level():
    assert recall_hit(row(), [("D02", 9), ("D01", 9)]) is True
    assert recall_hit(row(), [("D02", 9), ("D01", 8)]) is False
    assert recall_hit(row(type="unknown"), [("D01", 9)]) is None


class NoLLM:
    def invoke(self, *_):
        raise AssertionError("이 경우에는 LLM을 부르면 안 됨")


def test_unknown_question_rules():
    assert judge_answer(row(type="unknown"), "모름", NoLLM())[0] == "correct"
    assert judge_answer(row(type="unknown"), "43.8%입니다", NoLLM())[0] == "hallucinated"


def test_refused_answer():
    assert judge_answer(row(), "모름", NoLLM())[0] == "refused"


def test_summarize_shape():
    results = pd.DataFrame([
        {"type": "text", "chart_kind": "", "recall_hit": True, "judge": "correct"},
        {"type": "table", "chart_kind": "", "recall_hit": False, "judge": "wrong"},
        {"type": "chart", "chart_kind": "image", "recall_hit": True, "judge": "correct"},
        {"type": "unknown", "chart_kind": "", "recall_hit": None, "judge": "correct"},
    ])
    s = summarize(results)
    assert s["텍스트"] == 1.0 and s["표"] == 0.0
    assert s["환각률"] == 0.0
