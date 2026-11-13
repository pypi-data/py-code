"""Build the search index from per-project scan results, for 200k+ projects.

Usage:
  python3 scanner/build_index.py data/projects dist/api [--downloads data/downloads.tsv ...] [--since YYYY-MM-DD]

A project is indexed if its latest release is on or after --since, or if it is in the downloads
file (the most downloaded projects), so popular but stable packages such as six stay in.

Stage 1 streams every data/projects/<name>.json once and writes flat NDJSON files
(one row per project x referenced name). Stage 2 aggregates them in DuckDB, which spills
to disk instead of holding everything in memory. Stage 3 streams the sorted result into
hash-sharded JSON files, so the number of files and their size stay bounded.

Output layout (the browser computes the same CRC32 to find a shard):
  api/manifest.json                      shard counts, big-module table, build info
  api/idx/<crc32(top) % IDX_SHARDS>.json {top: {name: {"total": n, "rows": [...]}}}
  api/big/<top>/<crc32(name) % n>.json   same shape, for modules too big for one shard
  api/names/<crc32(top) % IDX_SHARDS>.json {top: [[name, total], ...]}  (autocomplete)
  api/tops.json                          [[top, total], ...] for top-level autocomplete
  api/project/<crc32(name) % PROJECT_SHARDS>.json {normalized_name: doc}
  api/projects.json                      most-downloaded projects for the project list
  api/upgrade.json, api/stats.json, api/status.json

Rows are [project, version, downloads, files, [path:line, ...]], sorted by downloads.
"""

import argparse
import json
import re
import shutil
import sys
import tempfile
import time
import zlib
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import duckdb

IDX_SHARDS = 4096
PROJECT_SHARDS = 8192
ROW_CAP = 2000            # rows stored per name; "total" keeps the true count
BIG_BYTES = 1_500_000     # a top-level module above this gets its own sub-shards
BIG_PART_BYTES = 1_000_000
NAMES_CAP = 5000          # autocomplete names kept per top-level module
TOPS_MIN = 2              # top-level modules used by fewer projects are left out of tops.json
PROJECT_LIST = 20000

UPGRADE = [
    {"title": "Removed in Python 3.14", "items": [
        ("ast.Num", "Use ast.Constant"), ("ast.Str", "Use ast.Constant"),
        ("ast.Bytes", "Use ast.Constant"), ("ast.NameConstant", "Use ast.Constant"),
        ("ast.Ellipsis", "Use ast.Constant"),
        ("pkgutil.find_loader", "Use importlib.util.find_spec"),
        ("pkgutil.get_loader", "Use importlib.util.find_spec"),
        ("asyncio.get_child_watcher", "Child watchers were removed"),
        ("asyncio.set_child_watcher", "Child watchers were removed"),
        ("asyncio.AbstractChildWatcher", "Child watchers were removed")]},
    {"title": "Removed in Python 3.13 (PEP 594 dead batteries)", "items": [(m, "Module removed") for m in [
        "aifc", "audioop", "cgi", "cgitb", "chunk", "crypt", "imghdr", "mailcap", "msilib", "nis",
        "nntplib", "ossaudiodev", "pipes", "sndhdr", "spwd", "sunau", "telnetlib", "uu", "xdrlib", "lib2to3"]]},
    {"title": "Removed in Python 3.12", "items": [
        ("distutils", "Use setuptools or packaging"), ("imp", "Use importlib"),
        ("asynchat", "Use asyncio"), ("asyncore", "Use asyncio"), ("smtpd", "Use aiosmtpd")]},
    {"title": "Ecosystem deprecations", "items": [
        ("pkg_resources", "Deprecated by setuptools; use importlib.metadata / importlib.resources"),
        ("numpy.float", "Removed in NumPy 1.24; use float"), ("numpy.int", "Removed in NumPy 1.24; use int"),
        ("numpy.object", "Removed in NumPy 1.24; use object"),
        ("numpy.complex", "Removed in NumPy 1.24; use complex")]},
]
UPGRADE_NAMES = {n for g in UPGRADE for n, _ in g["items"]}
REMOVED_STDLIB = {"distutils", "imp", "asynchat", "asyncore", "smtpd", "aifc", "audioop", "cgi", "cgitb",
                  "chunk", "crypt", "imghdr", "mailcap", "msilib", "nis", "nntplib", "ossaudiodev", "pipes",
                  "sndhdr", "spwd", "sunau", "telnetlib", "uu", "xdrlib", "lib2to3"}
