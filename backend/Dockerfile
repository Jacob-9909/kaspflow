# Backend API 컨테이너 이미지
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .

# uvicorn으로 FastAPI 실행. 0.0.0.0 으로 열어야 컨테이너 밖에서 접속 가능.
EXPOSE 8000
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
