# API 명세

FastAPI 서버 주소: `http://localhost:8000` (Docker Compose 안에서는 `http://api:8000`)
서버를 켠 뒤 **http://localhost:8000/docs** 에서 모든 API를 직접 눌러 볼 수 있습니다. (FastAPI가 자동으로 만들어 주는 화면)

코드: [app/main.py](../app/main.py)

| 방식 | 주소 | 하는 일 | 화면 |
|---|---|---|---|
| GET | `/health` | 서버·DB 상태 | 왼쪽 메뉴 "서버 상태" |
| GET | `/documents` | 문서 목록과 처리 상태 | 문서 관리 |
| GET | `/documents/{id}` | 문서 상세 (청크 수 포함) | 문서 관리 |
| POST | `/documents` | PDF 업로드. 처리는 백그라운드에서 진행 | 문서 관리 |
| POST | `/documents/{id}/retry` | 실패한 문서 다시 처리 | 문서 관리 |
| DELETE | `/documents/{id}` | 문서와 관련 청크 삭제 | 문서 관리 |
| POST | `/ask` | 질문 → 답변 + 근거 | 질문하기 |
| GET | `/assets/{id}` | 차트 이미지(PNG) | 질문하기 · 근거 상세 |
| GET | `/assets/{id}/caption` | 차트 캡션과 위치 | 근거 상세 |
| GET | `/documents/{id}/pages/{page}` | 원본 페이지 이미지. `?highlight=x0,y0,x1,y1`이면 빨간 상자 | 근거 상세 |
| GET | `/chunks/{id}` | 청크 내용 | 근거 상세 |

---

## POST /documents
`multipart/form-data`로 보냅니다.

| 항목 | 필수 | 설명 |
|---|---|---|
| `file` | 필수 | PDF 파일 (50MB 이하) |
| `title` | 선택 | 문서 제목. 비우면 파일 이름 |
| `source_org` | 선택 | 발행 기관 |
| `page_ranges` | 선택 | 사용할 쪽 범위 (예: `1-40`, `13-22,37-54`). 비우면 전체 |

응답 `202`: `{"id": 4, "status": "pending"}`
→ `GET /documents/4`로 `status`가 `done`(완료) 또는 `failed`(실패)가 될 때까지 확인합니다. `progress`에 "차트 캡션 13/20"처럼 진행 상황이 나옵니다.

> 새 문서는 차트마다 Vision LLM을 부르므로 비용이 듭니다. 문서 1개당 새 캡션은 최대 `MAX_CAPTION_PAGES`개(기본 60)로 제한합니다.

## POST /ask
요청:
```json
{"question": "2025년 4분기 음악 산업 매출액은?", "document_ids": [2], "top_k": 5}
```
응답:
```json
{
  "answer": "2025년 4분기 음악 산업 매출액은 4,056,713백만 원입니다. [D02 p.45]",
  "is_unknown": false,
  "latency_ms": 3120,
  "sources": [
    {
      "rank": 1, "chunk_id": 301, "document_id": 2, "doc_code": "D02", "doc_title": "…",
      "page": 45, "kind": "table", "content": "[표] … - 음악: … 2025 년 P 4 분기 = 4,056,713 …",
      "asset_id": null, "vector_rank": 2, "bm25_rank": 1, "rerank_score": 0.97
    }
  ]
}
```
- `is_unknown`: 근거가 부족해 "찾을 수 없음"으로 답했는지
- `vector_rank` / `bm25_rank`: 1차 검색에서 각 방식의 순위 (못 찾았으면 `null`) → 근거 상세 화면의 "왜 뽑혔나"
- `rerank_score`: 리랭커 관련도 (0~1). 1등 점수가 `RELEVANCE_THRESHOLD`보다 낮으면 LLM을 부르지 않고 "찾을 수 없음"
- `asset_id`: 차트 근거면 이미지 번호 → `GET /assets/{asset_id}`

## 오류 응답
| 코드 | 언제 |
|---|---|
| 400 | PDF가 아님, 50MB 초과, 열 수 없는 PDF |
| 404 | 없는 문서·이미지·쪽 번호 |
| 422 | 요청 모양이 틀림 (예: 빈 질문) — FastAPI가 자동으로 검사 |
| 503 | DB에 연결할 수 없음 |
