"""Rebuild data/projects/<name>.json from the published Parquet tables.

Usage:
  python3 scanner/parquet_to_projects.py PARQUET_DIR data/projects

The monthly refresh runs on a fresh GitHub runner that has no scan results. This turns the
Parquet export (export_parquet.py, published on Hugging Face) back into the per-project files
that scan.py writes, so the refresh only has to re-scan the projects that changed.
PARQUET_DIR may be a local folder or hf://datasets/pypi-data/py-code.
"""
import argparse
import json
import re
import time
from pathlib import Path

import duckdb


def canonical(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("out")
    ap.add_argument("--memory", default="4GB")
    a = ap.parse_args()
    src, out = a.src.rstrip("/"), Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    con = duckdb.connect()
    con.execute(f"SET memory_limit='{a.memory}'")

    docs = {}
    cols = [c[0] for c in con.execute(f"DESCRIBE SELECT * FROM '{src}/projects.parquet'").fetchall()]
    for row in con.execute(f"SELECT * FROM '{src}/projects.parquet'").fetchall():
        p = dict(zip(cols, row))
        ut = p["upload_time"]
        docs[p["project"]] = {
            "name": p["project"], "version": p["version"], "file": p["file"], "file_size": p["file_size"],
            "bytes_fetched": p.get("bytes_fetched") or 0,
            "upload_time": ut.strftime("%Y-%m-%dT%H:%M:%S.%fZ") if ut else None,
            "requires_python": p["requires_python"], "summary": p["summary"],
            "py_files": p["py_files"], "py_failed": p["py_failed"],
            "own_modules": [], "pth_with_import": [], "refs": {},
        }
    for project, module in con.execute(f"SELECT project, module FROM '{src}/own_modules.parquet' ORDER BY project, module").fetchall():
        docs[project]["own_modules"].append(module)
    for project, pth in con.execute(f"SELECT project, pth FROM '{src}/pth_files.parquet' ORDER BY project, pth").fetchall():
        docs[project]["pth_with_import"].append(pth)
    print(f"projects: {len(docs)}, {time.time()-t0:.0f}s", flush=True)

    cur = con.execute(f"SELECT project, name, files, locations FROM '{src}/refs.parquet' ORDER BY project")
    current, written = None, 0

    def flush(name):
        nonlocal written
        (out / f"{canonical(name)}.json").write_text(json.dumps(docs.pop(name)))
        written += 1

    while True:
        rows = cur.fetchmany(200_000)
        if not rows:
            break
        for project, name, files, locations in rows:
            if project != current:
                if current is not None:
                    flush(current)
                current = project
            docs[project]["refs"][name] = [files, list(locations or [])]
    if current is not None:
        flush(current)
    for name in list(docs):  # projects that reference nothing
        flush(name)
    print(f"wrote {written} project files in {time.time()-t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
