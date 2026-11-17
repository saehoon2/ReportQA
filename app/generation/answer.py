"""답변 생성 (구조도 3의 ⑤): 근거 청크 + 질문 → LLM 답변

프롬프트는 v0~v3 평가에서 쓴 것과 같습니다. (docs/prompts.md)
"""
ANSWER_PROMPT = """당신은 통계 보고서 내용으로만 답하는 도우미입니다.
규칙:
1. 아래 [근거]에 있는 내용으로만 답하세요. 추측하지 마세요.
2. 답의 끝에 사용한 근거를 [문서ID p.쪽] 형식으로 표시하세요. 예: [D01 p.8]
3. [근거]에서 답을 찾을 수 없으면 정확히 "모름"이라고만 답하세요.

[근거]
{context}

[질문]
{question}"""

UNKNOWN = "모름"


def format_context(chunks) -> str:
    """chunks: [{'doc_code':…, 'page':…, 'content':…}]"""
    return "\n\n".join(f"[{c['doc_code']} p.{c['page']}]\n{c['content']}" for c in chunks)


def generate_answer(question: str, chunks, llm) -> str:
    return llm.invoke(ANSWER_PROMPT.format(context=format_context(chunks), question=question)).text.strip()


def is_unknown(text: str) -> bool:
    """'모름' 또는 '모름. [D01 p.3]' 처럼 근거 표시만 붙은 경우도 모름으로 봄"""
    core = text.strip().split("[")[0].strip().rstrip(".").strip()
    return core == UNKNOWN
