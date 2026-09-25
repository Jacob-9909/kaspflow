# Dashboard(Streamlit) 컨테이너 이미지
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .

# Streamlit 실행. 0.0.0.0 + 8501 포트로 열어야 컨테이너 밖에서 접속 가능.
EXPOSE 8501
CMD ["streamlit", "run", "app.py", \
     "--server.address=0.0.0.0", \
     "--server.port=8501", \
     "--server.headless=true"]
