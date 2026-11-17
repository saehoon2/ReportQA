"""차트 영역 찾기 + Vision LLM 캡션 (v2)

차트는 PDF 안에 두 가지 형태로 들어 있습니다.
  - 그림(이미지) 차트: 이미지 블록. 가는 띠 수백 개로 잘려 있는 경우도 있어 붙어 있는 것끼리 합침
  - 벡터 차트: 선·막대가 도형(drawing)으로 그려진 것. 표 테두리를 뺀 도형이 모인 곳
찾은 영역은 페이지 폭 전체의 띠(band)로 넓혀 이미지로 잘라냅니다. (나란히 놓인 차트 2개, 차트 제목까지 포함)
캡션은 비용이 들기 때문에 data/captions/ 에 저장해 두고 다시 쓰며, 질문할 때는 이미지를 다시 읽지 않습니다.
"""
import base64
import hashlib
import json
from pathlib import Path

import pymupdf

CHART_PROMPT = """당신은 통계 보고서의 차트를 글로 옮기는 도우미입니다. 이미지 안의 모든 차트(그래프)를 아래 규칙대로 옮기세요.
규칙:
1. 차트마다 "[차트] 제목" 한 줄로 시작하세요. 제목이 보이지 않으면 내용을 보고 짧게 붙이고 끝에 (제목 추정)이라고 적으세요.
2. 차트에 적힌 숫자(데이터 레이블)를 빠짐없이 "- 계열 · 항목 = 값" 형식으로 한 줄씩 적으세요. 예: "- 국내관광 · 2015년 = 66.7%"
3. 숫자가 적혀 있지 않은 막대·점은 적지 마세요. 눈대중으로 추정한 값은 쓰지 마세요.
4. 단위(%, 천명, 권 등)가 보이면 값에 붙이세요.
5. 표와 본문 글은 옮기지 마세요. 이미지에 차트가 없으면 "차트 없음"이라고만 답하세요."""

PROMPT_VERSION = hashlib.sha1(CHART_PROMPT.encode()).hexdigest()[:8]


def merge_rects(rects, pad=4):
    """서로 닿거나 pad 이내로 가까운 사각형을 하나로 합침"""
    boxes = [list(pymupdf.Rect(r)) for r in rects]
    changed = True
    while changed:
        changed = False
        out = []
        for b in sorted(boxes, key=lambda b: (b[1], b[0])):
            for o in out:
                if (b[0] <= o[2] + pad and b[2] >= o[0] - pad and b[1] <= o[3] + pad and b[3] >= o[1] - pad):
                    o[0], o[1] = min(o[0], b[0]), min(o[1], b[1])
                    o[2], o[3] = max(o[2], b[2]), max(o[3], b[3])
                    changed = True
                    break
            else:
                out.append(list(b))
        if len(out) == len(boxes):
            changed = False
        boxes = out
    return [pymupdf.Rect(b) for b in boxes]


def chart_regions(page, table_bboxes):
    """페이지에서 차트로 보이는 영역 목록. table_bboxes = 표로 재구성한 영역(차트 아님)"""
    area = page.rect.get_area()
    tabs = [pymupdf.Rect(b) for b in table_bboxes]
    regions = []

    # 1) 그림 차트
    imgs = []
    for info in page.get_image_info():
        x0, y0, x1, y1 = info["bbox"]
        r = pymupdf.Rect(x0, y0, x1, max(y1, y0 + 1)) & page.rect   # 높이 0인 가는 띠도 합칠 수 있게 1pt로
        if r.width > 0:
            imgs.append(r)
    for r in merge_rects(imgs, pad=4):
        if r.get_area() > 0.03 * area and r.height > 60:
            regions.append(r)

    # 2) 벡터 차트: 표 영역 바깥의 도형 묶음
    items = []
    for d in page.get_drawings():
        r = pymupdf.Rect(d["rect"])
        if r.is_empty or r.get_area() > 0.5 * area:
            continue
        if any((t & r).get_area() > 0.5 * r.get_area() for t in tabs):
            continue
        items.append(r)
    for g in merge_rects(items, pad=4):
        n = sum(1 for it in items if g.contains(it))
        if g.get_area() > 0.04 * area and n >= 8 and g.width > 80 and g.height > 60:
            regions.append(g)

    regions = [r for r in merge_rects(regions, pad=2) if not any(t.contains(r) for t in tabs)]
    return regions


def to_bands(page, regions, margin=36, top_pad=30, bottom_pad=12):
    """차트 영역을 페이지 폭 전체의 띠로 넓히고, 위아래로 겹치는 띠는 합침"""
    bands = []
    for r in sorted(regions, key=lambda r: r.y0):
        b = pymupdf.Rect(margin, max(0, r.y0 - top_pad), page.rect.width - margin,
                         min(page.rect.height, r.y1 + bottom_pad))
        if bands and b.y0 <= bands[-1].y1 + 8:
            bands[-1] |= b
        else:
            bands.append(b)
    return bands


def render_png(page, rect, zoom=2.0) -> bytes:
    return page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), clip=rect).tobytes("png")


def caption_image(png: bytes, llm) -> str:
    """Vision LLM(langchain ChatOpenAI)으로 이미지 → 캡션 글"""
    from langchain_core.messages import HumanMessage
    b64 = base64.b64encode(png).decode()
    msg = HumanMessage(content=[
        {"type": "text", "text": CHART_PROMPT},
        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
    ])
    return llm.invoke([msg]).text.strip()


class CaptionCache:
    """캡션 저장소: data/captions/{파일 해시}.json  (같은 PDF를 다시 올려도 Vision을 다시 부르지 않음)"""

    def __init__(self, caption_dir: Path, pdf_path: Path, model: str):
        caption_dir.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha1(Path(pdf_path).read_bytes()).hexdigest()[:16]
        self.path = caption_dir / f"{digest}.json"
        self.model = model
        self.data = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}

    def key(self, page_no, rect):
        return f"{page_no}:{','.join(str(round(x)) for x in rect)}:{self.model}:{PROMPT_VERSION}"

    def get(self, page_no, rect):
        return self.data.get(self.key(page_no, rect))

    def put(self, page_no, rect, caption):
        self.data[self.key(page_no, rect)] = caption
        self.path.write_text(json.dumps(self.data, ensure_ascii=False, indent=1), encoding="utf-8")