STDLIB = set(sys.stdlib_module_names) | REMOVED_STDLIB


def crc(s):
    return zlib.crc32(s.encode("utf-8"))


def norm(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def dump(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, separators=(",", ":")))


def load_removed():
    """Projects whose authors asked to be left out (scanner/removed.txt)."""
    f = Path(__file__).with_name("removed.txt")
    lines = f.read_text().splitlines() if f.exists() else []
    return {norm(l.split("#", 1)[0].strip()) for l in lines if l.split("#", 1)[0].strip()}


def load_downloads(files):
    dl = {}
    for f in files:
        for line in Path(f).read_text().splitlines():
            parts = line.split("\t")
            if len(parts) == 2 and parts[1].isdigit():
                k = norm(parts[0])
                dl[k] = max(dl.get(k, 0), int(parts[1]))
    return dl


def in_scope(d, since, downloads):
    """Latest release on or after `since`, or one of the most downloaded projects."""
    return not since or (d.get("upload_time") or "") >= since or downloads.get(norm(d["name"]), 0) > 0


def unique_files(src):
    """One scan file per normalized project name. Older runs saved some projects under two
    spellings (ab-engine.json and ab_engine.json); keep the one with the newest upload."""
    groups = defaultdict(list)
    for f in src.glob("*.json"):
        groups[norm(f.stem)].append(f)
    for files in groups.values():
        if len(files) == 1:
            yield files[0]
            continue
        def key(f):
            try:
                d = json.loads(f.read_text())
            except ValueError:
                return (0, "")
            return (0 if "error" in d or "skipped" in d else 1, d.get("upload_time") or "")
        yield max(files, key=key)


def stage1(src, tmp, downloads, since=""):
    """Stream project files into flat NDJSON for DuckDB."""
    refs_f = open(tmp / "refs.ndjson", "w")
    proj_f = open(tmp / "projects.ndjson", "w")
    own_f = open(tmp / "own.ndjson", "w")
    n_ok = n_err = 0
    totals = {"py_files": 0, "py_failed": 0, "bytes_fetched": 0, "bytes_full_files": 0}
    pth = []
    removed = load_removed()
    for f in unique_files(src):
        if norm(f.stem) in removed:
            continue
        try:
            d = json.loads(f.read_text())
        except ValueError:
            n_err += 1
            continue
        if "skipped" in d or (since and "error" not in d and not in_scope(d, since, downloads)):
            continue  # latest release is outside the date window and the project is not popular
        if "error" in d:
            n_err += 1
            continue
        n_ok += 1
        name = d["name"]
        dls = downloads.get(norm(name), 0)
        imports = {"stdlib": [], "third_party": []}
        for ref, (files, ex) in d["refs"].items():
            top = ref.split(".", 1)[0]
            refs_f.write(json.dumps({"top": top, "name": ref, "project": name, "version": d["version"],
                                     "downloads": dls, "files": files, "ex": ex}) + "\n")
            if "." not in ref:
                imports["stdlib" if ref in STDLIB else "third_party"].append(ref)
        for m in d["own_modules"]:
            own_f.write(json.dumps({"project": name, "module": m}) + "\n")
        if d["pth_with_import"]:
            pth.append({"name": name, "downloads": dls, "files": d["pth_with_import"]})
        for k in ("py_files", "py_failed", "bytes_fetched"):
            totals[k] += d.get(k, 0)
        totals["bytes_full_files"] += d.get("file_size", 0)
        proj_f.write(json.dumps({
            "key": norm(name), "name": name, "version": d["version"], "summary": (d.get("summary") or "")[:300],
            "downloads": dls, "file": d.get("file"), "upload_time": d.get("upload_time"),
            "requires_python": d.get("requires_python"), "py_files": d["py_files"], "py_failed": d["py_failed"],
            "own_modules": d["own_modules"], "pth_with_import": d["pth_with_import"],
            "imports": {k: sorted(v) for k, v in imports.items()},
            "upgrade_hits": sorted(n for n in d["refs"] if n in UPGRADE_NAMES)}) + "\n")
    for fh in (refs_f, proj_f, own_f):
        fh.close()
    totals["pth_count"] = len(pth)
    totals["pth_with_import"] = sorted(pth, key=lambda x: (-x["downloads"], x["name"].lower()))[:200]
    totals["since"] = since
    return n_ok, n_err, totals


