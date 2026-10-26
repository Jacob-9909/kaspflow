# kaspflow

**English** | [한국어](README.ko.md)

[![Backend CI](https://github.com/Jacob-9909/kaspflow/actions/workflows/ci-backend.yml/badge.svg)](https://github.com/Jacob-9909/kaspflow/actions/workflows/ci-backend.yml)
[![Frontend CI](https://github.com/Jacob-9909/kaspflow/actions/workflows/ci-frontend.yml/badge.svg)](https://github.com/Jacob-9909/kaspflow/actions/workflows/ci-frontend.yml)

A data pipeline that turns the Binance trade stream into 1-minute candles with Kafka and Spark Structured Streaming, and shows them on a live candlestick chart.

- Demo: https://bit-kaspflow.duckdns.org

```
Binance WS ─▶ Producer ─▶ Kafka ─▶ Spark Streaming ─▶ PostgreSQL ─▶ FastAPI ─▶ React
                                   (1-min OHLC aggregation)
```

## Features

- **Real-time ingestion and aggregation**: The symbol is the partition key, so order is kept per symbol. Watermarks and checkpoints handle late events and restarts. Results are upserted by `(symbol, minute)`.
- **Candlestick chart**: Draws 1-minute to 1-day candles with `lightweight-charts`.
- **Backtesting**: Shows SMA, MACD, RSI, Bollinger Bands, and OBV signals on the chart and calculates returns and trade history.
- **News signal (optional)**: Collects crypto media RSS feeds, turns them into per-symbol sentiment scores, and feeds them into backtests. [Design doc (Korean)](docs/news-signal-design.md)

> Backtest results do not include slippage and are not investment advice.

## Tech Stack

- Streaming: Apache Kafka, Spark 3.5 Structured Streaming
- Backend: Python, FastAPI, PostgreSQL
- Frontend: React, Vite, TypeScript, lightweight-charts
- Infra: Docker Compose, GitHub Actions, GHCR, Oracle Cloud VM, Caddy

## Getting Started

Requires Docker and Docker Compose.

```bash
cp .env.example .env
docker compose up --build
```

After 1–2 minutes, open the services below. The first Spark run takes longer because it downloads connectors.

| Service | URL |
|---|---|
| Dashboard | http://localhost:8501 |
| API docs | http://localhost:8000/docs |
| Kafka UI | http://localhost:8080 |

Change the collected symbols with `SYMBOLS` in `.env`. Stop with `docker compose down`, or `docker compose down -v` to also reset the database.

## Project Structure

```
producer/         Binance WebSocket → Kafka
spark/            Kafka → 1-min aggregation → PostgreSQL
db/               Table schema
backend/          REST API, backtest engine
dashboard/        React dashboard
news-collector/   RSS → Kafka
signal-worker/    News symbol matching and sentiment classification
infra/            Caddy, deploy scripts
```

New to the code? Read `docker-compose.yml` → `producer/main.py` → `spark/jobs/ohlc_stream.py` in that order.

## Tests

```bash
cd backend && python -m unittest discover -s tests
```

## Docs (Korean)

- [Design and decisions](docs/design.md)
- [Architecture](docs/architecture.md)
- [VM deployment](infra/vm-setup.md)
