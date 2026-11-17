"""기본 문서 3개(D01~D03)를 DB에 등록하고 처리하는 명령

사용법 (프로젝트 폴더에서):
    python -m app.ingest.load_samples
- data/pdf/ 에 PDF 3개가 있어야 합니다. (docs/data_sources.md 링크에서 받기)
- 이미 등록된 문서는 건너뜁니다.
- 차트 캡션은 노트북 05에서 만든 data/captions/ 를 재사용하므로, 캡션이 있으면 Vision 비용이 들지 않습니다.
"""
import logging

from app.config import rel_data_path, settings
from app.db import repo
from app.ingest.pipeline import process_document
from app.ingest.samples import SAMPLE_DOCS, ranges_to_text


def main():
    logging.basicConfig(level=settings.log_level)
    repo.init_db()
    for s in SAMPLE_DOCS:
        path = settings.pdf_dir / s["file"]
        if not path.exists():
            print(f"[건너뜀] {s['code']}: 파일 없음 → {path}")
            continue
        existing = repo.get_document_by_code(s["code"])
        if existing and existing["status"] == "done":
            print(f"[이미 있음] {s['code']} {s['title']}")
            continue
        doc_id = existing["id"] if existing else repo.insert_document(
            s["title"], rel_data_path(path), s["source_org"], s["source_url"], s["license"],
            ranges_to_text(s["page_ranges"]), code=s["code"])
        print(f"[처리 중] {s['code']} {s['title']} ...")
        process_document(doc_id)
        doc = repo.get_document(doc_id)
        print(f"  → {doc['status']} ({doc['progress'] or doc['error']})")


if __name__ == "__main__":
    main()