def stage2(con, tmp):
    con.execute(f"""
        CREATE TABLE refs AS SELECT * FROM read_json('{tmp}/refs.ndjson', format='newline_delimited',
          columns={{'top':'VARCHAR','name':'VARCHAR','project':'VARCHAR','version':'VARCHAR',
                   'downloads':'BIGINT','files':'INTEGER','ex':'VARCHAR[]'}})""")
    con.execute(f"""
        CREATE TABLE own AS SELECT * FROM read_json('{tmp}/own.ndjson', format='newline_delimited',
          columns={{'project':'VARCHAR','module':'VARCHAR'}})""")
    # Per name: true total, plus the most-downloaded ROW_CAP rows.
    con.execute(f"""
        CREATE TABLE ranked AS
        SELECT top, name, project, version, downloads, files, ex,
               count(*) OVER (PARTITION BY name) AS total
        FROM refs
        QUALIFY row_number() OVER (PARTITION BY name ORDER BY downloads DESC, project) <= {ROW_CAP}""")
    con.execute("""
        CREATE TABLE top_totals AS
        SELECT top, count(DISTINCT project) AS total FROM refs GROUP BY top""")
    con.execute("""
        CREATE TABLE name_totals AS SELECT top, name, count(*) AS total FROM refs GROUP BY top, name""")


def flush_shards(out, buf, kind):
    for shard, obj in buf.items():
        dump(out / kind / f"{shard}.json", obj)


