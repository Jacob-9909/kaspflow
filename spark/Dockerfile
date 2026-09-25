# Spark 컨테이너 이미지
# ------------------------------------------------------------
# 공식 Apache Spark 이미지 사용 (Python 포함 → PySpark 실행 가능).
# 설계서 5절의 Spark 3.5 계열. (구 bitnami/spark 이미지는 2026-08 Bitnami가
# 무료 카탈로그에서 내려 더 이상 pull 되지 않으므로 공식 이미지로 전환했다.)
#
# 공식 이미지 특징:
#   - 비루트 사용자(uid 185, spark)로 동작 → 쓰기는 /tmp 등에.
#   - spark-submit 이 PATH 에 있음, 작업 디렉터리 /opt/spark/work-dir.
FROM apache/spark:3.5.6

# spark-submit 시 자동으로 내려받을 패키지:
#   - spark-sql-kafka : Kafka 소스 커넥터 (Spark <-> Kafka 연결)
#   - postgresql      : JDBC 드라이버 (Spark <-> Postgres 연결)
# 버전은 Spark 3.5(scala 2.12) 기준으로 맞춘다.
ENV SPARK_KAFKA_PKG=org.apache.spark:spark-sql-kafka-0-10_2.12:3.5.6
ENV POSTGRES_JDBC_PKG=org.postgresql:postgresql:42.7.3

# 잡 코드 복사 (비루트 사용자가 읽을 수 있는 경로)
WORKDIR /opt/spark/work-dir
COPY jobs/ /opt/spark/work-dir/jobs/

# spark-submit 실행.
#   --packages 로 위 커넥터/드라이버를 가져온다 (최초 1회 다운로드, 이후 캐시).
#   ivy 캐시/홈을 쓰기 가능한 /tmp 로 지정 (비루트 사용자라 홈이 제한적).
CMD /opt/spark/bin/spark-submit \
    --master "local[*]" \
    --packages "${SPARK_KAFKA_PKG},${POSTGRES_JDBC_PKG}" \
    --conf spark.jars.ivy=/tmp/.ivy2 \
    /opt/spark/work-dir/jobs/ohlc_stream.py
