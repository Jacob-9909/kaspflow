# kaspflow

**English** | [한국어](README.ko.md)

[![Backend CI](https://github.com/Jacob-9909/kaspflow/actions/workflows/ci-backend.yml/badge.svg)](https://github.com/Jacob-9909/kaspflow/actions/workflows/ci-backend.yml)
[![Frontend CI](https://github.com/Jacob-9909/kaspflow/actions/workflows/ci-frontend.yml/badge.svg)](https://github.com/Jacob-9909/kaspflow/actions/workflows/ci-frontend.yml)

A buy / hold / sell validator for US stocks. Pick a ticker, press one button, and the backend runs technical analysis, statistically validated strategy backtests, Monte Carlo simulation, valuation, news sentiment and six investor-philosophy checklists, then gives a verdict for each horizon (1 month, 3 months, 1 year).

- Demo: https://bit-kaspflow.duckdns.org (viewing results is public; running an analysis needs a password)

```
Yahoo Finance ─┐                                  ┌─ verdict per horizon (deterministic)
Google News  ──┼─▶ FastAPI pipeline (8 steps) ────┤
NVIDIA NIM   ──┘     PostgreSQL cache + history   └─ React dashboard (SSE progress, charts)
```

## What it checks

- **Validated strategy signals**: 11 strategies (6 ported from midas-touch, plus Turtle breakout, time-series momentum, 10-month trend filter, RSI(2), volatility breakout), 30 parameter variants. Walk-forward out-of-sample results, a Monte Carlo permutation test for timing skill, and the Deflated Sharpe Ratio. Only strategies that pass vote.
- **Simulation**: 2,000 block-bootstrap price paths with a CAPM drift, stop/target hit probabilities, position sizing, historical analogs.
- **Valuation and quality**: FCFF DCF with a WACC × terminal-growth sensitivity grid, reverse DCF, PEG, Graham, EPV, Piotroski F, Altman Z, analyst consensus and estimate revisions.
- **News and investor panel**: NIM labels recent headlines; Buffett, Graham, Lynch, Munger, Wood and Druckenmiller checklists plus LLM opinions.
- **Track record**: every run is stored and scored after its horizon ends.

The verdict is computed by code and is reproducible. The LLM explains it but cannot change it. Details: [docs/methodology.md](docs/methodology.md) (Korean).

> Not investment advice. Backtests and simulations do not guarantee future returns.

## Tech stack

- Backend: Python 3.12, FastAPI, pandas/numpy, yfinance, psycopg 3, PostgreSQL 17
- LLM: NVIDIA NIM (OpenAI-compatible, structured JSON output)
- Frontend: React, Vite, TypeScript, TradingView lightweight-charts, Pretendard
- Infra: Docker Compose on the [kiri](https://github.com/libracoder/kiri) runtime (Mac and the VM), GitHub Actions, GHCR (amd64 + arm64), Oracle Cloud ARM VM, Caddy

## Getting started

```bash
cp .env.example .env          # add NVIDIA_API_KEY (optional) and APP_PASSWORD
docker compose up --build     # http://localhost:8501
```

Tests: `cd backend && python -m unittest discover -s tests` (set `TEST_DATABASE_URL` to also run the PostgreSQL tests).

## Docs

- [Architecture](docs/architecture.md) · [Methodology](docs/methodology.md) · [VM runbook](infra/vm-setup.md) (Korean)
