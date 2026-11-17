"""ReportQA 화면 (Streamlit) — 목업 1~4

실행 (프로젝트 폴더에서, API 서버가 켜져 있어야 함):
    streamlit run ui/streamlit_app.py
화면은 FastAPI 서버(API_BASE_URL)에 요청만 보내고, 평가 결과 화면만 eval/results/*.csv 파일을 직접 읽습니다.
"""
import os
from pathlib import Path

import pandas as pd
import requests
import streamlit as st

API = os.getenv("API_BASE_URL", "http://localhost:8000").rstrip("/")
EVAL_DIR = Path(__file__).resolve().parent.parent / "eval"
KIND_KO = {"text": "본문", "table": "표", "chart": "차트"}
STATUS_KO = {"pending": "대기", "processing": "처리 중", "done": "완료", "failed": "실패"}

st.set_page_config(page_title="ReportQA", page_icon="📊", layout="wide")


# ---------- 서버 호출 ----------
def api(method, path, **kw):
    try:
        r = requests.request(method, API + path, timeout=kw.pop("timeout", 30), **kw)
    except requests.RequestException:
        return None, "서버에 연결할 수 없습니다"
    if r.status_code >= 400:
        try:
            return None, r.json().get("detail", r.text)
        except ValueError:
            return None, r.text
    return (r.json() if "json" in r.headers.get("content-type", "") else r.content), None


@st.cache_data(ttl=600, show_spinner=False)
def fetch_bytes(path):
    data, err = api("GET", path)
    return data if err is None else None


# ---------- 왼쪽 메뉴 ----------
with st.sidebar:
    st.title("📊 ReportQA")
    st.caption("통계 보고서의 본문·표·차트를 읽고 근거와 함께 답합니다")
    page = st.radio("메뉴", ["질문하기", "문서 관리", "평가 결과"], label_visibility="collapsed")
    health, err = api("GET", "/health", timeout=5)
    st.divider()
    if health and health.get("db"):
        st.success(f"서버 연결됨 · 문서 {health['documents']}개")
    elif health:
        st.warning("서버는 켜져 있지만 DB에 연결할 수 없습니다")
    else:
        st.error("서버 연결 안 됨")
        st.caption(f"API 주소: {API}")


# ================= 목업 1: 문서 관리 =================
def page_documents():
    st.header("문서 관리")
    with st.form("upload", clear_on_submit=True):
        f = st.file_uploader("PDF 파일을 끌어다 놓거나 클릭해서 선택하세요 (최대 50MB · PDF만)", type=["pdf"])
        c1, c2, c3 = st.columns(3)
        title = c1.text_input("문서 제목 (비우면 파일 이름)")
        org = c2.text_input("발행 기관")
        ranges = c3.text_input("사용할 쪽 범위 (예: 1-30, 비우면 전체)")
        st.caption("⚠️ 차트마다 Vision LLM을 호출하므로 OpenAI 비용이 듭니다 (차트 1개 약 $0.004, 문서당 최대 60개).")
        if st.form_submit_button("업로드", type="primary") and f is not None:
            data, err = api("POST", "/documents", timeout=120,
                            files={"file": (f.name, f.getvalue(), "application/pdf")},
                            data={"title": title or f.name, "source_org": org, "page_ranges": ranges})
            st.success(f"업로드 완료 · 문서 번호 {data['id']} (처리는 서버에서 계속됩니다)") if data else st.error(err)

    head = st.columns([6, 1])
    docs, err = api("GET", "/documents")
    head[0].subheader(f"업로드된 문서 ({len(docs) if docs else 0})")
    if head[1].button("🔄 새로고침"):
        st.rerun()
    if err:
        st.error(err)
        return
    cols = [3, 1.2, 0.6, 1, 1.6, 1.4]
    hdr = st.columns(cols)
    for c, name in zip(hdr, ["문서명", "기관", "쪽", "표 / 차트", "상태", ""]):
        c.markdown(f"**{name}**")
    for d in docs:
        row = st.columns(cols)
        row[0].write(f"`{d['code']}` {d['title']}")
        row[1].write(d["source_org"] or "-")
        row[2].write(str(d["page_count"] or "—"))
        row[3].write(f"{d['n_tables']} / {d['n_charts']}" if d["status"] != "pending" else "—")
        status = STATUS_KO.get(d["status"], d["status"])
        if d["status"] == "done":
            row[4].success(status)
        elif d["status"] == "failed":
            row[4].error(f"{status}: {d['error'] or ''}")
        else:
            row[4].info(f"{status} · {d['progress'] or ''}")
        if d["status"] == "failed" and row[5].button("재시도", key=f"r{d['id']}"):
            api("POST", f"/documents/{d['id']}/retry")
            st.rerun()
        if row[5].button("삭제", key=f"d{d['id']}"):
            st.session_state["confirm_delete"] = d["id"]
        if st.session_state.get("confirm_delete") == d["id"]:
            st.warning(f"'{d['title']}' 문서와 검색 데이터를 삭제할까요? 되돌릴 수 없습니다.")
            y, n = st.columns([1, 8])
            if y.button("삭제 확인", key=f"y{d['id']}", type="primary"):
                api("DELETE", f"/documents/{d['id']}")
                st.session_state.pop("confirm_delete")
                st.rerun()
            if n.button("취소", key=f"n{d['id']}"):
                st.session_state.pop("confirm_delete")
                st.rerun()


