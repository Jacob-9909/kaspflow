#!/bin/sh
# kaspflow 판단 기록 백업: crypto DB 의 stock 스키마를 하루 한 번 pg_dump 한다.
# cache 표(시세·AI 응답 캐시, 12시간짜리)는 구조만 남기고 데이터는 뺀다.
# 보관: 매일 백업은 KEEP_DAYS(60)일, 매달 1일 백업은 KEEP_MONTHLY_DAYS(730)일. 손상을 두 달 늦게 알아차려도 되돌리고,
# 판단 기록(적중률 표본)은 2년 치 월별 시점이 남는다. 파일이 하루 약 200KB 라 다 합쳐도 수십 MB 다.
# postgres 사용자로 돈다 (peer 인증). 설치·복구 절차는 infra/vm-setup.md §L.
set -eu
dir=${BACKUP_DIR:-/var/backups/postgres}
keep=${KEEP_DAYS:-60}
keep_monthly=${KEEP_MONTHLY_DAYS:-730}
out="$dir/kaspflow-stock-$(date +%Y%m%d).dump"
pg_dump -d crypto -n stock --exclude-table-data=stock.cache -Fc -f "$out.tmp"
mv "$out.tmp" "$out"
find "$dir" -name 'kaspflow-stock-*.dump' ! -name 'kaspflow-stock-??????01.dump' -mtime +"$keep" -delete
find "$dir" -name 'kaspflow-stock-??????01.dump' -mtime +"$keep_monthly" -delete
echo "backup ok: $out ($(du -h "$out" | cut -f1))"
