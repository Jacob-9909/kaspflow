# 배포 메모 (Oracle VM) — CD 진행 중

> CD(실제 배포) 파일들을 이 세션에서 작성했다: `docker-compose.prod.yml`, `infra/kaspflow-autodeploy.*`.
> VM에서 실제 실행(Docker 설치, DB 생성, 방화벽, 배포)은 Jacob이 §4·§7 순서로 진행한다.
> 관련: 루트 `README.md` "CI/CD" 섹션, `.github/workflows/publish-images.yml`.

---

## 1. VM 접속 정보 (기존 midas-touch에서 확인됨)

`~/.ssh/config` 에 이미 등록되어 있어 `ssh oracle_vm` 으로 바로 접속된다.

```
Host oracle_vm db
    HostName 161.33.134.252
    User ubuntu
    IdentityFile ~/.ssh/id_ed25519
```

- 사양: **2 OCPU / 12GB RAM**, Ubuntu 22.04.5 LTS, 디스크 여유 넉넉(~181GB)
- 컨테이너 런타임: **없음 → Docker 새로 설치한다.**
  (2026-09-25 실측: podman/docker 둘 다 미설치. 그래서 자유롭게 Docker 선택.
   우리 GHCR 이미지/compose가 전부 Docker 기준이라 가장 매끄럽다.)
- **PostgreSQL 17 + pgvector: 이미 apt로 설치됨(현재 중지 상태).**
  이걸 네이티브로 그대로 재사용한다(재설치 안 함). kaspflow는 postgres 컨테이너를 안 띄운다.
- Neo4j: **없음** (설치 흔적 없음 → 내릴 것도 없음).
- midas-touch: `~/midas-touch` 디렉터리는 있으나 **이 VM에 실제 배포는 안 된 상태**
  (80/443 방화벽 단계에서 멈춤. bash_history로 확인). 즉 지금 VM은 거의 백지.

---

## 2. 이번 배포 결정 (2026-09-25 실측 기준 갱신)

- **Postgres는 네이티브(기존 PG17) 재사용.** kaspflow는 postgres 컨테이너를 안 띄우고
  호스트 PG17에 붙되, **별도 DB(`crypto`)** 로 분리한다. (Jacob이 "PG 이미 깔려있어서" 선택)
- **나머지(Kafka/Spark/Producer/Backend/Dashboard)는 Docker 컨테이너.** GHCR 이미지 pull.
  Kafka↔Spark 버전 호환이 이미지에 고정돼 있어 네이티브 설치 삽질을 피한다.
- Neo4j: 없음(할 것 없음). midas: 이 VM에 미배포(충돌 걱정 없음).
- 메모리: 현재 available ~8.5Gi + swap 4Gi. Kafka ~1GB + Spark 상한 ~1.5GB +
  producer/backend/nginx ~0.5GB ≈ 3~3.5GB → 여유 충분.

### 집에서 실행 순서 요약 (자세한 명령은 §4, §7)
```bash
# 0) 접속 + 현재 상태 확인
ssh oracle_vm 'free -h; systemctl status postgresql@17-main --no-pager | head -3; docker --version 2>/dev/null || echo "docker 없음"'
```
- Neo4j 백업/내리기: **불필요** (없음).
- Postgres 백업: 기존 PG17에 midas 데이터가 있을 수도 있으니, DB 만지기 전 스냅샷 1회 권장:
  ```bash
  ssh oracle_vm 'sudo systemctl start postgresql@17-main && sudo -u postgres pg_dumpall > /var/backups/pg_all_$(date +%F).sql 2>/dev/null; ls -lh /var/backups/pg_all_* 2>/dev/null || echo "빈 DB거나 백업 생략됨"'
  ```

---

## 3. Docker 설치 (VM에 1회)

```bash
ssh oracle_vm
# 공식 편의 스크립트 (Ubuntu 22.04 지원)
curl -fsSL https://get.docker.com | sudo sh
# ubuntu 사용자가 sudo 없이 docker 쓰게
sudo usermod -aG docker $USER
newgrp docker    # 또는 재로그인
docker --version && docker compose version
```
- Docker의 `restart: unless-stopped` 정책이 재부팅/크래시 복구를 맡는다(별도 systemd 유닛 불필요).
- 자동배포 타이머만 systemd로 둔다(§5).

---

## 4. 호스트 Postgres 공유 방법 (핵심)

kaspflow는 postgres 컨테이너를 **제거**하고 호스트 Postgres에 붙는다.