# ================= 목업 2: 질문하기 =================
def source_label(s):
    return f"[{s['rank']}] {s['doc_code']} p.{s['page']} · {KIND_KO.get(s['kind'], s['kind'])}"


def relevance(score):
    if score is None:
        return ""
    return "관련도 높음" if score >= 0.5 else "관련도 보통" if score >= 0.1 else "관련도 낮음"


def page_ask():
    docs, _ = api("GET", "/documents")
    done = [d for d in (docs or []) if d["status"] == "done"]
    with st.sidebar:
        st.subheader("검색할 문서")
        selected = [d["id"] for d in done if st.checkbox(f"{d['code']} {d['title'][:18]}", value=True, key=f"c{d['id']}")]
        top_k = st.select_slider("근거 개수", options=[3, 5, 8], value=5)
        show_img = st.checkbox("근거 이미지 보기", value=True)
        if st.button("대화 지우기"):
            st.session_state["history"] = []
            st.session_state.pop("detail", None)

    if st.session_state.get("detail"):
        return page_detail(st.session_state["detail"])

    st.header("질문하기")
    if not done:
        st.info("처리가 끝난 문서가 없습니다. 문서 관리에서 PDF를 올리거나 `python -m app.ingest.load_samples`로 기본 문서를 넣어 주세요.")
    history = st.session_state.setdefault("history", [])
    for qi, item in enumerate(history):
        with st.chat_message("user"):
            st.write(item["question"])
        with st.chat_message("assistant"):
            res = item["result"]
            if res["is_unknown"]:
                st.warning(res["answer"] + " 다른 문서를 선택하거나 질문을 바꿔 보세요.")
            else:
                st.write(res["answer"])
            src = res["sources"]
            st.caption(" · ".join(source_label(s) for s in src) + f"  —  근거 {len(src)}개 사용 · {res['latency_ms'] / 1000:.1f}초")
            for i, s in enumerate(src):
                with st.expander(f"{source_label(s)} · {relevance(s['rerank_score'])}", expanded=(i == 0 and not res["is_unknown"])):
                    if show_img and s["asset_id"]:
                        img = fetch_bytes(f"/assets/{s['asset_id']}")
                        if img:
                            st.image(img, use_container_width=True)
                    st.text(s["content"][:1500])
                    if st.button("근거 상세 보기", key=f"det{qi}_{i}"):
                        st.session_state["detail"] = s
                        st.rerun()

    q = st.chat_input("질문을 입력하세요 (예: 2025년 4분기 음악 산업 매출액은?)")
    if q:
        with st.spinner("근거를 찾고 답변을 만드는 중..."):
            res, err = api("POST", "/ask", timeout=180,
                           json={"question": q, "document_ids": selected or None, "top_k": top_k})
        if err:
            st.error(err)
        else:
            history.append({"question": q, "result": res})
            st.rerun()


