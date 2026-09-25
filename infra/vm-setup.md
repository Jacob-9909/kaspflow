# Oracle VM 셋업 런북 (kaspflow 최초 배포)

> 이 문서는 **처음 1회** VM을 세팅하는 순서다. 위에서 아래로 그대로 따라가면 된다.
> 실제 VM 상태(2026-09-25 실측): Docker 없음, PostgreSQL 17 설치됨(중지), Neo4j 없음.
> 배경/결정은 `../docs/deploy-notes.md` 참고.

접속: `ssh oracle_vm`  (User `ubuntu`, 161.33.134.252)

---

## 0. GitHub push (맥에서, 1회)

VM이 폴링할 원격 저장소가 있어야 한다. 아직 remote가 없다면:
```bash
# 맥의 repo 루트(crypto-realtime-dashboard 상위? 아래 주의)에서
# 주의: git repo 루트는 crypto-realtime-dashboard/ 이다.
cd ~/Develop/kaspflow/crypto-realtime-dashboard
gh repo create kaspflow --public --source=. --remote=origin --push
# 또는 이미 만든 repo가 있으면:
#   git remote add origin https://github.com/<owner>/kaspflow.git
#   git push -u origin main
```
- push 후 GitHub → Settings → Actions → General → **Workflow permissions = Read and write** 확인
  (GHCR push 권한). 그래야 `publish-images` 가 이미지를 올린다.
- Actions 탭에서 `Publish Images` 가 성공하고, repo → Packages 에 4개 이미지가 보이는지 확인.

---

## 1. Docker 설치 (VM)

```bash
ssh oracle_vm
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER && newgrp docker
docker --version && docker compose version
```

---

## 2. 네이티브 PostgreSQL 17 준비 (VM)

```bash
# 시작 + 부팅 시 자동 시작
sudo systemctl enable --now postgresql@17-main
psql --version && sudo ss -ltnp | grep 5432    # 5432 리슨 확인

# (안전) 기존 데이터 스냅샷 — midas 데이터가 있을 수도 있으니 1회
sudo -u postgres pg_dumpall > /var/backups/pg_all_$(date +%F).sql 2>/dev/null; \
  ls -lh /var/backups/pg_all_* 2>/dev/null || echo "빈 DB거나 백업 생략"

# kaspflow 전용 DB/유저 (비밀번호는 강하게)
sudo -u postgres psql <<'SQL'
CREATE DATABASE crypto;
CREATE USER crypto WITH PASSWORD 'CHANGE_ME_STRONG';
GRANT ALL PRIVILEGES ON DATABASE crypto TO crypto;
\c crypto
GRANT ALL ON SCHEMA public TO crypto;
SQL
```

### 컨테이너가 호스트 PG에 붙게 허용
Docker 컨테이너는 기본 브리지(172.16.0.0/12)에서 온다. 두 파일을 고친다.
```bash
# 설정 파일 경로 확인
sudo -u postgres psql -tA -c "SHOW config_file;"    # 보통 /etc/postgresql/17/main/postgresql.conf
PGCONF=/etc/postgresql/17/main
```
- `postgresql.conf`: `listen_addresses` 를 열어준다.
  ```bash
  sudo sed -i "s/^#\?listen_addresses.*/listen_addresses = '*'/" $PGCONF/postgresql.conf
  ```
  (외부에는 방화벽으로 5432를 절대 열지 않으므로 `*` 라도 인터넷 노출은 아니다. §4 참고)
- `pg_hba.conf`: docker 브리지 대역에서 `crypto` DB 접속 허용(맨 아래 추가).
  ```bash
  echo "host    crypto    crypto    172.16.0.0/12    scram-sha-256" | sudo tee -a $PGCONF/pg_hba.conf
  ```
- 반영:
  ```bash
  sudo systemctl reload postgresql@17-main
  ```

---

## 3. repo clone + .env + 스키마 (VM)

```bash
cd ~ && git clone https://github.com/<owner>/kaspflow.git
cd kaspflow/crypto-realtime-dashboard

# init.sql 을 호스트 PG의 crypto DB에 적용 (컨테이너 PG가 아니므로 수동)
sudo -u postgres psql -d crypto -f db/init.sql
sudo -u postgres psql -d crypto -c "\dt"    # ohlc_1m, price_alert 보이면 성공

# .env 작성 (프로덕션)
cp .env.prod.example .env
nano .env
#   IMAGE_PREFIX=ghcr.io/<owner>/kaspflow   (소문자!)
#   IMAGE_TAG=latest
#   POSTGRES_HOST=host.docker.internal
#   POSTGRES_PASSWORD=<2번에서 정한 그 비번>
chmod 600 .env
```

