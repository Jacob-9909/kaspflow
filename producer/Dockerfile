# Producer 컨테이너 이미지
# Python 3.12 슬림 이미지 사용 (설계서 5절 기준)
FROM python:3.12-slim

WORKDIR /app

# 1) 의존성 먼저 설치 (requirements만 먼저 복사하면 캐시가 잘 재사용됨)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 2) 애플리케이션 코드 복사
COPY main.py .

# 3) 실행: WebSocket -> Kafka 상주 프로세스
CMD ["python", "-u", "main.py"]
