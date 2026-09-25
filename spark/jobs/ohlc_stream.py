"""
Spark Structured Streaming: Kafka 체결 스트림 -> 1분봉(OHLC) 집계 -> PostgreSQL
============================================================
[이 파일이 하는 일]
  1. Kafka `crypto-trades` 토픽을 구독한다 (스트리밍 소스).
  2. JSON 메시지를 파싱한다.
  3. 심볼별 1분 텀블링 윈도우로 OHLC(시/고/저/종) + 거래량 + 건수를 집계한다.
  4. micro-batch 마다 결과를 PostgreSQL 테이블(ohlc_1m)에 upsert 한다.

[데이터 흐름에서의 위치]
  거래소 WS -> Producer -> Kafka -> [이 Spark 잡] -> Postgres -> API -> 대시보드
                                       ^^^^^^^^^^^^

[핵심 스트리밍 개념]  (설계서 4.2 절)
  - Watermark   : 늦게 도착한 이벤트를 어디까지 받아줄지 (여기선 2분).
  - Window      : 1분 텀블링 윈도우로 집계.
  - Output Mode : 집계 결과라 "update" 모드 사용.
  - Checkpoint  : 장애 시 마지막 offset부터 재시작 -> 정확성 보장.
  - foreachBatch: micro-batch(작은 DataFrame)를 받아 JDBC로 DB에 쓴다.

[읽는 순서 추천]
  main() -> read_from_kafka() -> parse_trades() -> aggregate_ohlc() -> write_to_postgres()
"""
import os

from pyspark.sql import SparkSession, DataFrame
from pyspark.sql.functions import from_json, col, window
from pyspark.sql.functions import first, last, max as smax, min as smin, sum as ssum, count
from pyspark.sql.types import StructType, StructField, StringType, DoubleType, TimestampType

# ------------------------------------------------------------
# 설정 (docker-compose environment 로 주입)
# ------------------------------------------------------------
KAFKA_BOOTSTRAP_SERVERS = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "kafka:9092")
KAFKA_TOPIC = os.getenv("KAFKA_TOPIC", "crypto-trades")

POSTGRES_HOST = os.getenv("POSTGRES_HOST", "postgres")
POSTGRES_PORT = os.getenv("POSTGRES_PORT", "5432")
POSTGRES_DB = os.getenv("POSTGRES_DB", "crypto")
POSTGRES_USER = os.getenv("POSTGRES_USER", "crypto")
POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD", "crypto")

JDBC_URL = f"jdbc:postgresql://{POSTGRES_HOST}:{POSTGRES_PORT}/{POSTGRES_DB}"

# 체크포인트 저장 위치 (컨테이너 내부 경로). 여기 offset/state가 저장된다.
CHECKPOINT_DIR = "/tmp/checkpoint/ohlc_1m"

# ------------------------------------------------------------
# Producer가 보내는 JSON 스키마 (producer/main.py 의 event 딕셔너리와 일치)
# ------------------------------------------------------------
TRADE_SCHEMA = StructType([
    StructField("symbol", StringType()),
    StructField("price", DoubleType()),
    StructField("volume", DoubleType()),
    StructField("side", StringType()),
    StructField("trade_ts", TimestampType()),   # ISO8601 문자열 -> 자동 파싱
    StructField("source", StringType()),
])


def build_spark() -> SparkSession:
    """SparkSession 생성. Kafka 커넥터/JDBC 드라이버는 Dockerfile에서 미리 넣어둔다."""
    return (
        SparkSession.builder
        .appName("crypto-ohlc-stream")
        # 셔플 파티션 수를 낮춰 로컬 단일 노드에서 오버헤드를 줄인다
        .config("spark.sql.shuffle.partitions", "4")
        .getOrCreate()
    )


def read_from_kafka(spark: SparkSession) -> DataFrame:
    """Kafka 토픽을 스트리밍 소스로 읽는다.

    startingOffsets=latest : 잡을 켠 시점 이후의 새 메시지만 읽는다.
    (과거 데이터부터 다 읽고 싶으면 earliest)
    """
    return (
        spark.readStream
        .format("kafka")
        .option("kafka.bootstrap.servers", KAFKA_BOOTSTRAP_SERVERS)
        .option("subscribe", KAFKA_TOPIC)
        .option("startingOffsets", "latest")
        .load()
    )


def parse_trades(raw: DataFrame) -> DataFrame:
    """Kafka 레코드의 value(바이너리)를 JSON으로 파싱해 평평한 컬럼으로 편다."""
    return (
        raw
        # value는 binary -> string -> from_json 으로 구조체 변환
        .select(from_json(col("value").cast("string"), TRADE_SCHEMA).alias("t"))
        .select("t.*")
    )