1. **DB 생성 + 스키마 적용** (호스트에서 1회):
   ```bash
   ssh oracle_vm
   sudo systemctl enable --now postgresql@17-main   # 부팅 시 자동 시작 + 지금 시작
   sudo -u postgres psql -c "CREATE DATABASE crypto;"
   sudo -u postgres psql -c "CREATE USER crypto WITH PASSWORD '<강한암호>';"
   sudo -u postgres psql -c "GRANT ALL PRIVILEGES ON DATABASE crypto TO crypto;"
   # PG16+ 는 public 스키마 권한도 명시 필요할 수 있음
   sudo -u postgres psql -d crypto -c "GRANT ALL ON SCHEMA public TO crypto;"
   # init.sql 적용 (repo clone 후, repo 루트에서)
   sudo -u postgres psql -d crypto -f db/init.sql
   ```
2. **컨테이너 → 호스트 Postgres 접속 주소**:
   - Docker: `host.docker.internal:5432` 사용 (compose에 `extra_hosts: ["host.docker.internal:host-gateway"]` 필요)
   - spark/backend/producer 의 `POSTGRES_HOST` 를 `host.docker.internal` 로 세팅.
3. **호스트 Postgres가 컨테이너 접속을 허용**해야 함:
   - `postgresql.conf`: `listen_addresses = '*'` (또는 docker 브리지 IP) — 방화벽으로 보호
   - `pg_hba.conf`: docker 기본 브리지 대역(`172.16.0.0/12`)에서 `crypto` DB `scram-sha-256`/`md5` 허용
   - 반영: `sudo systemctl reload postgresql@17-main`
4. **주의**: 기존 PG17과 **같은 인스턴스**다. `crypto` DB/유저를 분리하고,
   비밀번호는 VM의 `.env`로만 관리(레포에 커밋 금지).

---

## 5. 배포 방식 (midas 패턴 재사용 권장)

midas-touch는 **VM이 2분마다 main을 polling** 해서 스스로 배포한다
(`~/Develop/midas-touch/infra/vm-autodeploy.sh` + systemd timer).
Actions에서 SSH push가 아니라 **pull**인 이유: VM에 DB가 같이 돌아서
셸 열리는 키를 레포 시크릿에 두기 부담스럽기 때문. kaspflow도 같은 VM/상황이라 동일 논리 적용.

### kaspflow용으로 이식할 것 (이 세션에서 작성함)
- [x] `docker-compose.prod.yml`:
      - postgres 서비스 **제거**(호스트 공유), 나머지는 `image: ghcr.io/<owner>/<repo>-<svc>:latest`
      - spark 에 메모리 상한(`mem_limit` + `SPARK_DRIVER_MEMORY`)
      - `POSTGRES_HOST=host.docker.internal` + `extra_hosts: host-gateway`
- [x] `infra/kaspflow-autodeploy.sh` (midas 스크립트 각색: git fetch/pull --ff-only →
      CI 게이트 → `docker compose -f docker-compose.prod.yml pull` → `up -d` → 헬스체크 → 실패 시 롤백)
- [x] `infra/kaspflow-autodeploy.{service,timer}` (2분 폴링)
- [ ] (Jacob) 방화벽: 대시보드 포트(8501) VCN Security List + iptables 양쪽 open

---

## 6. midas 런북에서 가져온 교훈 (그대로 적용)

- 오라클 방화벽은 **VCN Security List + OS(iptables/firewalld) 양쪽** 열어야 한다.
- systemd `EnvironmentFile` 은 값 뒤 `#` 주석을 안 자른다 → `.env` 에 인라인 주석 금지.
- 메모리 상한(`MemoryMax`) 없으면 OOM 시 커널이 **DB를 골라 죽일 수 있다** →
  kaspflow Spark/Kafka 컨테이너에 상한을 걸어 호스트 Postgres(공유)를 보호한다.
- 헬스체크는 200만 보지 말고 **DB 의존 엔드포인트**까지 확인(`/health` → DB 붙는지).
- 참고 파일: `~/Develop/midas-touch/infra/` (vm-autodeploy.sh, *.timer, *.service),
  `~/Develop/midas-touch/docs/deploy-runbook.md`

---

## 7. "이어서 해줘" 하면 이 순서로

1. (Jacob) GitHub remote 추가 + push → `publish-images` 첫 실행 → GHCR에 이미지 확인
2. (Jacob) `ssh oracle_vm` 로 §2 확인 명령 실행, Neo4j 내리기, 호스트 Postgres에 `crypto` DB 생성
3. (나) §5 목록대로 `docker-compose.prod.yml` + autodeploy 스크립트/유닛 작성
4. (Jacob) VM에서 podman compose 배포 트리거, (나) 로그 보며 디버깅
```