### GHCR 로그인 (이미지가 private이면)
```bash
# GitHub PAT (read:packages 스코프) 를 만들어서:
echo <GHCR_PAT> | docker login ghcr.io -u <github-user> --password-stdin
# 또는 repo → Packages 에서 각 이미지를 public 으로 바꾸면 로그인 불필요
```

---

## 4. 방화벽 — 대시보드 포트(8501) 열기

> 오라클은 **두 군데** 다 열어야 한다. 하나만 열면 안 된다. (midas 런북 교훈)

**① OCI 콘솔 (VCN Security List)**
- Networking → VCN → 해당 Security List → Ingress Rules 추가:
  - Source `0.0.0.0/0`, Protocol TCP, Dest port **8501** (Kafka UI도 볼 거면 8080)

**② VM 안 iptables** — Oracle Ubuntu는 INPUT 체인 끝에 REJECT가 있어 **`-I INPUT 1`(앞에 삽입)** 해야 한다. `-A`(뒤 추가)는 REJECT에 먼저 걸려 무용지물.
```bash
sudo iptables -I INPUT 1 -m state --state NEW -p tcp --dport 8501 -j ACCEPT
# (선택) Kafka UI 도 외부에서 보려면:
# sudo iptables -I INPUT 1 -m state --state NEW -p tcp --dport 8080 -j ACCEPT
# 재부팅 후에도 유지
sudo apt-get install -y iptables-persistent
sudo netfilter-persistent save
```
> ⚠️ **5432(Postgres)는 절대 외부로 열지 말 것.** 컨테이너는 내부 브리지로만 붙는다.

---

## 5. 최초 배포 (VM)

```bash
cd ~/kaspflow/crypto-realtime-dashboard
docker compose -f docker-compose.prod.yml pull
docker compose -f docker-compose.prod.yml up -d
docker compose -f docker-compose.prod.yml ps
```
확인:
```bash
curl -s http://127.0.0.1:8000/health           # {"status":"ok"} 면 DB까지 OK
docker compose -f docker-compose.prod.yml logs -f spark   # [batch N] ... rows upsert
```
브라우저: `http://161.33.134.252:8501` (대시보드)

---

## 6. 자동배포 켜기 (VM, 선택)

main 에 push → publish-images 성공 → VM이 2분 폴링으로 스스로 pull&배포.
```bash
cd ~/kaspflow/crypto-realtime-dashboard/infra
# CI 게이트를 켜려면 service 파일의 GH_REPO 를 채운다 (예: Jacob-9909/kaspflow)
sudo nano kaspflow-autodeploy.service     #   Environment=GH_REPO=<owner>/kaspflow
sudo cp kaspflow-autodeploy.service /etc/systemd/system/
sudo cp kaspflow-autodeploy.timer   /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now kaspflow-autodeploy.timer

# 확인
systemctl status kaspflow-autodeploy.timer
journalctl -u kaspflow-autodeploy -f       # 배포 로그
sudo systemctl start kaspflow-autodeploy.service   # 지금 즉시 1회 실행
```
끄기: `sudo systemctl disable --now kaspflow-autodeploy.timer`

---

## 7. 트러블슈팅

- **컨테이너가 DB에 못 붙음** (`/health` 503): §2의 `pg_hba.conf`/`listen_addresses`
  반영(`reload`)과 `.env` 의 `POSTGRES_PASSWORD` 확인. 컨테이너 안에서 테스트:
  `docker compose -f docker-compose.prod.yml exec backend python -c "import psycopg,os;psycopg.connect(host='host.docker.internal',dbname='crypto',user='crypto',password=os.environ['POSTGRES_PASSWORD']);print('ok')"`
- **대시보드 접속 안 됨**: §4 방화벽 양쪽(VCN + iptables) 확인. `curl -m5 http://161.33.134.252:8501` 로 외부 도달 테스트.
- **이미지 pull 실패(unauthorized)**: GHCR private → §3의 `docker login ghcr.io`, 또는 패키지 public 전환.
- **Spark OOM/느림**: `docker stats` 로 메모리 확인. `docker-compose.prod.yml` 의 spark `mem_limit` 조정.
- **자동배포가 안 돎**: `journalctl -u kaspflow-autodeploy -n 50`. CI 게이트에 걸렸으면 `publish-images` 성공 여부 확인, 또는 service에서 `GH_REPO` 비우면 게이트 skip.