def aggregate_ohlc(trades: DataFrame) -> DataFrame:
    """심볼별 1분 윈도우 OHLC 집계.

    withWatermark: trade_ts 기준 2분까지 늦은 데이터를 허용(그 이후는 버림).
    window(...,'1 minute'): 1분 텀블링 윈도우.
    first/last 는 시가/종가, max/min 은 고가/저가.
    """
    return (
        trades
        .withWatermark("trade_ts", "2 minutes")
        .groupBy(window(col("trade_ts"), "1 minute"), col("symbol"))
        .agg(
            first("price", ignorenulls=True).alias("open"),
            smax("price").alias("high"),
            smin("price").alias("low"),
            last("price", ignorenulls=True).alias("close"),
            ssum("volume").alias("volume"),
            count("*").alias("trade_count"),
        )
        # window 구조체(start,end)에서 start만 꺼내 저장용 컬럼으로 정리
        .select(
            col("symbol"),
            col("window.start").alias("window_start"),
            col("open"), col("high"), col("low"), col("close"),
            col("volume"), col("trade_count"),
        )
    )


def write_to_postgres(batch_df: DataFrame, batch_id: int) -> None:
    """foreachBatch 콜백: micro-batch 하나(=일반 DataFrame)를 Postgres에 upsert.

    Structured Streaming의 writeStream은 JDBC를 직접 지원하지 않으므로,
    foreachBatch 안에서 일반 배치 write처럼 다룬다.

    [upsert 전략]
      Spark JDBC write에는 기본 upsert가 없어서:
        1) 스테이징 임시 테이블(ohlc_1m_stg_<batch_id>)에 append 저장
        2) INSERT ... ON CONFLICT ... DO UPDATE 로 본 테이블에 병합
      이렇게 하면 같은 (symbol, window_start)가 다시 와도 최신값으로 갱신된다.
    """
    if batch_df.isEmpty():
        return

    staging = f"ohlc_1m_stg_{batch_id}"

    # 1) 스테이징 테이블에 이번 배치 결과를 통째로 저장 (있으면 덮어씀)
    (
        batch_df.write
        .format("jdbc")
        .option("url", JDBC_URL)
        .option("dbtable", staging)
        .option("user", POSTGRES_USER)
        .option("password", POSTGRES_PASSWORD)
        .option("driver", "org.postgresql.Driver")
        .mode("overwrite")
        .save()
    )

    # 2) 스테이징 -> 본 테이블 병합 후 스테이징 제거.
    #    JDBC 커넥션을 열어 순수 SQL을 실행한다 (py4j로 JVM DriverManager 사용).
    jvm = batch_df.sparkSession._jvm
    conn = jvm.java.sql.DriverManager.getConnection(
        JDBC_URL, POSTGRES_USER, POSTGRES_PASSWORD
    )
    try:
        stmt = conn.createStatement()
        stmt.execute(
            f"""
            INSERT INTO ohlc_1m
                (symbol, window_start, open, high, low, close, volume, trade_count)
            SELECT symbol, window_start, open, high, low, close, volume, trade_count
            FROM {staging}
            ON CONFLICT (symbol, window_start) DO UPDATE SET
                open        = EXCLUDED.open,
                high        = EXCLUDED.high,
                low         = EXCLUDED.low,
                close       = EXCLUDED.close,
                volume      = EXCLUDED.volume,
                trade_count = EXCLUDED.trade_count;
            """
        )
        stmt.execute(f"DROP TABLE IF EXISTS {staging};")
        stmt.close()
    finally:
        conn.close()

    print(f"[batch {batch_id}] {batch_df.count()} rows upsert 완료")


def main() -> None:
    spark = build_spark()
    spark.sparkContext.setLogLevel("WARN")  # 로그 소음 줄이기

    raw = read_from_kafka(spark)
    trades = parse_trades(raw)
    ohlc = aggregate_ohlc(trades)

    query = (
        ohlc.writeStream
        .outputMode("update")                       # 집계 결과 -> update 모드
        .option("checkpointLocation", CHECKPOINT_DIR)
        .foreachBatch(write_to_postgres)            # 배치마다 Postgres로
        .trigger(processingTime="5 seconds")        # 5초마다 micro-batch 실행
        .start()
    )

    print("[Spark] 스트리밍 시작. Kafka 구독 중...")
    query.awaitTermination()


if __name__ == "__main__":
    main()
