#!/bin/sh
# kaspflow 판단 기록 백업: crypto DB 의 stock 스키마를 하루 한 번 pg_dump 한다.
# cache 표(시세·AI 응답 캐시, 12시간짜리)는 구조만 남기고 데이터는 뺀다. KEEP_DAYS 가 지난 파일은 지운다.
# postgres 사용자로 돈다 (peer 인증). 설치·복구 절차는 infra/vm-setup.md §L.
set -eu
dir=${BACKUP_DIR:-/var/backups/postgres}
keep=${KEEP_DAYS:-14}
out="$dir/kaspflow-stock-$(date +%Y%m%d).dump"
pg_dump -d crypto -n stock --exclude-table-data=stock.cache -Fc -f "$out.tmp"
mv "$out.tmp" "$out"
find "$dir" -name 'kaspflow-stock-*.dump' -mtime +"$keep" -delete
echo "backup ok: $out ($(du -h "$out" | cut -f1))"
