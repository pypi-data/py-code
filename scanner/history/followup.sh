#!/usr/bin/env bash
# Finish the first full run unattended:
#   1. re-fetch project-name buckets that hit the ClickHouse demo quota
#   2. in the next quota hour, fetch 30-day downloads for the top 60k projects
#   3. when the main scan ends, scan the extra names, then rebuild the index
# Log: data/followup.log. Safe to rerun: every step skips work already done.
set -u
cd "$(dirname "$0")/.."
PY=/usr/bin/python3
CH="https://sql-clickhouse.clickhouse.com/?user=demo"
log() { echo "$(date -u +%FT%TZ) $*" >> data/followup.log; }
q() { curl -s --max-time 300 "$CH" --data-binary "$1"; }

wait_quota() {  # sleep until the query works (quota resets on the hour, UTC)
  while q "SELECT 1 FORMAT TSV" | grep -q QUOTA; do log "quota exhausted, waiting"; sleep 600; done
}

# 1. names: probe each bucket with one name; re-fetch buckets we don't have
log "step 1: names"
mkdir -p data/names_extra
for k in $(seq 0 39); do
  f=data/names_extra/$k.tsv
  [ -s "$f" ] && ! grep -q '^Code: ' "$f" && continue
  wait_quota
  probe=$(q "SELECT name FROM pypi.projects WHERE upload_time >= now() - INTERVAL 365 DAY AND cityHash64(lower(name)) % 40 = $k LIMIT 1 FORMAT TSV")
  if grep -qxF -- "$probe" data/recent_names.tsv; then echo "have" > "$f.have"; continue; fi
  q "SELECT DISTINCT name FROM pypi.projects WHERE upload_time >= now() - INTERVAL 365 DAY AND cityHash64(lower(name)) % 40 = $k FORMAT TSV" > "$f"
  if grep -q '^Code: ' "$f"; then log "bucket $k failed: $(head -c 120 "$f")"; rm -f "$f"; sleep 600; wait_quota
    q "SELECT DISTINCT name FROM pypi.projects WHERE upload_time >= now() - INTERVAL 365 DAY AND cityHash64(lower(name)) % 40 = $k FORMAT TSV" > "$f"; fi
  log "bucket $k: $(wc -l < "$f") names"
done
cat data/names_extra/*.tsv 2>/dev/null | grep -v '^Code: ' | sort -u | grep -vxF -f data/scan_list_names.txt 2>/dev/null | awk '{print $0"\t0"}' > data/scan_list_extra.tsv
log "extra names to scan: $(wc -l < data/scan_list_extra.tsv)"

# 2. downloads for the top 60k projects, 10k rows per query (demo row cap)
log "step 2: downloads"
: > data/dl_top60k.tsv.tmp
for off in 0 10000 20000 30000 40000 50000; do
  for attempt in 1 2 3 4 5 6 7 8; do
    wait_quota
    out=$(q "SELECT project, sum(count) c FROM pypi.pypi_downloads_per_day WHERE date >= today()-30 GROUP BY project ORDER BY c DESC LIMIT 10000 OFFSET $off FORMAT TSV")
    if echo "$out" | grep -q '^Code: '; then log "downloads offset $off: $(echo "$out" | head -c 120)"; sleep 600; continue; fi
    echo "$out" >> data/dl_top60k.tsv.tmp; log "downloads offset $off: ok"; break
  done
done
mv data/dl_top60k.tsv.tmp data/dl_top60k.tsv
log "downloads rows: $(wc -l < data/dl_top60k.tsv)"

# 3. wait for the main scan, scan extras, rebuild
log "step 3: waiting for main scan"
until grep -q '^done' data/scan_full.log; do sleep 120; done
log "main scan: $(grep '^done' data/scan_full.log)"
if [ -s data/scan_list_extra.tsv ]; then
  $PY scanner/scan.py data/scan_list_extra.tsv data/projects > data/scan_extra.log 2>&1
  log "extra scan: $(tail -1 data/scan_extra.log)"
fi
$PY scanner/build_index.py data/projects site/api --downloads data/top_downloads.tsv data/dl_top60k.tsv > data/build.log 2>&1
log "build: $(tail -1 data/build.log)"
log "ALL DONE"
