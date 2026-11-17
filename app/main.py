"""FastAPI 서버 시작점 (API 설명: docs/api_spec.md)

실행 (프로젝트 폴더에서):
    uvicorn app.main:app --reload --port 8000
브라우저에서 http://localhost:8000/docs 를 열면 API를 직접 눌러 볼 수 있습니다.
"""
import logging
import shutil
import uuid
from contextlib import asynccontextmanager

import pymupdf
from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field

from app import qa
from app.config import data_path, rel_data_path, settings
from app.db import repo
from app.ingest.pipeline import process_document

logging.basicConfig(level=settings.log_level)
log = logging.getLogger(__name__)
MAX_UPLOAD_MB = 50


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        repo.init_db()           # 테이블이 없으면 만듦
    except Exception as e:      # DB가 꺼져 있어도 서버는 켜지고, /health 에서 알려줌
        log.warning("DB 초기화 실패: %s", e)
    yield


app = FastAPI(title="ReportQA API", version="1.0", lifespan=lifespan,
              description="통계 보고서 PDF의 본문·표·차트를 읽고 근거와 함께 답하는 RAG API")


# ---------- 요청·응답 모양 (Pydantic 모델) ----------
class AskRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=500, examples=["2025년 4분기 음악 산업 매출액은?"])
    document_ids: list[int] | None = Field(None, description="이 문서들 안에서만 찾기. 비우면 전체")
    top_k: int = Field(5, ge=1, le=10, description="답변에 쓸 근거 수")


def _doc_out(d):
    return {k: d[k] for k in ("id", "code", "title", "source_org", "source_url", "license", "page_count",
                              "page_ranges", "n_tables", "n_charts", "status", "progress", "error")} | {
        "created_at": d["created_at"].isoformat()}


# ---------- 경로 ----------
@app.get("/health")
def health():
    ok = repo.ping()
    n = len(repo.list_documents()) if ok else 0
    return {"status": "ok", "db": ok, "documents": n, "llm_model": settings.llm_model,
            "reranker": settings.reranker_model if settings.use_reranker else None}


@app.get("/documents")
def list_documents():
    return [_doc_out(d) for d in repo.list_documents()]


@app.get("/documents/{doc_id}")
def get_document(doc_id: int):
    d = repo.get_document(doc_id)
    if d is None:
        raise HTTPException(404, "문서를 찾을 수 없습니다")
    return _doc_out(d) | {"n_chunks": repo.count_chunks(doc_id)}


@app.post("/documents", status_code=202)
def upload_document(background: BackgroundTasks, file: UploadFile = File(...),
                    title: str | None = Form(None), source_org: str | None = Form(None),
                    page_ranges: str | None = Form(None, description="예: 1-40 또는 13-22,37-54. 비우면 전체")):
    if not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(400, "PDF 파일만 올릴 수 있습니다")
    upload_dir = settings.pdf_dir / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    path = upload_dir / f"{uuid.uuid4().hex[:12]}.pdf"     # 한글 파일 이름 문제를 피하려고 새 이름으로 저장
    with path.open("wb") as f:
        shutil.copyfileobj(file.file, f)
    if path.stat().st_size > MAX_UPLOAD_MB * 1024 * 1024:
        path.unlink()
        raise HTTPException(400, f"{MAX_UPLOAD_MB}MB 이하만 올릴 수 있습니다")
    try:
        pymupdf.open(path).close()
    except Exception:
        path.unlink()
        raise HTTPException(400, "PDF를 열 수 없습니다")
    doc_id = repo.insert_document(title or file.filename, rel_data_path(path), source_org,
                                  page_ranges=page_ranges or None)
    background.add_task(_process_and_refresh, doc_id)        # 처리는 백그라운드에서 (화면은 기다리지 않음)
    return {"id": doc_id, "status": "pending"}


def _process_and_refresh(doc_id):
    process_document(doc_id)
    qa.invalidate()


@app.post("/documents/{doc_id}/retry", status_code=202)
def retry_document(doc_id: int, background: BackgroundTasks):
    if repo.get_document(doc_id) is None:
        raise HTTPException(404, "문서를 찾을 수 없습니다")
    repo.update_document(doc_id, status="pending", error=None)
    background.add_task(_process_and_refresh, doc_id)
    return {"id": doc_id, "status": "pending"}


@app.delete("/documents/{doc_id}")
def delete_document(doc_id: int):
    d = repo.get_document(doc_id)
    if d is None:
        raise HTTPException(404, "문서를 찾을 수 없습니다")
    repo.delete_document(doc_id)       # 청크·이미지 기록도 함께 지워짐 (ON DELETE CASCADE)
    path = data_path(d["file_path"])
    if "uploads" in path.parts and path.exists():   # 직접 올린 파일만 지움 (기본 문서 PDF는 남김)
        path.unlink()
    qa.invalidate()
    return {"deleted": doc_id}


@app.post("/ask")
def ask(req: AskRequest):
    if not repo.ping():
        raise HTTPException(503, "DB에 연결할 수 없습니다")
    return qa.ask(req.question, req.document_ids, req.top_k)


@app.get("/assets/{asset_id}")
def get_asset(asset_id: int):
    a = repo.get_asset(asset_id)
    if a is None or not data_path(a["image_path"]).exists():
        raise HTTPException(404, "이미지를 찾을 수 없습니다")
    return FileResponse(data_path(a["image_path"]), media_type="image/png")


@app.get("/assets/{asset_id}/caption")
def get_asset_caption(asset_id: int):
    a = repo.get_asset(asset_id)
    if a is None:
        raise HTTPException(404, "이미지를 찾을 수 없습니다")
    return {"id": a["id"], "page": a["page"], "bbox": a["bbox"], "caption": a["caption"]}


@app.get("/documents/{doc_id}/pages/{page_no}")
def page_image(doc_id: int, page_no: int, highlight: str | None = None):
    """원본 페이지 이미지 (근거 상세 화면용). highlight='x0,y0,x1,y1' 이면 그 영역에 빨간 상자"""
    d = repo.get_document(doc_id)
    if d is None:
        raise HTTPException(404, "문서를 찾을 수 없습니다")
    pdf = pymupdf.open(data_path(d["file_path"]))
    if not 1 <= page_no <= len(pdf):
        raise HTTPException(404, "쪽 번호가 범위를 벗어났습니다")
    page = pdf[page_no - 1]
    if highlight:
        try:
            page.draw_rect(pymupdf.Rect([float(x) for x in highlight.split(",")]), color=(1, 0, 0), width=2)
        except ValueError:
            pass
    png = page.get_pixmap(matrix=pymupdf.Matrix(1.5, 1.5)).tobytes("png")
    return Response(png, media_type="image/png")


@app.get("/chunks/{chunk_id}")
def get_chunk(chunk_id: int):
    c = repo.get_chunk(chunk_id)
    if c is None:
        raise HTTPException(404, "청크를 찾을 수 없습니다")
    return c
