# oracle_vm 셋업 런북 (kaspflow 주식 검증기)

접속: `ssh oracle_vm` (User `ubuntu`, OCI 도쿄 A1.Flex ARM64 2 OCPU · 12GB, Ubuntu 22.04)
구성: Docker compose 2개 컨테이너(backend, dashboard) + 호스트 네이티브 PostgreSQL 17(`crypto` DB) + Caddy(TLS).
접속 주소: https://bit-kaspflow.duckdns.org (Caddy 가 `/api/*` → 127.0.0.1:8010, 나머지 → 127.0.0.1:8501)

---

## A. 1세대(코인 파이프라인)에서 2세대(주식 검증기)로 전환 (2026-11-16)

이미 1세대가 돌던 VM 에서 한 번만 하면 된다. 코드는 `main` push 후 자동배포가 바꾼다.

1. `.env` 에 새 값 추가 (값은 화면에 출력하지 않는다)
   ```bash
   cd ~/kaspflow
   grep -E '^NVIDIA_API_KEY(_2)?=' ~/midas-touch/.env >> .env     # NIM 키 복사
   nano .env                                                       # APP_PASSWORD=<실행 비밀번호> 추가
   chmod 600 .env
   ```
   1세대 전용 값(KAFKA_TOPIC, SYMBOLS, LAYA_ENABLED, SIGNAL_WORKER_MEM_LIMIT)은 남아 있어도 무시된다.
2. 자동배포가 새 커밋을 띄운다. 첫 배포는 이미 메모리에 올라간 옛 스크립트로 돌아서 옛 컨테이너가
   남을 수 있다. 그때는 손으로 한 번 정리한다 (다음 배포부터는 `--remove-orphans` 가 처리):
   ```bash
   docker compose -f docker-compose.prod.yml up -d --remove-orphans
   docker ps --format '{{.Names}}\t{{.Status}}'     # backend, dashboard 두 개만 남아야 한다
   ```
3. 확인: `curl -s http://127.0.0.1:8010/health` 가 `"status":"ok"` 와 `"llm":true` 를 돌려주면 끝.
4. 1세대 테이블(`public.ohlc_1m`, `price_alert`, `news_raw`, `news_signal`, `news_label`, `news_eval`)과
   `kaspflow_hf-cache` 볼륨(Laya 모델 808MB)은 지우지 않았다. 필요 없으면 직접 정리한다.

---

## B. 새 VM 에 처음부터 (1회)

### 1. Docker
```bash
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER && newgrp docker
```

### 2. PostgreSQL 17 (호스트 네이티브)
```bash
sudo systemctl enable --now postgresql@17-main
sudo -u postgres psql <<'SQL'
CREATE DATABASE crypto;
CREATE USER crypto WITH PASSWORD 'CHANGE_ME_STRONG';
GRANT ALL PRIVILEGES ON DATABASE crypto TO crypto;
\c crypto
GRANT ALL ON SCHEMA public TO crypto;
SQL
PGCONF=/etc/postgresql/17/main
sudo sed -i "s/^#\?listen_addresses.*/listen_addresses = '*'/" $PGCONF/postgresql.conf
echo "host    crypto    crypto    172.16.0.0/12    scram-sha-256" | sudo tee -a $PGCONF/pg_hba.conf
sudo systemctl reload postgresql@17-main
```
앱 테이블(`stock.cache`, `stock.analysis_run`)은 backend 가 기동할 때 `schema.sql` 로 스스로 만든다
(`crypto` 역할이 DB 에 CREATE 권한이 있어야 한다. 위 GRANT ALL PRIVILEGES ON DATABASE 가 그 권한이다).
**5432 는 외부에 열지 않는다.** 컨테이너는 도커 브리지로만 붙는다.

### 3. 코드 + .env
```bash
cd ~ && git clone https://github.com/Jacob-9909/kaspflow.git && cd kaspflow
cp .env.prod.example .env && nano .env && chmod 600 .env
```
GHCR 패키지가 private 이면 `echo <PAT(read:packages)> | docker login ghcr.io -u <github-user> --password-stdin`.

### 4. Caddy (TLS)
`infra/Caddyfile` 의 사이트 블록을 `/etc/caddy/Caddyfile` 에 붙이고 `sudo systemctl reload caddy`.
80/443 은 OCI VCN Security List 와 VM iptables **양쪽** 다 열려 있어야 한다 (iptables 는 `-I INPUT 1` 로 앞에 넣기).
8501·8010 은 compose 가 127.0.0.1 에만 열기 때문에 바깥에서 닿지 않는다.

### 5. 최초 기동 + 자동배포
```bash
docker compose -f docker-compose.prod.yml pull && docker compose -f docker-compose.prod.yml up -d
sudo cp infra/kaspflow-autodeploy.service infra/kaspflow-autodeploy.timer /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now kaspflow-autodeploy.timer
journalctl -u kaspflow-autodeploy -f
```
자동배포 흐름: `main` push → GitHub Actions `publish-images`(테스트 → amd64/arm64 이미지 → GHCR) →
VM 타이머가 2분마다 `origin/main` 확인 → `build-and-push` 체크가 전부 성공한 커밋만 `git pull` →
그 커밋의 `:sha-xxxxxxx` 이미지로 `up -d --remove-orphans` → `/health` 가 120초 안에 ok 가 아니면 직전 커밋으로 롤백.

---

## C. 운영 메모 (1세대 런북에서 가져온 교훈)

- systemd `EnvironmentFile` 과 compose `.env` 는 값 뒤 `# 주석`을 값으로 읽을 수 있다. 주석은 줄 단위로만.
- compose 파일을 VM 에서 직접 고치지 않는다. 롤백(`git reset --hard`)이 지운다. 바꿀 값은 `.env` 로.
- 컨테이너에는 `mem_limit` 을 둔다. 호스트가 OOM 이면 커널이 PostgreSQL 을 죽일 수 있다 (backend 1g, dashboard 128m).
- 헬스체크는 DB 까지 본다. `/health` 는 `stock.analysis_run` 조회가 돼야 `"status":"ok"` 다.
- backend 는 1 워커다. 분석 동시 실행 수(`MAX_CONCURRENT_RUNS`, 기본 2)와 로그인 시도 제한이 프로세스 메모리에 있다.

## D. 트러블슈팅

- `/health` 503: `pg_hba.conf`/`listen_addresses` 반영, `.env` 의 `POSTGRES_PASSWORD`. 컨테이너 안에서
  `docker compose -f docker-compose.prod.yml exec backend python -c "import db; print(db.DB().ping())"`.
- `"llm":false`: `.env` 에 `NVIDIA_API_KEY` 가 없다. 넣고 `docker compose -f docker-compose.prod.yml up -d`.
- 실행 버튼이 503: `APP_PASSWORD` 가 비어 있다.
- 분석이 '데이터를 찾지 못했습니다': 야후 일시 차단일 수 있다. 몇 분 뒤 다시. 캐시(`stock.cache`)가 있으면 그걸 쓴다.
- 자동배포가 멈춤: `journalctl -u kaspflow-autodeploy -n 50`. 테스트 실패면 `build-and-push` 가 skipped 라 배포하지 않는다.
