"""Export the scan results as Parquet tables for bulk download and SQL.

Usage:
  python3 scanner/export_parquet.py data/projects dist/parquet [--downloads data/downloads.tsv ...] [--since YYYY-MM-DD]

Uses the same projects as build_index.py: one scan file per normalized name, no errors,
latest release on or after --since, and nothing listed in scanner/removed.txt.

Tables (zstd-compressed Parquet):
  projects.parquet     one row per project: release, Python floor, file counts, 30-day downloads
  refs.parquet         one row per (project, name): every module, function or class the code
                       imports or reaches by attribute, with the number of files and the first locations
  own_modules.parquet  one row per (project, top-level module the project provides)
  pth_files.parquet    .pth files that run an import when Python starts
"""
import argparse
import json
import tempfile
import time
from pathlib import Path

import duckdb

from build_index import load_downloads, load_removed, norm, unique_files


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("out")
    ap.add_argument("--downloads", nargs="*", default=["data/downloads.tsv"])
    ap.add_argument("--since", default="")
    ap.add_argument("--memory", default="8GB")
    a = ap.parse_args()
    src, out = Path(a.src), Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    downloads = load_downloads([f for f in a.downloads if Path(f).exists()])
    removed = load_removed()
    t0 = time.time()

    with tempfile.TemporaryDirectory(prefix="pycode-parquet-") as td:
        tmp = Path(td)
        fh = {k: open(tmp / f"{k}.ndjson", "w") for k in ("projects", "refs", "own_modules", "pth_files")}
        n = 0
        for f in unique_files(src):
            if norm(f.stem) in removed:
                continue
            try:
                d = json.loads(f.read_text())
            except ValueError:
                continue
            if "skipped" in d or "error" in d:
                continue
            if a.since and (d.get("upload_time") or "") < a.since:
                continue
            n += 1
            name = d["name"]
            fh["projects"].write(json.dumps({
                "project": name, "version": d["version"], "upload_time": d.get("upload_time"),
                "requires_python": d.get("requires_python"), "summary": (d.get("summary") or "")[:300],
                "file": d.get("file"), "file_size": d.get("file_size"),
                "py_files": d["py_files"], "py_failed": d["py_failed"],
                "downloads_30d": downloads.get(norm(name), 0)}) + "\n")
            for ref, (files, ex) in d["refs"].items():
                fh["refs"].write(json.dumps({"project": name, "name": ref, "module": ref.split(".", 1)[0],
                                             "files": files, "locations": ex}) + "\n")
            for m in d["own_modules"]:
                fh["own_modules"].write(json.dumps({"project": name, "module": m}) + "\n")
            for p in d["pth_with_import"]:
                fh["pth_files"].write(json.dumps({"project": name, "pth": p}) + "\n")
        for f in fh.values():
            f.close()
        print(f"read {n} projects in {time.time()-t0:.0f}s", flush=True)

        con = duckdb.connect()
        con.execute(f"SET memory_limit='{a.memory}'; SET temp_directory='{tmp}/spill'")
        order = {"projects": "project", "refs": "name, project", "own_modules": "module, project",
                 "pth_files": "project"}
        types = {
            "projects": "{'project':'VARCHAR','version':'VARCHAR','upload_time':'TIMESTAMP','requires_python':'VARCHAR',"
                        "'summary':'VARCHAR','file':'VARCHAR','file_size':'BIGINT','py_files':'INTEGER',"
                        "'py_failed':'INTEGER','downloads_30d':'BIGINT'}",
            "refs": "{'project':'VARCHAR','name':'VARCHAR','module':'VARCHAR','files':'INTEGER','locations':'VARCHAR[]'}",
            "own_modules": "{'project':'VARCHAR','module':'VARCHAR'}",
            "pth_files": "{'project':'VARCHAR','pth':'VARCHAR'}",
        }
        for k in fh:
            path = out / f"{k}.parquet"
            con.execute(f"""COPY (SELECT * FROM read_json('{tmp}/{k}.ndjson', format='newline_delimited',
                            columns={types[k]}) ORDER BY {order[k]})
                            TO '{path}' (FORMAT parquet, COMPRESSION zstd, ROW_GROUP_SIZE 500000)""")
            rows = con.execute(f"SELECT count(*) FROM '{path}'").fetchone()[0]
            print(f"{path.name}: {rows:,} rows, {path.stat().st_size/1e6:.1f} MB, {time.time()-t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
