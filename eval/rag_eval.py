"""ReportQA 평가 도구 (docs/evaluation_guide.md의 채점 규칙을 코드로 옮긴 것)

v0, v1, v2 … 모든 버전이 같은 함수로 채점받도록 한 곳에 모아 둡니다.
"""
from pathlib import Path
from typing import Callable, Literal

import pandas as pd
from pydantic import BaseModel, Field

UNKNOWN = "모름"


# ---------- 테스트셋 ----------
def load_testset(path: str | Path) -> pd.DataFrame:
    """testset.csv를 읽고, answer_pages를 숫자 목록으로 바꿔 둠 (예: "1;17" → [1, 17])"""
    df = pd.read_csv(path, dtype=str).fillna("")
    df["pages"] = df["answer_pages"].apply(
        lambda s: [int(p) for p in s.split(";")] if s else []
    )
    return df


# ---------- 검색 점수: 쪽 단위 Recall@k ----------
def recall_hit(row, retrieved: list[tuple[str, int]]) -> bool | None:
    """검색된 (문서ID, 쪽) 목록에 정답 쪽이 하나라도 있으면 True. 모름 문항은 None(계산 제외)"""
    if row["type"] == "unknown":
        return None
    gold = {(row["doc_id"], p) for p in row["pages"]}
    return any(r in gold for r in retrieved)


# ---------- 답변 점수: 판정 ----------
class JudgeResult(BaseModel):
    judge: Literal["correct", "wrong"] = Field(description="정답과 같은 내용이면 correct, 아니면 wrong")
    reason: str = Field(description="판정 이유 한 문장")


JUDGE_PROMPT = """당신은 통계 보고서 질의응답의 채점자입니다.
[정답]과 [답변]을 비교해 판정하세요.

규칙:
- 숫자·대상이 정답과 같으면 correct (표현, 단위 표기, 반올림 차이는 허용. 예: "161조 4,839억 원" = "약 161.5조 원")
- 숫자가 다르거나, 다른 대상의 값이거나, 정답의 핵심이 빠졌으면 wrong
- 답변에 붙은 근거 표시([D01 p.8] 등)는 판정에 영향을 주지 않음

[질문] {question}
[정답] {answer}
[답변] {prediction}"""


def is_unknown(text: str) -> bool:
    """답변이 '모름'인지 (근거 표시나 마침표가 붙어도 인정)"""
    core = text.strip().split("[")[0].strip().rstrip(".").strip()
    return core == UNKNOWN


def judge_answer(row, prediction: str, judge_llm) -> tuple[str, str]:
    """가이드 2-2 규칙으로 판정. 모름 관련 판정은 규칙으로, 나머지는 채점 모델로"""
    if row["type"] == "unknown":
        if is_unknown(prediction):
            return "correct", "모름 문항에 모름으로 답함"
        return "hallucinated", "모름 문항에 답을 지어냄"
    if is_unknown(prediction):
        return "refused", "답이 있는 문항에 모름으로 답함"
    result = judge_llm.invoke(JUDGE_PROMPT.format(
        question=row["question"], answer=row["answer"], prediction=prediction))
    return result.judge, result.reason


# ---------- 전체 평가 ----------
def run_eval(testset: pd.DataFrame, answer_fn: Callable, judge_llm, k: int = 5) -> pd.DataFrame:
    """answer_fn(question) -> (답변 문자열, [(문서ID, 쪽), ...]) 형태의 함수를 받아 전 문항을 채점"""
    rows = []
    for i, row in testset.iterrows():
        prediction, retrieved = answer_fn(row["question"])
        retrieved = retrieved[:k]
        judge, reason = judge_answer(row, prediction, judge_llm)
        rows.append({
            "id": row["id"], "type": row["type"], "chart_kind": row["chart_kind"],
            "retrieved_pages": ";".join(f"{d}:{p}" for d, p in retrieved),
            "recall_hit": recall_hit(row, retrieved),
            "answer_text": prediction, "judge": judge, "judge_reason": reason,
        })
        if len(rows) % 10 == 0 or len(rows) == len(testset):
            print(f"{len(rows)}/{len(testset)} 완료")
    return pd.DataFrame(rows)


def summarize(results: pd.DataFrame) -> pd.Series:
    """README 9장 표 형태의 요약 (비율은 소수, 예: 0.82 = 82%)"""
    answerable = results[results["type"] != "unknown"]
    unknown = results[results["type"] == "unknown"]

    def acc(mask):
        part = results[mask]
        return round((part["judge"] == "correct").mean(), 3) if len(part) else None

    return pd.Series({
        "Recall@5": round(answerable["recall_hit"].astype(bool).mean(), 3),
        "정답률": round((results["judge"] == "correct").mean(), 3),
        "텍스트": acc(results["type"] == "text"),
        "표": acc(results["type"] == "table"),
        "차트(vector)": acc((results["type"] == "chart") & (results["chart_kind"] == "vector")),
        "차트(image)": acc((results["type"] == "chart") & (results["chart_kind"] == "image")),
        "환각률": round((unknown["judge"] == "hallucinated").mean(), 3) if len(unknown) else None,
    })
