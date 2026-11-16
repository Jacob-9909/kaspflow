# kaspflow 주식 검증기 API 이미지 (arm64 Oracle VM + amd64 로컬 공용)
FROM python:3.12-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 소스는 와일드카드로 복사한다. 파일 이름을 나열하면 새 모듈을 빼먹는다 (2026-10-17 장애: news_features 누락).
COPY *.py schema.sql ./

EXPOSE 8000
# 1 워커: 분석 작업·로그인 제한 카운터가 프로세스 메모리에 있다 (여러 워커로 늘리려면 이 둘을 DB 로 옮길 것)
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