def stage3(con, out):
    big = {}
    idx_buf, cur_top, cur = {}, None, {}

    def close_top(top, data):
        size = len(json.dumps(data, separators=(",", ":")))
        if size > BIG_BYTES:
            parts = max(2, -(-size // BIG_PART_BYTES))
            big[top] = parts
            split = {}
            for name, v in data.items():
                split.setdefault(crc(name) % parts, {})[name] = v
            for p, obj in split.items():
                dump(out / "big" / top / f"{p}.json", obj)
        else:
            idx_buf.setdefault(f"{crc(top) % IDX_SHARDS:03x}", {})[top] = data

    res = con.execute("""SELECT top, name, total, project, version, downloads, files, ex
                         FROM ranked ORDER BY top, name, downloads DESC, project""")
    while True:
        chunk = res.fetchmany(200_000)
        if not chunk:
            break
        for top, name, total, project, version, downloads, files, ex in chunk:
            if top != cur_top:
                if cur_top is not None:
                    close_top(cur_top, cur)
                cur_top, cur = top, {}
            e = cur.get(name)
            if e is None:
                e = cur[name] = {"total": total, "rows": []}
            e["rows"].append([project, version, downloads, files, ex])
    if cur_top is not None:
        close_top(cur_top, cur)
    flush_shards(out, idx_buf, "idx")

    names_buf = {}
    res = con.execute(f"""SELECT top, name, total FROM name_totals
                          QUALIFY row_number() OVER (PARTITION BY top ORDER BY total DESC, name) <= {NAMES_CAP}
                          ORDER BY top, total DESC, name""")
    for top, name, total in res.fetchall():
        names_buf.setdefault(f"{crc(top) % IDX_SHARDS:03x}", {}).setdefault(top, []).append([name, total])
    flush_shards(out, names_buf, "names")

    tops = con.execute(f"SELECT top, total FROM top_totals WHERE total >= {TOPS_MIN} ORDER BY total DESC, top").fetchall()
    dump(out / "tops.json", [list(t) for t in tops])
    return big


def stage4(con, tmp, out):
    # Users of each project's own top-level modules (exact top-level import), excluding itself.
    con.execute(f"""
        CREATE TABLE used AS
        SELECT o.project, o.module, count(DISTINCT n.project) AS users
        FROM own o JOIN refs n ON n.name = o.module AND n.project <> o.project
        GROUP BY o.project, o.module""")
    used = {}
    for project, module, users in con.execute("SELECT project, module, users FROM used").fetchall():
        used.setdefault(project, {})[module] = users

    buf, plist = {}, []
    with open(tmp / "projects.ndjson") as fh:
        for line in fh:
            p = json.loads(line)
            per = used.get(p["name"], {})
            k = p["key"].replace("-", "_")
            primary = next((m for m in p["own_modules"] if m.lower() == k), None) or (
                max(per, key=per.get) if per else (p["own_modules"][0] if p["own_modules"] else None))
            p.update({"used_by_module": per, "primary_module": primary,
                      "used_by": per.get(primary, 0) if primary else 0})
            buf.setdefault(f"{crc(p['key']) % PROJECT_SHARDS:04x}", {})[p["key"]] = p
            plist.append([p["name"], p["downloads"], p["summary"][:120]])
    flush_shards(out, buf, "project")
    plist.sort(key=lambda x: -x[1])
    dump(out / "projects.json", plist[:PROJECT_LIST])


def stage5(con, out, n_ok, n_err, totals, big, t0):
    upgrade = []
    for g in UPGRADE:
        items = []
        for n, note in g["items"]:
            row = con.execute("SELECT total FROM name_totals WHERE name = ?", [n]).fetchone()
            top = [r[0] for r in con.execute(
                "SELECT project FROM ranked WHERE name = ? ORDER BY downloads DESC LIMIT 5", [n]).fetchall()]
            items.append({"name": n, "note": note, "count": row[0] if row else 0, "top": top})
        upgrade.append({"title": g["title"], "items": items})
    dump(out / "upgrade.json", upgrade)

    tops = con.execute("SELECT top, total FROM top_totals ORDER BY total DESC LIMIT 400").fetchall()
    stats = dict(totals, projects_indexed=n_ok, projects_failed=n_err,
                 names=con.execute("SELECT count(*) FROM name_totals").fetchone()[0],
                 top_third_party=[list(t) for t in tops if t[0] not in STDLIB][:25],
                 top_stdlib=[list(t) for t in tops if t[0] in STDLIB][:25])
    dump(out / "stats.json", stats)
    built = datetime.now(timezone.utc).isoformat(timespec="minutes")
    dump(out / "status.json", {"built": built, "projects_indexed": n_ok,
                               "since": totals["since"],
                               "scope": f"{n_ok:,} PyPI projects (latest release"
                                        + (f"; released since {totals['since']}, or among the most downloaded)"
                                           if totals["since"] else ")")})
    dump(out / "manifest.json", {"idx_shards": IDX_SHARDS, "project_shards": PROJECT_SHARDS,
                                 "big": big, "row_cap": ROW_CAP, "built": built,
                                 "build_seconds": round(time.time() - t0)})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("out")
    ap.add_argument("--downloads", nargs="*", default=["data/downloads.tsv"])
    ap.add_argument("--since", default="", help="leave out projects whose latest release is older (YYYY-MM-DD), "
                    "unless they are in the downloads file")
    ap.add_argument("--memory", default="8GB")
    a = ap.parse_args()
    src, out = Path(a.src), Path(a.out)
    t0 = time.time()
    downloads = load_downloads([f for f in a.downloads if Path(f).exists()])

    with tempfile.TemporaryDirectory(prefix="pycode-build-") as td:
        tmp = Path(td)
        n_ok, n_err, totals = stage1(src, tmp, downloads, a.since)
        print(f"stage1: {n_ok} projects, {n_err} errors, {time.time()-t0:.0f}s", flush=True)
        con = duckdb.connect(str(tmp / "build.duckdb"))
        con.execute(f"SET memory_limit='{a.memory}'; SET temp_directory='{tmp}/spill'; SET preserve_insertion_order=false")
        stage2(con, tmp)
        print(f"stage2: aggregated, {time.time()-t0:.0f}s", flush=True)
        staging = out.with_name(out.name + ".new")
        if staging.exists():
            shutil.rmtree(staging)
        big = stage3(con, staging)
        print(f"stage3: index written ({len(big)} big modules), {time.time()-t0:.0f}s", flush=True)
        stage4(con, tmp, staging)
        print(f"stage4: projects written, {time.time()-t0:.0f}s", flush=True)
        stage5(con, staging, n_ok, n_err, totals, big, t0)
        con.close()
    # Swap the finished build in, so the live preview never sees a half-written index.
    old = out.with_name(out.name + ".old")
    if old.exists():
        shutil.rmtree(old)
    if out.exists():
        out.rename(old)
    staging.rename(out)
    if old.exists():
        shutil.rmtree(old)
    print(f"done in {time.time()-t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
