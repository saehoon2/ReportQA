-- ReportQA 데이터베이스 구조 (설명: docs/db_schema.md)
-- 서버가 시작할 때 실행됩니다. IF NOT EXISTS 덕분에 여러 번 실행해도 안전합니다.

CREATE EXTENSION IF NOT EXISTS vector;

-- 문서 1개 = 1줄
CREATE TABLE IF NOT EXISTS documents (
    id          SERIAL PRIMARY KEY,
    code        TEXT UNIQUE,                       -- 답변 출처 표시용 짧은 ID (D01, D02, ...)
    title       TEXT NOT NULL,
    source_org  TEXT,
    source_url  TEXT,
    license     TEXT,
    file_path   TEXT NOT NULL,
    page_count  INT,
    page_ranges TEXT,                              -- 사용할 쪽 범위 (예: '13-22,37-54'). 비어 있으면 전체
    n_tables    INT DEFAULT 0,
    n_charts    INT DEFAULT 0,
    status      TEXT NOT NULL DEFAULT 'pending',   -- pending / processing / done / failed
    progress    TEXT,                              -- 진행 상황 (예: '차트 캡션 13/20')
    error       TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 차트 이미지 1개 = 1줄
CREATE TABLE IF NOT EXISTS assets (
    id          SERIAL PRIMARY KEY,
    document_id INT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    page        INT NOT NULL,
    kind        TEXT NOT NULL DEFAULT 'chart',
    image_path  TEXT NOT NULL,
    bbox        TEXT,                              -- 페이지 안 위치 'x0,y0,x1,y1' (근거 영역 강조용)
    caption     TEXT
);

-- 검색 단위(청크) 1개 = 1줄
CREATE TABLE IF NOT EXISTS chunks (
    id          SERIAL PRIMARY KEY,
    document_id INT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    asset_id    INT REFERENCES assets(id) ON DELETE SET NULL,   -- 차트 청크면 이미지 번호, 아니면 비움
    page        INT NOT NULL,
    kind        TEXT NOT NULL,                     -- text / table / chart
    content     TEXT NOT NULL,
    embedding   vector(1536),                      -- text-embedding-3-small 의 차원 수
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS chunks_document_idx ON chunks (document_id);
-- 벡터 검색을 빠르게 하는 색인 (코사인 거리)
CREATE INDEX IF NOT EXISTS chunks_embedding_idx ON chunks USING hnsw (embedding vector_cosine_ops);

-- 질문 1개 = 1줄
CREATE TABLE IF NOT EXISTS qa_logs (
    id          SERIAL PRIMARY KEY,
    question    TEXT NOT NULL,
    answer      TEXT,
    chunk_ids   INT[],
    is_unknown  BOOLEAN,
    latency_ms  INT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
