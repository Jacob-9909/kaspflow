# oracle_vm 셋업 런북 (kaspflow 주식 검증기)

접속: `ssh oracle_vm` (User `ubuntu`, OCI 도쿄 A1.Flex ARM64 2 OCPU · 12GB, Ubuntu 22.04)
구성: compose 2개 컨테이너(backend, dashboard) + 호스트 네이티브 PostgreSQL 17(`crypto` DB) + Caddy(TLS).
컨테이너 런타임: **kiri** (2026-11-16 Docker Engine 에서 전환, 아래 §K). docker CLI·compose 명령은 그대로 쓴다.
접속 주소: https://bit-kaspflow.duckdns.org (Caddy 가 `/api/*` → 127.0.0.1:8010, 나머지 → 127.0.0.1:8501)

---

## A. 1세대(코인 파이프라인)에서 2세대(주식 검증기)로 전환 (2026-11-16)

이미 1세대가 돌던 VM 에서 한 번만 하면 된다. 코드는 `main` push 후 자동배포가 바꾼다.

1. `.env` 에 새 값 추가 (값은 화면에 출력하지 않는다)
   ```bash
   cd ~/kaspflow
   grep -E '^NVIDIA_API_KEY(_2)?=' ~/midas-touch/.env >> .env     # NIM 키 복사
   nano .env                                                       # APP_PASSWORD=<실행 비밀번호> 추가
   echo "SESSION_SECRET=$(openssl rand -hex 32)" >> .env          # 로그인 쿠키 서명 키
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

## K. 컨테이너 런타임 kiri (2026-11-16 전환)

[kiri](https://github.com/libracoder/kiri) 는 Docker Engine API 를 그대로 말하는 Rust 런타임이다. Linux 에서는 VM 없이
호스트 커널에서 돈다. VM 에는 엔진만 있는 `kiri-lite` 를 깔고, docker CLI·compose 플러그인은 Docker 패키지 것을 계속 쓴다.

```bash
# 설치 (arm64, 해시 확인 후)
curl -fsSLO https://github.com/libracoder/kiri/releases/download/v0.2.1/kiri-lite_0.2.1_arm64.deb
echo "76fe00facf4d61aefe90fbb0e44774942b1507e810c4e7b47ea34fddafe0193d  kiri-lite_0.2.1_arm64.deb" | sha256sum -c -
sudo dpkg -i kiri-lite_0.2.1_arm64.deb && sudo usermod -aG kiri ubuntu
# Docker Engine 은 지우지 않고 끄기만 한다 (롤백용, 이미지도 /var/lib/docker 에 남는다)
sudo systemctl disable --now docker.service docker.socket containerd.service
sudo rm -f /run/docker.sock        # dockerd 가 남긴 소켓 파일이 있으면 kiri 가 링크를 만들지 못한다
sudo systemctl enable --now kiri   # /run/docker.sock -> /run/kiri/docker.sock 링크를 만든다
```

`/etc/systemd/system/kiri.service.d/` 의 드롭인 두 개가 필요하다 (VM 에만 있음):
- `ports.conf`: `KIRI_HTTP_PORT=18080`, `KIRI_HTTPS_PORT=18443`. kiri 의 컨테이너 도메인 라우터(127.0.0.1 전용)가 기본으로
  80/443 을 잡으려 해서, 부팅 순서에 따라 Caddy 와 부딪힐 수 있다.
- `forward.conf`: kiri 가 뜰 때 `iptables -I FORWARD -i kiri1 -j ACCEPT` 와 `-o kiri1 RELATED,ESTABLISHED ACCEPT` 를 넣고 멈출 때 뺀다.
  Oracle 이미지의 FORWARD 체인 끝 REJECT 가 컨테이너의 바깥 연결(야후·NIM)을 막기 때문이다. kiri 는 자기 nftables 테이블
  (`ip kiri`)로 NAT·필터를 하지만 같은 훅의 iptables 체인도 통과해야 한다. `/etc/iptables/rules.v4` 는 건드리지 않는다.

kiri 브리지는 `172.19.0.0/16`(kiri1) 이라 기존 `pg_hba.conf` 의 `172.16.0.0/12` 허용 범위 안이다.
`restart: unless-stopped` 컨테이너는 kiri 가 재시작되면 다시 뜬다 (확인함). 자동배포(User=ubuntu, kiri 그룹)는 그대로 동작한다.

롤백 (Docker Engine 으로):
```bash
cd ~/kaspflow && docker compose -f docker-compose.prod.yml down
sudo systemctl disable --now kiri
sudo systemctl enable --now containerd.service docker.socket docker.service
IMAGE_TAG=sha-$(git rev-parse --short=7 HEAD) docker compose -f docker-compose.prod.yml up -d
```

---

## L. 판단 기록 DB 백업 (2026-11-18)

판단 기록(`crypto` DB 의 `stock` 스키마)을 매일 07:30 에 `pg_dump` 로 떠서 `/var/backups/postgres/` 에 14일 보관한다.
`stock.cache` 는 캐시라 구조만 남긴다. 파일 이름은 `kaspflow-stock-YYYYMMDD.dump`(custom 형식).

```bash
# 설치 (저장소 루트에서)
sudo install -m 755 infra/kaspflow-db-backup.sh /usr/local/bin/kaspflow-db-backup
sudo install -d -o postgres -g postgres -m 700 /var/backups/postgres
sudo cp infra/kaspflow-db-backup.service infra/kaspflow-db-backup.timer /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now kaspflow-db-backup.timer
sudo systemctl start kaspflow-db-backup.service && journalctl -u kaspflow-db-backup -n 3 --no-pager   # 한 번 돌려 확인

# 백업 파일 확인 (표 목록)
sudo -u postgres pg_restore --list /var/backups/postgres/kaspflow-stock-YYYYMMDD.dump | grep 'TABLE DATA'

# 복구 (stock 스키마를 백업 시점으로 되돌린다. 그 뒤의 기록은 사라지니 먼저 지금 상태를 한 번 더 백업할 것)
sudo -u postgres pg_restore -d crypto --clean --if-exists -n stock /var/backups/postgres/kaspflow-stock-YYYYMMDD.dump
```

같은 VM 디스크에 있으므로 실수로 지운 기록과 깨진 테이블은 되살리지만, VM 자체를 잃으면 함께 사라진다.
가끔 맥으로 복사해 두려면: `ssh oracle_vm 'sudo cat /var/backups/postgres/kaspflow-stock-YYYYMMDD.dump' > kaspflow-stock.dump`

---

## B. 새 VM 에 처음부터 (1회)

### 1. 컨테이너 런타임
docker CLI·compose 플러그인(Docker apt 저장소의 `docker-ce-cli`, `docker-compose-plugin`)과 kiri 엔진(§K)을 설치한다.
Docker Engine 을 쓰려면 `curl -fsSL https://get.docker.com | sudo sh && sudo usermod -aG docker $USER`.

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
