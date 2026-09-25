# 배포 메모 (Oracle VM) — 다음 세션에서 이어서

> 이 파일은 "집에 가서 oracle_vm에 이어서 배포" 할 때 바로 쓰려고 남긴 메모다.
> CD(실제 배포)는 아직 안 만들었고, 지금은 **GHCR 이미지 push까지(B단계)** 완료 상태.
> 관련: 루트 `README.md` 의 "CI/CD" 섹션, `.github/workflows/publish-images.yml`.

---

## 1. VM 접속 정보 (기존 midas-touch에서 확인됨)

`~/.ssh/config` 에 이미 등록되어 있어 `ssh oracle_vm` 으로 바로 접속된다.

```
Host oracle_vm db
    HostName 161.33.134.252
    User ubuntu
    IdentityFile ~/.ssh/id_ed25519
```

- 사양: **2 OCPU / 12GB RAM**, Ubuntu 22.04, 디스크 여유 넉넉(~181GB)
- 컨테이너 런타임: **Podman** (Docker 아님 — 아래 주의점 참고)
- 기존 운영: midas-touch (백엔드 uv+systemd, Postgres/Neo4j 호스트 systemd)

---

## 2. 이번 배포 결정 (2026-09-25)

- **Neo4j 내린다** → 메모리 회수 (midas가 쓰던 ~3.2GB 중 상당분).
- **Postgres 1개를 공유한다** → kaspflow는 자체 postgres 컨테이너를 띄우지 않고
  호스트의 기존 Postgres에 붙되, **별도 DB(`crypto`)** 로 분리해 midas와 섞이지 않게 한다.
- 이러면 한 VM에서 midas + kaspflow 동시 운영이 메모리상 현실적이다
  (Kafka ~1GB + Spark 상한 ~1.5GB + producer/backend/nginx ~0.5GB ≈ 3~3.5GB).

### 집에 가서 가장 먼저 확인할 것
```bash
ssh oracle_vm 'podman --version; free -h; systemctl is-active postgresql neo4j'
```
- Neo4j가 active면 내린다: `sudo systemctl disable --now neo4j` (메모리 회수 확인: free -h)
- 호스트 Postgres 버전/포트 확인: `ssh oracle_vm 'psql -V; sudo ss -ltnp | grep 5432'`

---

## 3. Podman 주의점 (Docker와 다른 부분)

1. **compose 실행**: `docker compose` → `podman compose` (또는 `podman-compose`).
   우리 compose 파일은 대체로 호환되나 실행 커맨드가 다르다. 먼저 지원 여부 확인:
   `ssh oracle_vm 'podman compose version || podman-compose --version'`
2. **GHCR pull**: 이미지가 private이면 로그인 필요.
   `echo <GHCR_PAT> | podman login ghcr.io -u <github-user> --password-stdin`
   (또는 GHCR에서 kaspflow 패키지들을 public으로 전환)
3. **자동 재시작**: Podman은 데몬이 없어 `restart: unless-stopped` 가 Docker처럼
   안 먹는다. 재부팅/크래시 복구는 **`podman generate systemd`** 또는 **Quadlet(.container)**
   로 systemd 유닛을 만들어야 한다. ← midas의 systemd 패턴과 동일 철학.

---

## 4. 호스트 Postgres 공유 방법 (핵심)

kaspflow는 postgres 컨테이너를 **제거**하고 호스트 Postgres에 붙는다.

1. **DB 생성 + 스키마 적용** (호스트에서 1회):
   ```bash
   ssh oracle_vm
   sudo -u postgres psql -c "CREATE DATABASE crypto;"
   sudo -u postgres psql -c "CREATE USER crypto WITH PASSWORD '<강한암호>';"
   sudo -u postgres psql -c "GRANT ALL PRIVILEGES ON DATABASE crypto TO crypto;"
   # init.sql 적용 (repo clone 후)
   sudo -u postgres psql -d crypto -f db/init.sql
   ```
2. **컨테이너 → 호스트 Postgres 접속 주소**:
   - Podman: `host.containers.internal:5432` 사용 (compose 서비스명 `postgres`는 못 씀)
   - 또는 해당 서비스만 `--network=host` 로.
   - spark/backend 의 `POSTGRES_HOST` 를 `host.containers.internal` 로 세팅.
3. **호스트 Postgres가 컨테이너 접속을 허용**해야 함:
   - `postgresql.conf`: `listen_addresses` 에 podman 브리지 대역 포함(또는 `*`, 방화벽으로 보호)
   - `pg_hba.conf`: podman 네트워크 대역(예: `10.88.0.0/16`)에서 `crypto` DB md5 허용
   - 반영: `sudo systemctl reload postgresql`
4. **주의**: midas와 **같은 인스턴스**다. `crypto` DB/유저 권한을 midas DB와 분리하고,
   비밀번호는 `.env`로만 관리(커밋 금지).

---

## 5. 배포 방식 (midas 패턴 재사용 권장)

midas-touch는 **VM이 2분마다 main을 polling** 해서 스스로 배포한다
(`~/Develop/midas-touch/infra/vm-autodeploy.sh` + systemd timer).
Actions에서 SSH push가 아니라 **pull**인 이유: VM에 DB가 같이 돌아서
셸 열리는 키를 레포 시크릿에 두기 부담스럽기 때문. kaspflow도 같은 VM/상황이라 동일 논리 적용.

### kaspflow용으로 이식할 것 (다음 세션 작업 목록)
- [ ] `docker-compose.prod.yml` 작성:
      - postgres 서비스 **제거**(호스트 공유), 나머지는 `image: ghcr.io/<owner>/<repo>-<svc>:latest`
      - spark 에 메모리 상한: `SPARK_DRIVER_MEMORY=1g` 등 + (podman) `mem_limit`
      - `POSTGRES_HOST=host.containers.internal`
- [ ] `infra/kaspflow-autodeploy.sh` (midas 스크립트 참고: git pull --ff-only →
      `podman compose pull` → `up -d` → 헬스체크 → 실패 시 롤백)
- [ ] `infra/kaspflow-autodeploy.{service,timer}` (2분 폴링) 또는 Quadlet
- [ ] CI 게이트: publish-images 성공 커밋만 배포되게(선택)
- [ ] 방화벽: 대시보드 포트(8501) VCN Security List + OS 방화벽 양쪽 open
      (midas 런북 교훈: 오라클은 콘솔 VCN + VM 방화벽 둘 다 열어야 한다)

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
