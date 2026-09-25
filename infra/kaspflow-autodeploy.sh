#!/usr/bin/env bash
#
# kaspflow-autodeploy.sh — VM이 GitHub main 을 폴링해서 움직이면 스스로 배포한다.
# (midas-touch 의 vm-autodeploy.sh 패턴을 Docker compose 버전으로 각색)
#
# 왜 pull(폴링) 방식인가:
#   GitHub Actions 에서 SSH 로 밀어넣으려면 셸이 열리는 키를 레포 시크릿에 둬야 한다.
#   이 VM 은 네이티브 Postgres 도 돌리므로, pull 방식이 시크릿도 새 인바운드 통로도
#   필요 없어 더 안전하다. (레포가 public 이면 git pull 에 인증도 불필요)
#
# 하는 일:
#   1) origin/main 이 로컬과 다르면 감지
#   2) (선택) GitHub check-runs 로 publish-images/CI 통과한 커밋만 배포 (CI 게이트)
#   3) git pull → docker compose pull → up -d
#   4) 헬스체크(backend /health) 실패 시 직전 커밋으로 롤백
#
# 설정: 아래 변수는 환경(예: systemd EnvironmentFile)이나 기본값으로 결정된다.
#   REPO_DIR   : repo 루트 (docker-compose.prod.yml 이 있는 곳)
#   GH_REPO    : "<owner>/<repo>" (CI 게이트 조회용). 비우면 게이트 건너뜀.
#   COMPOSE    : compose 파일 경로
#   BRANCH     : 배포 브랜치 (기본 main)

set -uo pipefail

REPO_DIR="${REPO_DIR:-/home/ubuntu/kaspflow}"
GH_REPO="${GH_REPO:-}"                 # 예: "Jacob-9909/kaspflow" (비우면 CI 게이트 skip)
COMPOSE="${COMPOSE:-docker-compose.prod.yml}"
BRANCH="${BRANCH:-main}"
HEALTH="${HEALTH:-http://127.0.0.1:8000/health}"

log() { printf '%s | %s\n' "$(date '+%F %T')" "$*"; }

# 타이머가 겹쳐 들어와 배포 도중에 또 배포하는 사고를 막는다.
exec 9>/tmp/kaspflow-autodeploy.lock
flock -n 9 || { log "이전 실행이 아직 도는 중 — 건너뜀"; exit 0; }

cd "$REPO_DIR" || { log "❌ $REPO_DIR 없음"; exit 1; }

git fetch -q origin "$BRANCH" || { log "❌ git fetch 실패(네트워크?)"; exit 1; }

LOCAL="$(git rev-parse HEAD)"
REMOTE="$(git rev-parse "origin/$BRANCH")"
[ "$LOCAL" = "$REMOTE" ] && exit 0          # 변화 없음 — 조용히 종료

log "새 커밋 감지: ${LOCAL:0:7} → ${REMOTE:0:7}"

# ── 1) CI 게이트 (GH_REPO 설정 시에만) ──────────────────────────
# publish-images 워크플로가 성공한 커밋만 배포한다. 아직 도는 중이면 다음 틱에 재확인.
if [ -n "$GH_REPO" ] && command -v jq >/dev/null 2>&1; then
  API="https://api.github.com/repos/$GH_REPO"
  CHECKS="$(curl -s -m 20 -H 'Accept: application/vnd.github+json' "$API/commits/$REMOTE/check-runs")"
  # "Publish Images" 잡의 상태를 본다. 없으면 보류(아직 트리거 전일 수 있음).
  GATE="$(printf '%s' "$CHECKS" \
    | jq -r '.check_runs[]? | select(.name | test("[Pp]ublish")) | "\(.status):\(.conclusion)"' 2>/dev/null | head -1)"
  case "$GATE" in
    completed:success) ;;                                  # 통과 — 계속
    "")                log "⏳ publish-images 체크 없음 — 보류"; exit 0 ;;
    completed:*)       log "❌ publish-images ${GATE#completed:} — ${REMOTE:0:7} 배포 안 함"; exit 0 ;;
    *)                 log "⏳ publish-images 진행 중($GATE) — 보류"; exit 0 ;;
  esac
fi

# ── 2) 코드 갱신 ────────────────────────────────────────────────
git pull -q --ff-only origin "$BRANCH" || { log "❌ pull 실패(로컬 변경 있음?)"; exit 1; }

# ── 3) 배포 (이미지 pull → 기동) ────────────────────────────────
deploy_and_wait() {
  # .env 는 REPO_DIR 에 있어야 한다 (compose가 자동으로 읽음)
  docker compose -f "$COMPOSE" pull -q 2>/dev/null || docker compose -f "$COMPOSE" pull || return 1
  docker compose -f "$COMPOSE" up -d || return 1
  # backend 헬스가 초록(=DB까지 연결)일 때까지 대기 (최대 ~120초)
  for _ in $(seq 1 24); do
    sleep 5
    curl -s -m 5 "$HEALTH" 2>/dev/null | grep -q '"status":"ok"' && return 0
  done
  return 1
}

if deploy_and_wait; then
  log "✅ 배포 완료: $(git log --oneline -1)"
  exit 0
fi

# ── 4) 실패 시 롤백 ─────────────────────────────────────────────
log "❌ 배포 후 헬스 실패 — ${LOCAL:0:7} 로 롤백"
git reset -q --hard "$LOCAL"
if deploy_and_wait; then
  log "↩️  롤백 성공 — ${LOCAL:0:7} 복구됨. ${REMOTE:0:7} 은 배포되지 않았다."
else
  log "🚨 롤백 후에도 헬스 실패 — 사람이 봐야 한다: docker compose -f $COMPOSE logs --tail=100"
fi
exit 1
