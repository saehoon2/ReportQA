"""문서 1개 처리: PDF → 청크 → 임베딩 → DB 저장  (구조도 2 인덱싱 흐름)

POST /documents 로 올린 문서는 서버가 백그라운드에서 이 함수를 실행합니다.
상태: pending(대기) → processing(처리 중) → done(완료) / failed(실패)
"""
import logging
import traceback

from langchain_openai import ChatOpenAI, OpenAIEmbeddings

from app.config import data_path, rel_data_path, settings
from app.db import repo
from app.ingest.parse import build_chunks, caption_charts, parse_pdf
from app.ingest.samples import text_to_ranges

log = logging.getLogger(__name__)


def process_document(doc_id: int, use_vision: bool = True):
    doc = repo.get_document(doc_id)
    if doc is None:
        return
    try:
        repo.update_document(doc_id, status="processing", progress="PDF 분해", error=None)
        repo.clear_document_content(doc_id)
        parsed = parse_pdf(data_path(doc["file_path"]), doc["code"], text_to_ranges(doc["page_ranges"]))
        if not parsed.text_docs and not parsed.table_docs:
            raise ValueError("글자를 찾을 수 없음 (스캔본 PDF일 수 있음)")
        repo.update_document(doc_id, page_count=parsed.page_count, n_tables=parsed.n_tables,
                             n_charts=len(parsed.chart_jobs))

        # 차트 캡션 (저장된 캡션은 재사용, 새로 만드는 것은 최대 MAX_CAPTION_PAGES개 → 비용 상한)
        vision = ChatOpenAI(model=settings.vision_model) if use_vision and settings.openai_api_key else None
        caption_charts(parsed, vision, settings.caption_dir, settings.asset_dir, settings.vision_model,
                       max_new=settings.max_caption_pages,
                       progress=lambda i, n: repo.update_document(doc_id, progress=f"차트 캡션 {i}/{n}"))

        # 차트 이미지 기록 → 청크와 연결할 asset_id
        asset_ids = {}
        for job in parsed.chart_jobs:
            if job.image_path:
                asset_ids[job.image_path] = repo.insert_asset(
                    doc_id, job.page, rel_data_path(job.image_path), ",".join(str(x) for x in job.rect), job.caption)

        repo.update_document(doc_id, progress="임베딩")
        chunks = build_chunks(parsed, settings.chunk_size, settings.chunk_overlap)
        vectors = OpenAIEmbeddings(model=settings.embedding_model).embed_documents([c.page_content for c in chunks])
        repo.insert_chunks([
            (doc_id, asset_ids.get(c.metadata.get("image_path")), c.metadata["page"], c.metadata["kind"],
             c.page_content, v)
            for c, v in zip(chunks, vectors)])
        repo.update_document(doc_id, status="done", progress=f"청크 {len(chunks)}개")
        log.info("문서 %s 처리 완료: 청크 %d개", doc_id, len(chunks))
    except Exception as e:
        log.error("문서 %s 처리 실패: %s", doc_id, traceback.format_exc())
        repo.update_document(doc_id, status="failed", error=str(e)[:500])
