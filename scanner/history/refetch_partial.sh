#!/usr/bin/env bash
# Re-fetch the 3 name buckets that were cut off mid-stream, then refresh scan_list_extra.tsv.
set -u; cd "$(dirname "$0")/.."
CH="https://sql-clickhouse.clickhouse.com/?user=demo"
q() { curl -s --max-time 300 "$CH" --data-binary "$1"; }
log() { echo "$(date -u +%FT%TZ) partial: $*" >> data/followup.log; }
for name in yhsivsqqntrnnins ilskmpxspemwlvip openshift-python-utilities; do
  while :; do k=$(q "SELECT cityHash64(lower('$name')) % 40 FORMAT TSV"); [[ "$k" =~ ^[0-9]+$ ]] && break; sleep 600; done
  rm -f data/names_extra/$k.tsv.have
  while :; do
    q "SELECT DISTINCT name FROM pypi.projects WHERE upload_time >= now() - INTERVAL 365 DAY AND cityHash64(lower(name)) % 40 = $k FORMAT TSV" > data/names_extra/$k.tsv
    grep -q '^Code: ' data/names_extra/$k.tsv || break; log "bucket $k waiting for quota"; sleep 600
  done
  log "bucket $k (from $name): $(wc -l < data/names_extra/$k.tsv) names"
done
cat data/names_extra/*.tsv | grep -v '^Code: ' | sort -u | grep -vxF -f data/scan_list_names.txt | awk '{print $0"\t0"}' > data/scan_list_extra.tsv
log "extra names to scan now: $(wc -l < data/scan_list_extra.tsv)"
