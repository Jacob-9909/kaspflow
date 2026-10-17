# Signal Worker 컨테이너 이미지
#
# 두 가지 빌드:
#   기본(INSTALL_LAYA=0, M2)  저장 전용. 모델/torch 없음 -> 작고 빠르다.
#   INSTALL_LAYA=1  (M3)      Laya + PyTorch(CPU 전용) 포함. 모델 가중치는 이미지에 넣지 않는다:
#                             HF_HOME(/hf-cache) 을 named volume 으로 마운트해서 재배포마다 다시 받지 않게 한다.
#   docker build --build-arg INSTALL_LAYA=1 -t signal-worker:laya .
FROM python:3.12-slim

ARG INSTALL_LAYA=0
ENV HF_HOME=/hf-cache \
    PYTHONUNBUFFERED=1

WORKDIR /app

# 1) 의존성 먼저 설치 (requirements만 먼저 복사하면 캐시가 잘 재사용됨)
COPY requirements.txt requirements-laya.txt ./
RUN pip install --no-cache-dir -r requirements.txt
# Laya 선택 설치: CPU 전용 torch 를 먼저 깔아 laya 가 CUDA 빌드를 끌어오지 못하게 한다.
RUN if [ "$INSTALL_LAYA" = "1" ]; then \
      pip install --no-cache-dir torch==2.14.1 --index-url https://download.pytorch.org/whl/cpu \
      && pip install --no-cache-dir -r requirements-laya.txt; \
    fi

# 2) 애플리케이션 코드 복사
COPY main.py matching.py classifier.py questions.py store.py label.py symbols.yaml ./

# 3) 실행: Kafka -> Postgres 상주 프로세스
CMD ["python", "-u", "main.py"]
