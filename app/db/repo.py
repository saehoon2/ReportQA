"""DB 읽기·쓰기 함수 모음 (PostgreSQL + pgvector, psycopg 사용)

SQL은 이 파일에만 둡니다. 다른 모듈은 여기 함수만 부릅니다.
"""
from contextlib import contextmanager
from pathlib import Path

import numpy as np
import psycopg
from pgvector.psycopg import register_vector
from psycopg.rows import dict_row

from app.config import settings

SCHEMA_PATH = Path(__file__).with_name("schema.sql")


@contextmanager
def connect():
    """DB 연결을 열고, 끝나면 저장(commit) 후 닫음. 오류가 나면 저장하지 않음"""
    conn = psycopg.connect(settings.database_url, row_factory=dict_row, connect_timeout=5)
    try:
        register_vector(conn)
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    """확장(vector)과 테이블 만들기. 이미 있으면 그대로 둠"""
    with psycopg.connect(settings.database_url, connect_timeout=5) as conn:
        conn.execute(SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.commit()


def ping() -> bool:
    try:
        with connect() as conn:
            conn.execute("SELECT 1")
        return True
    except Exception:
        return False


# ---------- documents ----------
def insert_document(title, file_path, source_org=None, source_url=None, license=None,
                    page_ranges=None, code=None) -> int:
    with connect() as conn:
        row = conn.execute(
            """INSERT INTO documents (title, file_path, source_org, source_url, license, page_ranges, code)
               VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id""",
            (title, str(file_path), source_org, source_url, license, page_ranges, code)).fetchone()
        if code is None:   # 직접 올린 문서: 번호로 코드 만들기 (예: U04)
            conn.execute("UPDATE documents SET code = %s WHERE id = %s", (f"U{row['id']:02d}", row["id"]))
        return row["id"]


def update_document(doc_id, **fields):
    if not fields:
        return
    cols = ", ".join(f"{k} = %s" for k in fields)
    with connect() as conn:
        conn.execute(f"UPDATE documents SET {cols} WHERE id = %s", (*fields.values(), doc_id))


def list_documents():
    with connect() as conn:
        return conn.execute("SELECT * FROM documents ORDER BY id").fetchall()


def get_document(doc_id):
    with connect() as conn:
        return conn.execute("SELECT * FROM documents WHERE id = %s", (doc_id,)).fetchone()


def get_document_by_code(code):
    with connect() as conn:
        return conn.execute("SELECT * FROM documents WHERE code = %s", (code,)).fetchone()


def delete_document(doc_id) -> bool:
    with connect() as conn:
        return conn.execute("DELETE FROM documents WHERE id = %s", (doc_id,)).rowcount > 0


def clear_document_content(doc_id):
    """다시 처리하기 전에 이전 청크·이미지 기록을 지움"""
    with connect() as conn:
        conn.execute("DELETE FROM chunks WHERE document_id = %s", (doc_id,))
        conn.execute("DELETE FROM assets WHERE document_id = %s", (doc_id,))


# ---------- assets ----------
def insert_asset(doc_id, page, image_path, bbox, caption, kind="chart") -> int:
    with connect() as conn:
        return conn.execute(
            """INSERT INTO assets (document_id, page, kind, image_path, bbox, caption)
               VALUES (%s, %s, %s, %s, %s, %s) RETURNING id""",
            (doc_id, page, kind, str(image_path), bbox, caption)).fetchone()["id"]


def get_asset(asset_id):
    with connect() as conn:
        return conn.execute("SELECT * FROM assets WHERE id = %s", (asset_id,)).fetchone()


# ---------- chunks ----------
def insert_chunks(rows):
    """rows: [(document_id, asset_id, page, kind, content, embedding), ...]"""
    with connect() as conn:
        with conn.cursor() as cur:
            cur.executemany(
                """INSERT INTO chunks (document_id, asset_id, page, kind, content, embedding)
                   VALUES (%s, %s, %s, %s, %s, %s)""",
                [(d, a, p, k, c, np.asarray(e, dtype=np.float32)) for d, a, p, k, c, e in rows])


_CHUNK_COLS = """c.id, c.document_id, c.asset_id, c.page, c.kind, c.content,
                 d.code AS doc_code, d.title AS doc_title"""


def search_vector(query_embedding, k=20, document_ids=None):
    """질문 벡터와 코사인 거리가 가까운 청크 k개 (완료된 문서만)"""
    sql = f"""SELECT {_CHUNK_COLS}, c.embedding <=> %s AS distance
              FROM chunks c JOIN documents d ON d.id = c.document_id
              WHERE d.status = 'done' {"AND d.id = ANY(%s)" if document_ids else ""}
              ORDER BY c.embedding <=> %s LIMIT %s"""
    q = np.asarray(query_embedding, dtype=np.float32)
    params = (q, list(document_ids), q, k) if document_ids else (q, q, k)
    with connect() as conn:
        return conn.execute(sql, params).fetchall()


def all_chunks():
    """BM25 키워드 색인을 만들 때 쓰는 전체 청크 (완료된 문서만)"""
    with connect() as conn:
        return conn.execute(f"""SELECT {_CHUNK_COLS} FROM chunks c JOIN documents d ON d.id = c.document_id
                                WHERE d.status = 'done' ORDER BY c.id""").fetchall()


def get_chunk(chunk_id):
    with connect() as conn:
        return conn.execute(f"""SELECT {_CHUNK_COLS} FROM chunks c JOIN documents d ON d.id = c.document_id
                                WHERE c.id = %s""", (chunk_id,)).fetchone()


def count_chunks(doc_id):
    with connect() as conn:
        return conn.execute("SELECT count(*) AS n FROM chunks WHERE document_id = %s", (doc_id,)).fetchone()["n"]


# ---------- qa_logs ----------
def log_qa(question, answer, chunk_ids, is_unknown, latency_ms):
    with connect() as conn:
        conn.execute("""INSERT INTO qa_logs (question, answer, chunk_ids, is_unknown, latency_ms)
                        VALUES (%s, %s, %s, %s, %s)""", (question, answer, chunk_ids, is_unknown, latency_ms))
