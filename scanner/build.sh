#!/usr/bin/env bash
# Build the py-code.org index from nothing. Every step can be rerun; finished work is skipped.
#
#   scanner/build.sh            full build: list, downloads, scan, index, stats
#   scanner/build.sh refresh    weekly: re-scan only projects that changed in the last CHANGED_DAYS (31)
#
# Needs Python 3.11+ with the packages in requirements.txt, about 10 GB free disk,
# and a few hours. Logs go to data/build-<step>.log.
set -euo pipefail
cd "$(dirname "$0")/.."
PY=${PYTHON:-python3}
DAYS=${DAYS:-365}
SINCE=$(date -u -d "-$DAYS days" +%F 2>/dev/null || date -u -v-"$DAYS"d +%F)
mkdir -p data
step() { echo "$(date -u +%FT%TZ) $*" | tee -a data/build.log; }

if [ "${1:-}" = "refresh" ]; then
  step "list projects changed in the last ${CHANGED_DAYS:-31} days"
  $PY scanner/project_list.py data/changed.tsv --days "${CHANGED_DAYS:-31}" 2> data/build-list.log
  step "re-scan them"
  # SCAN_TIMEOUT (e.g. 4h) stops the scan early so the rest of the build still runs; the
  # projects it did not reach keep their previous result.
  rc=0; timeout "${SCAN_TIMEOUT:-0}" $PY scanner/scan.py data/changed.tsv data/projects --since "$SINCE" --changed > data/build-scan.log 2>&1 || rc=$?
  [ "$rc" = 0 ] || [ "$rc" = 124 ] || exit "$rc"
  [ "$rc" = 0 ] || echo "scan stopped after ${SCAN_TIMEOUT} at $(tail -1 data/build-scan.log)" >> data/build-scan.log
else
  [ -s data/candidates.tsv ] || { step "list projects changed since $SINCE"; $PY scanner/project_list.py data/candidates.tsv --days "$DAYS" 2> data/build-list.log; }
  step "scan $(wc -l < data/candidates.tsv) candidates (resumes if interrupted)"
  $PY scanner/scan.py data/candidates.tsv data/projects --since "$SINCE" > data/build-scan.log 2>&1
fi
step "scan: $(tail -1 data/build-scan.log)"

# Download counts change daily; fetch them once per build. ClickHouse's hourly quota can make this wait.
if [ ! -s data/downloads.tsv ] || [ -n "$(find data/downloads.tsv -mtime +7)" ]; then
  step "download counts"
  $PY scanner/downloads.py data/downloads.tsv --top "${TOP:-60000}" 2> data/build-downloads.log
fi

# The most downloaded projects stay in the index even without a release in the window (six, PyYAML).
step "scan popular projects that have no usable result yet"
$PY scanner/scan.py data/downloads.tsv data/projects --rescan-skipped > data/build-scan-popular.log 2>&1
step "popular: $(tail -1 data/build-scan-popular.log)"

step "build index"
$PY scanner/build_index.py data/projects dist/api --downloads data/downloads.tsv --since "$SINCE" > data/build-index.log 2>&1
step "index: $(tail -1 data/build-index.log)"
$PY scanner/build_nerd_stats.py dist/api > /dev/null
cp scanner/data_headers dist/_headers
step "done. Deploy with scanner/deploy_data.sh"