# ================= 목업 3: 근거 상세 =================
def page_detail(s):
    if st.button("← 답변으로"):
        st.session_state.pop("detail")
        st.rerun()
    st.header(f"근거 [{s['rank']}] · {KIND_KO.get(s['kind'], s['kind'])}")
    doc, _ = api("GET", f"/documents/{s['document_id']}")
    left, right = st.columns([1.1, 1])
    with left:
        nav = st.session_state.setdefault("detail_page", {}).get(s["chunk_id"], s["page"])
        bbox = None
        if s["asset_id"]:
            cap, _ = api("GET", f"/assets/{s['asset_id']}/caption")
            bbox = cap["bbox"] if cap and nav == s["page"] else None
        img = fetch_bytes(f"/documents/{s['document_id']}/pages/{nav}" + (f"?highlight={bbox}" if bbox else ""))
        st.caption(f"{doc['title'] if doc else ''} · {nav} / {doc['page_count'] if doc else '?'}쪽")
        if img:
            st.image(img, use_container_width=True)
        p1, p2 = st.columns(2)
        if p1.button(f"◀ {nav - 1}쪽", disabled=nav <= 1):
            st.session_state["detail_page"][s["chunk_id"]] = nav - 1
            st.rerun()
        if p2.button(f"{nav + 1}쪽 ▶", disabled=bool(doc) and nav >= (doc["page_count"] or nav)):
            st.session_state["detail_page"][s["chunk_id"]] = nav + 1
            st.rerun()
    with right:
        st.markdown(f"**문서** {doc['title'] if doc else s['doc_code']} ({doc['source_org'] if doc else ''})")
        st.markdown(f"**위치** {s['page']}쪽 · 청크 #{s['chunk_id']} ({s['kind']})")
        if doc and doc.get("license"):
            st.caption(f"출처: {doc['source_org']} · 이용 조건: {doc['license']}")
        st.subheader("검색된 글" if s["kind"] != "chart" else "Vision LLM이 만든 캡션")
        st.text(s["content"])
        st.subheader("검색 점수 (왜 뽑혔나)")
        st.table(pd.DataFrame([
            {"방식": "벡터 검색", "순위": s["vector_rank"] or "-", "설명": "뜻이 비슷함"},
            {"방식": "BM25", "순위": s["bm25_rank"] or "-", "설명": "단어가 일치함"},
            {"방식": "리랭커", "순위": s["rank"], "설명": f"최종 순위 (점수 {s['rerank_score']:.3f})" if s["rerank_score"] is not None else "최종 순위"},
        ]).set_index("방식"))


# ================= 목업 4: 평가 결과 =================
def page_eval():
    st.header("평가 결과")
    from_dir = sorted((EVAL_DIR / "results").glob("v[0-9].csv"))
    if not from_dir:
        st.info("eval/results/ 에 결과 파일이 없습니다.")
        return
    sys_path = str(EVAL_DIR.parent)
    import sys
    if sys_path not in sys.path:
        sys.path.insert(0, sys_path)
    from eval.rag_eval import summarize

    results = {p.stem: pd.read_csv(p) for p in from_dir}
    testset = pd.read_csv(EVAL_DIR / "testset.csv", dtype=str)
    st.caption(f"테스트셋 {len(testset)}문항 (텍스트 40 · 표 30 · 차트 30 · 모름 10) · LLM 채점, 사람 검토 일치율 95%")
    names = list(results)
    c1, c2 = st.columns([1, 2])
    base = c1.selectbox("기준 버전", names, index=len(names) - 1)
    others = c2.multiselect("비교 대상", [n for n in names if n != base], default=[n for n in names if n != base])

    summ = {n: summarize(r) for n, r in results.items()}
    s = summ[base]
    m = st.columns(4)
    m[0].metric("검색 Recall@5", f"{s['Recall@5']:.1%}")
    m[1].metric("답변 정답률", f"{s['정답률']:.1%}")
    unk = results[base][results[base]["type"] == "unknown"]
    m[2].metric("\"모름\" 정확도", f"{(unk['judge'] == 'correct').mean():.0%}")
    m[3].metric("환각률", f"{s['환각률']:.0%}")

    st.subheader("질문 유형별 정답률 (버전 비교)")
    cols = ["텍스트", "표", "차트(vector)", "차트(image)"]
    chart_df = pd.DataFrame({n: summ[n][cols] for n in [*others, base] if n in summ}).T[cols] * 100
    chart_df = chart_df.loc[sorted(chart_df.index)]
    st.bar_chart(chart_df.T, stack=False, y_label="정답률(%)")
    st.dataframe((pd.DataFrame(summ).T * 100).round(1).loc[sorted(summ)], use_container_width=True)

    st.subheader(f"오답 사례 ({base})")
    r = results[base].merge(testset[["id", "question", "answer"]], on="id")
    wrong = r[r["judge"] != "correct"].copy()
    wrong["원인 분류"] = wrong.apply(lambda x: "검색 실패" if x["recall_hit"] is False or str(x["recall_hit"]) == "False"
                                    else "모름으로 답함" if x["judge"] == "refused" else "답변 오류", axis=1)
    kinds = st.multiselect("유형", ["text", "table", "chart", "unknown"], default=["text", "table", "chart", "unknown"])
    st.dataframe(wrong[wrong["type"].isin(kinds)][["id", "type", "question", "answer", "answer_text", "원인 분류"]],
                 use_container_width=True, hide_index=True)


{"질문하기": page_ask, "문서 관리": page_documents, "평가 결과": page_eval}[page]()
