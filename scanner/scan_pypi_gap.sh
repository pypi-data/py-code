#!/usr/bin/env bash
# Scan projects that PyPI's own list shows changed in the last year but the ClickHouse list missed,
# then rebuild the index. Log: data/gap.log. Safe to rerun: the scan skips saved projects.
set -u
cd "$(dirname "$0")/.."
PY=/usr/bin/python3
log() { echo "$(date -u +%FT%TZ) $*" >> data/gap.log; }
log "gap scan start: $(wc -l < data/scan_list_pypi.tsv) candidates"
SCAN_SINCE=2025-11-13 $PY scanner/scan.py data/scan_list_pypi.tsv data/projects > data/scan_gap.log 2>&1
log "gap scan: $(tail -1 data/scan_gap.log)"
$PY scanner/build_index.py data/projects dist/api --downloads data/top_downloads.tsv data/dl_top60k.tsv > data/build.log 2>&1
log "build: $(tail -1 data/build.log)"
$PY scanner/build_nerd_stats.py dist/api > /dev/null && log "nerd stats done"
log "ALL DONE"
