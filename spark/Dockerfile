# Spark 컨테이너 이미지
# ------------------------------------------------------------
# Bitnami Spark 3.5 이미지를 사용 (설계서 5절: Spark 3.5 LTS + Kafka 3.9 LTS 조합이 안정적).
# PySpark가 포함되어 있어 별도 설치 없이 spark-submit 을 바로 쓸 수 있다.
FROM bitnami/spark:3.5

# spark-submit 시 자동으로 내려받을 패키지:
#   - spark-sql-kafka : Kafka 소스 커넥터 (Spark <-> Kafka 연결)
#   - postgresql      : JDBC 드라이버 (Spark <-> Postgres 연결)
# 버전은 Spark 3.5(scala 2.12) 기준으로 맞춘다.
ENV SPARK_KAFKA_PKG=org.apache.spark:spark-sql-kafka-0-10_2.12:3.5.1
ENV POSTGRES_JDBC_PKG=org.postgresql:postgresql:42.7.3

# 잡 코드 복사
WORKDIR /opt/jobs
COPY jobs/ /opt/jobs/

# spark-submit 실행.
#   --packages 로 위 커넥터/드라이버를 가져온다 (최초 1회 다운로드, 이후 캐시).
#   ivy 캐시를 쓰기 가능한 경로로 지정해야 non-root 사용자에서 오류가 안 난다.
CMD spark-submit \
    --master "local[*]" \
    --packages "${SPARK_KAFKA_PKG},${POSTGRES_JDBC_PKG}" \
    --conf spark.jars.ivy=/tmp/.ivy2 \
    /opt/jobs/ohlc_stream.py
