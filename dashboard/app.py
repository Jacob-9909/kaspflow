"""
Dashboard (Streamlit): 실시간 캔들차트 시각화
============================================================
[이 파일이 하는 일]
  Backend API(/symbols, /ohlc)를 주기적으로 호출해서
  선택한 심볼의 1분봉 캔들차트와 최근 가격을 그린다.

[데이터 흐름에서의 위치]
  ... -> Postgres -> Backend API -> [이 대시보드]
                                     ^^^^^^^^^^^^^

[확인 방법]
  컨테이너가 뜨면 브라우저에서 http://localhost:8501 접속.
  왼쪽 사이드바에서 심볼을 고르면 차트가 그려진다.
  (몇 초마다 자동 새로고침)
"""
import os
import time

import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st

# backend 서비스 주소 (docker-compose environment로 주입)
BACKEND_URL = os.getenv("BACKEND_URL", "http://backend:8000")

# 자동 새로고침 주기(초)
REFRESH_SEC = 5

st.set_page_config(page_title="Crypto Realtime Dashboard", layout="wide")
st.title("📈 실시간 암호화폐 시세 대시보드")
st.caption("Kafka → Spark Structured Streaming → PostgreSQL → FastAPI → Streamlit")


def fetch_symbols() -> list[str]:
    """API에서 사용 가능한 심볼 목록을 가져온다. 실패하면 빈 리스트."""
    try:
        r = requests.get(f"{BACKEND_URL}/symbols", timeout=3)
        r.raise_for_status()
        return r.json().get("symbols", [])
    except Exception:
        return []


def fetch_ohlc(symbol: str, limit: int = 120) -> pd.DataFrame:
    """특정 심볼의 1분봉을 DataFrame으로 가져온다."""
    try:
        r = requests.get(
            f"{BACKEND_URL}/ohlc",
            params={"symbol": symbol, "limit": limit},
            timeout=3,
        )
        r.raise_for_status()
        candles = r.json().get("candles", [])
        if not candles:
            return pd.DataFrame()
        df = pd.DataFrame(candles)
        df["window_start"] = pd.to_datetime(df["window_start"])
        return df
    except Exception as e:
        st.warning(f"데이터를 불러오지 못했습니다: {e}")
        return pd.DataFrame()


# ------------------------------------------------------------
# 사이드바: 심볼 선택 + 새로고침 옵션
# ------------------------------------------------------------
symbols = fetch_symbols()

with st.sidebar:
    st.header("설정")
    if not symbols:
        st.info(
            "아직 데이터가 없습니다.\n\n"
            "producer/spark 컨테이너가 떠서 집계가 쌓일 때까지 "
            "1~2분 기다린 뒤 새로고침 하세요."
        )
    selected = st.selectbox("심볼", options=symbols or ["(데이터 없음)"])
    auto = st.checkbox("자동 새로고침", value=True)
    st.write(f"새로고침 주기: {REFRESH_SEC}초")

# ------------------------------------------------------------
# 메인: 캔들차트 + 지표
# ------------------------------------------------------------
if symbols and selected != "(데이터 없음)":
    df = fetch_ohlc(selected)

    if df.empty:
        st.info("선택한 심볼의 집계 데이터가 아직 없습니다.")
    else:
        # 최신 캔들에서 요약 지표 뽑기
        latest = df.iloc[-1]
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("종가(close)", f"{latest['close']:,.0f}")
        col2.metric("고가(high)", f"{latest['high']:,.0f}")
        col3.metric("저가(low)", f"{latest['low']:,.0f}")
        col4.metric("거래량(volume)", f"{latest['volume']:.4f}")

        # Plotly 캔들스틱 차트
        fig = go.Figure(
            data=[
                go.Candlestick(
                    x=df["window_start"],
                    open=df["open"],
                    high=df["high"],
                    low=df["low"],
                    close=df["close"],
                    name=selected,
                )
            ]
        )
        fig.update_layout(
            xaxis_title="시간(UTC)",
            yaxis_title="가격",
            xaxis_rangeslider_visible=False,
            height=500,
        )
        st.plotly_chart(fig, use_container_width=True)

        # 원본 데이터 표 (접어두기)
        with st.expander("원본 1분봉 데이터 보기"):
            st.dataframe(df, use_container_width=True)

# 자동 새로고침: 잠시 쉬었다가 스크립트를 다시 실행
if auto:
    time.sleep(REFRESH_SEC)
    st.rerun()
