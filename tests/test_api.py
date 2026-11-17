"""API 테스트: DB 없이 확인할 수 있는 동작 (DB가 꺼진 상황 포함)"""
from fastapi.testclient import TestClient

from app import main


def client(monkeypatch, db_ok=False):
    monkeypatch.setattr(main.repo, "init_db", lambda: None)
    monkeypatch.setattr(main.repo, "ping", lambda: db_ok)
    monkeypatch.setattr(main.repo, "list_documents", lambda: [])
    return TestClient(main.app)


def test_health_reports_db_down(monkeypatch):
    with client(monkeypatch) as c:
        r = c.get("/health")
    assert r.status_code == 200
    assert r.json()["db"] is False


def test_ask_returns_503_when_db_down(monkeypatch):
    with client(monkeypatch) as c:
        r = c.post("/ask", json={"question": "청년층 고용률은?"})
    assert r.status_code == 503


def test_ask_validates_request(monkeypatch):
    with client(monkeypatch, db_ok=True) as c:
        assert c.post("/ask", json={"question": ""}).status_code == 422          # 빈 질문
        assert c.post("/ask", json={"question": "q", "top_k": 50}).status_code == 422


def test_upload_rejects_non_pdf(monkeypatch):
    with client(monkeypatch) as c:
        r = c.post("/documents", files={"file": ("memo.txt", b"hello", "text/plain")})
    assert r.status_code == 400
