"""PDF 한 개 → 검색용 청크 (본문 / 표 / 차트 캡션)

노트북 04(v1)의 문서 읽기 과정을 서버에서도 쓸 수 있게 함수로 옮긴 것입니다.
  1. parse_pdf     : 쪽마다 섹션 추적, 표 재구성, 표를 뺀 본문, 차트 영역 찾기
  2. caption_charts: 차트 영역을 이미지로 저장하고 Vision LLM 캡션 생성 (저장된 캡션이 있으면 재사용)
  3. build_chunks  : 본문은 800자로 나누고, 표·차트는 행 단위로 나눠 청크 목록을 만듦
"""
import re
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf
from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

from app.ingest.charts import CaptionCache, caption_image, chart_regions, render_png, to_bands
from app.ingest.tables import split_table, table_lines, text_without_tables, update_sections


@dataclass
class ChartJob:
    page: int
    rect: tuple
    section: str
    caption: str = ""
    image_path: str = ""


@dataclass
class ParsedDoc:
    doc_id: str
    pdf_path: Path
    page_count: int
    text_docs: list = field(default_factory=list)
    table_docs: list = field(default_factory=list)
    chart_jobs: list = field(default_factory=list)
    n_tables: int = 0


def clean(text):
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text)
    return text.strip()


def looks_like_chart(body_lines):
    """표로 찾았지만 행 이름이 대부분 비어 있으면 차트를 표로 잘못 찾은 것으로 봄"""
    empty = sum(line.startswith("- :") for line in body_lines)
    return empty >= max(1, len(body_lines) * 0.5)


def in_ranges(page_no, page_ranges):
    return page_ranges is None or any(a <= page_no <= b for a, b in page_ranges)


def parse_pdf(pdf_path, doc_id, page_ranges=None, find_charts=True) -> ParsedDoc:
    pdf = pymupdf.open(pdf_path)
    parsed = ParsedDoc(doc_id=doc_id, pdf_path=Path(pdf_path), page_count=len(pdf))
    sections = []
    for page in pdf:
        page_no = page.number + 1
        section = update_sections(page, sections)        # 범위 밖 쪽도 읽어 섹션을 이어감
        if not in_ranges(page_no, page_ranges):
            continue
        table_bboxes, image_tables = [], []
        for t in page.find_tables().tables:
            result = table_lines(page, t.bbox, section)
            if result is None:
                continue
            header, body = result
            if looks_like_chart(body):                    # 차트 → Vision으로 보냄
                image_tables.append(t.bbox)
                continue
            parsed.n_tables += 1
            table_bboxes.append(t.bbox)
            parsed.table_docs.append((page_no, header, body))
        text = clean(text_without_tables(page, table_bboxes))
        if len(text) >= 50:
            parsed.text_docs.append(Document(page_content=text, metadata={
                "doc_id": doc_id, "page": page_no, "kind": "text"}))
        if find_charts:
            regions = chart_regions(page, table_bboxes) + [pymupdf.Rect(b) for b in image_tables]
            for band in to_bands(page, regions):
                parsed.chart_jobs.append(ChartJob(page=page_no, rect=tuple(round(x, 1) for x in band),
                                                  section=section))
    return parsed


def caption_charts(parsed: ParsedDoc, llm, caption_dir: Path, asset_dir: Path, model_name: str,
                   max_new=None, progress=None):
    """차트 이미지를 저장하고 캡션을 채움. 저장된 캡션이 없을 때만 Vision LLM 호출 (최대 max_new번)"""
    pdf = pymupdf.open(parsed.pdf_path)
    cache = CaptionCache(caption_dir, parsed.pdf_path, model_name)
    out_dir = Path(asset_dir) / parsed.doc_id
    out_dir.mkdir(parents=True, exist_ok=True)
    called = 0
    for i, job in enumerate(parsed.chart_jobs):
        page = pdf[job.page - 1]
        png = render_png(page, pymupdf.Rect(job.rect))
        image_path = out_dir / f"p{job.page:03d}_{i:02d}.png"
        image_path.write_bytes(png)
        job.image_path = str(image_path)
        cached = cache.get(job.page, job.rect)
        if cached is None:
            if llm is None or (max_new is not None and called >= max_new):
                continue
            cached = caption_image(png, llm)
            cache.put(job.page, job.rect, cached)
            called += 1
        job.caption = cached
        if progress:
            progress(i + 1, len(parsed.chart_jobs))
    return called


def split_charts(caption):
    """캡션 글을 차트 단위로 나눔 → [(제목, [값 줄들])]. '[차트] 제목' 줄이 새 차트의 시작"""
    charts = []
    for line in caption.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("[차트]"):
            charts.append((line.removeprefix("[차트]").strip(), []))
        elif charts:
            charts[-1][1].append(line)
        else:
            charts.append(("", [line]))
    return [(t, ls) for t, ls in charts if ls]


def build_chunks(parsed: ParsedDoc, chunk_size=800, chunk_overlap=100, with_charts=True):
    """본문·표·차트 청크(langchain Document) 목록"""
    splitter = RecursiveCharacterTextSplitter(chunk_size=chunk_size, chunk_overlap=chunk_overlap)
    chunks = [c for c in splitter.split_documents(parsed.text_docs) if len(c.page_content) >= 30]
    for page_no, header, body in parsed.table_docs:
        for c in split_table(header, body, max_chars=chunk_size):
            chunks.append(Document(page_content=c, metadata={"doc_id": parsed.doc_id, "page": page_no, "kind": "table"}))
    if with_charts:
        for job in parsed.chart_jobs:
            cap = job.caption.strip()
            if not cap or cap.startswith("차트 없음"):
                continue
            for title, lines in split_charts(cap):
                # 차트 제목을 머리말에 넣어, 긴 차트가 여러 조각으로 나뉘어도 조각마다 제목이 붙게 함
                header = " ".join(x for x in ["[차트]", f"[{job.section}]" if job.section else "", title] if x)
                for c in split_table(header, lines, max_chars=chunk_size):
                    chunks.append(Document(page_content=c, metadata={
                        "doc_id": parsed.doc_id, "page": job.page, "kind": "chart", "image_path": job.image_path}))
    return chunks
