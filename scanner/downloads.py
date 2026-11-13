"""Fetch 30-day download counts for the most-downloaded PyPI projects from ClickHouse's public demo.

Usage: python3 scanner/downloads.py data/downloads.tsv [--top 60000]

The demo server (user "demo", database "pypi") returns at most 10,000 rows per query and
60,000 result rows per hour, reset on the hour (UTC). This pages through the results and,
when the quota runs out, waits for the reset and continues. A page that comes back with an
error is retried, never written. Projects outside the top N get 0 downloads in the index.
"""
import argparse
import sys
import time

import requests

CH = "https://sql-clickhouse.clickhouse.com/?user=demo"
PAGE = 10_000
QUERY = ("SELECT project, sum(count) AS c FROM pypi.pypi_downloads_per_day "
         "WHERE date >= today() - 30 GROUP BY project ORDER BY c DESC, project LIMIT {n} OFFSET {off} FORMAT TSV")


def page(off: int, n: int) -> str:
    while True:
        r = requests.post(CH, data=QUERY.format(n=n, off=off), timeout=600)
        body = r.text
        if r.ok and not body.startswith("Code: "):
            return body
        print(f"offset {off}: {body[:120].strip()} - waiting 10 min", file=sys.stderr)
        time.sleep(600)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--top", type=int, default=60_000)
    a = ap.parse_args()
    rows = []
    for off in range(0, a.top, PAGE):
        body = page(off, min(PAGE, a.top - off))
        got = [l for l in body.splitlines() if l.strip()]
        rows += got
        print(f"offset {off}: {len(got)} rows", file=sys.stderr)
        if len(got) < PAGE:
            break
    with open(a.out, "w") as f:
        f.write("\n".join(rows) + "\n")
    print(f"{len(rows):,} projects with download counts", file=sys.stderr)


if __name__ == "__main__":
    main()
