# ReportQA 실행 이미지 (api·ui 컨테이너가 같은 이미지를 쓰고, 실행 명령만 다름)
FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# 리랭커 계산 엔진(torch)은 CPU 전용판을 먼저 설치 (GPU판은 수 GB라 이미지가 너무 커짐)
RUN pip install torch==2.15.0 --index-url https://download.pytorch.org/whl/cpu

# 라이브러리 목록이 바뀌지 않으면 이 단계는 캐시를 재사용해 다시 빌드할 때 빠름
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app ./app
COPY ui ./ui
COPY eval ./eval

EXPOSE 8000 8501
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
