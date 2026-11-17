"""설정 한 곳에서 읽기

모든 모듈은 os.getenv를 직접 부르지 않고 이 파일의 `settings`에서 값을 가져다 씁니다.
값은 .env 파일(없으면 환경 변수)에서 읽고, 선택 항목은 기본값을 씁니다.
항목 설명: docs/env_spec.md
"""
import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

PROJECT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_DIR / ".env")


def _get(name, default=None):
    value = os.getenv(name)
    return value if value not in (None, "") else default


@dataclass(frozen=True)
class Settings:
    # OpenAI
    openai_api_key: str | None = field(default_factory=lambda: _get("OPENAI_API_KEY"))
    llm_model: str = field(default_factory=lambda: _get("LLM_MODEL", "gpt-6-luna"))
    vision_model: str = field(default_factory=lambda: _get("VISION_MODEL", "gpt-6.1-terra"))
    embedding_model: str = field(default_factory=lambda: _get("EMBEDDING_MODEL", "text-embedding-3-small"))
    judge_model: str = field(default_factory=lambda: _get("JUDGE_MODEL", "gpt-6.1-terra"))

    # 경로
    # 상대 경로(./data)는 실행 위치가 아니라 프로젝트 폴더 기준으로 해석 (노트북은 notebooks/에서 실행되므로)
    data_dir: Path = field(default_factory=lambda: (PROJECT_DIR / _get("DATA_DIR", "data")).resolve())

    # 문서 처리
    chunk_size: int = field(default_factory=lambda: int(_get("CHUNK_SIZE", 800)))
    chunk_overlap: int = field(default_factory=lambda: int(_get("CHUNK_OVERLAP", 100)))
    max_caption_pages: int = field(default_factory=lambda: int(_get("MAX_CAPTION_PAGES", 60)))

    # 검색
    retrieve_top_k: int = field(default_factory=lambda: int(_get("RETRIEVE_TOP_K", 20)))
    rerank_top_k: int = field(default_factory=lambda: int(_get("RERANK_TOP_K", 5)))
    reranker_model: str = field(default_factory=lambda: _get("RERANKER_MODEL", "BAAI/bge-reranker-v2-m3"))
    relevance_threshold: float = field(default_factory=lambda: float(_get("RELEVANCE_THRESHOLD", 0.0)))
    use_reranker: bool = field(default_factory=lambda: _get("USE_RERANKER", "true").lower() == "true")

    # 데이터베이스 (로컬 개발용 기본값. 외부에 공개하는 서버에서는 반드시 .env에서 바꿀 것)
    postgres_user: str = field(default_factory=lambda: _get("POSTGRES_USER", "reportqa"))
    postgres_password: str = field(default_factory=lambda: _get("POSTGRES_PASSWORD", "reportqa"))
    postgres_db: str = field(default_factory=lambda: _get("POSTGRES_DB", "reportqa"))
    postgres_host: str = field(default_factory=lambda: _get("POSTGRES_HOST", "localhost"))
    postgres_port: int = field(default_factory=lambda: int(_get("POSTGRES_PORT", 5432)))

    # 화면
    api_base_url: str = field(default_factory=lambda: _get("API_BASE_URL", "http://localhost:8000"))
    log_level: str = field(default_factory=lambda: _get("LOG_LEVEL", "INFO"))

    @property
    def database_url(self) -> str:
        return (f"postgresql://{self.postgres_user}:{self.postgres_password}"
                f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}")

    @property
    def pdf_dir(self) -> Path:
        return self.data_dir / "pdf"

    @property
    def asset_dir(self) -> Path:
        return self.data_dir / "assets"

    @property
    def caption_dir(self) -> Path:
        return self.data_dir / "captions"


settings = Settings()


def data_path(p) -> Path:
    """DB에 저장된 경로(data 폴더 기준 상대 경로)를 실제 파일 경로로. 내 PC와 Docker에서 data 위치가 달라도 동작"""
    p = Path(p)
    return p if p.is_absolute() else settings.data_dir / p


def rel_data_path(p) -> str:
    """실제 파일 경로 → data 폴더 기준 상대 경로 (DB 저장용)"""
    p = Path(p).resolve()
    try:
        return str(p.relative_to(settings.data_dir))
    except ValueError:
        return str(p)
