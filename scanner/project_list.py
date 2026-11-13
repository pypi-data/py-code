"""List every PyPI project that changed in the last N days, from PyPI's own APIs.

Usage: python3 scanner/project_list.py data/candidates.tsv [--days 365]

1. The Simple API (JSON) lists every project with its `_last-serial`, the serial of its newest event.
2. A binary search over the XML-RPC `changelog_since_serial` finds the serial at the cut-off date.
3. Projects whose last serial is newer than that changed in the window: new releases, but also yanks,
   deletions or metadata edits. scan.py with --since drops those whose latest release is older.

Output: one "name<TAB>0" line per project, the format scan.py reads.
"""
import argparse
import sys
import time
import xmlrpc.client
from datetime import datetime, timedelta, timezone

import requests

UA = "py-code.org-scanner/0.2 (latest-release import index)"


def serial_at(when: float) -> int:
    """Smallest serial whose first following event is at or after `when` (Unix seconds)."""
    rpc = xmlrpc.client.ServerProxy("https://pypi.org/pypi")
    lo, hi = 0, rpc.changelog_last_serial()
    while hi - lo > 20_000:  # ~1 hour of events; precise enough for a one-year window
        mid = (lo + hi) // 2
        events = rpc.changelog_since_serial(mid)
        time.sleep(1)  # be polite to XML-RPC
        if events and events[0][2] < when:
            lo = mid
        else:
            hi = mid
    return hi


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--days", type=int, default=365)
    a = ap.parse_args()
    cutoff = datetime.now(timezone.utc) - timedelta(days=a.days)
    serial = serial_at(cutoff.timestamp())
    print(f"serial at {cutoff:%Y-%m-%d}: {serial}", file=sys.stderr)
    r = requests.get("https://pypi.org/simple/", headers={"Accept": "application/vnd.pypi.simple.v1+json", "User-Agent": UA}, timeout=300)
    r.raise_for_status()
    projects = r.json()["projects"]
    names = sorted(p["name"] for p in projects if p["_last-serial"] >= serial)
    with open(a.out, "w") as f:
        f.writelines(f"{n}\t0\n" for n in names)
    print(f"{len(projects):,} projects on PyPI, {len(names):,} changed since {cutoff:%Y-%m-%d}", file=sys.stderr)


if __name__ == "__main__":
    main()
