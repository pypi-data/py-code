"""Turn per-project scan results into the static JSON the site reads.

Usage: /usr/bin/python3 scanner/build_index.py data/projects data/top_downloads.tsv site/api

Writes:
  api/idx/<top>.json        {name: [[project, version, downloads, files, [path:line...]], ...]}
  api/names/<top>.json      [[name, project_count], ...] for autocomplete
  api/tops.json             [[top_module, project_count], ...]
  api/project/<name>.json   one project's imports and who uses its modules
  api/projects.json         [[name, downloads, summary], ...] for project search
  api/upgrade.json          removed/deprecated names per Python version with counts
  api/stats.json            totals
  api/status.json           when the index was built
"""

import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

CAP = 5000

UPGRADE = [
    {
        "title": "Removed in Python 3.14",
        "items": [
            ("ast.Num", "Use ast.Constant"),
            ("ast.Str", "Use ast.Constant"),
            ("ast.Bytes", "Use ast.Constant"),
            ("ast.NameConstant", "Use ast.Constant"),
            ("ast.Ellipsis", "Use ast.Constant"),
            ("pkgutil.find_loader", "Use importlib.util.find_spec"),
            ("pkgutil.get_loader", "Use importlib.util.find_spec"),
            ("asyncio.get_child_watcher", "Child watchers were removed"),
            ("asyncio.set_child_watcher", "Child watchers were removed"),
            ("asyncio.AbstractChildWatcher", "Child watchers were removed"),
        ],
    },
    {
        "title": "Removed in Python 3.13 (PEP 594 dead batteries)",
        "items": [(m, "Module removed") for m in [
            "aifc", "audioop", "cgi", "cgitb", "chunk", "crypt", "imghdr", "mailcap", "msilib",
            "nis", "nntplib", "ossaudiodev", "pipes", "sndhdr", "spwd", "sunau", "telnetlib",
            "uu", "xdrlib", "lib2to3"]],
    },
    {
        "title": "Removed in Python 3.12",
        "items": [
            ("distutils", "Use setuptools or packaging"),
            ("imp", "Use importlib"),
            ("asynchat", "Use asyncio"),
            ("asyncore", "Use asyncio"),
            ("smtpd", "Use aiosmtpd"),
        ],
    },
    {
        "title": "Ecosystem deprecations",
        "items": [
            ("pkg_resources", "Deprecated by setuptools; use importlib.metadata / importlib.resources"),
            ("numpy.float", "Removed in NumPy 1.24; use float"),
            ("numpy.int", "Removed in NumPy 1.24; use int"),
            ("numpy.object", "Removed in NumPy 1.24; use object"),
            ("numpy.complex", "Removed in NumPy 1.24; use complex"),
        ],
    },
]


def main():
    src, dl_file, out = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
    downloads = {}
    for line in dl_file.read_text().splitlines():
        n, c = line.split("\t")
        downloads[n.lower()] = int(c)

    projects, errors = [], []
    for f in src.glob("*.json"):
        d = json.loads(f.read_text())
        (errors if "error" in d else projects).append(d)

    by_name = defaultdict(list)
    own_owner = {}  # top-level module -> project that ships it (most downloaded wins)
    for p in projects:
        p["downloads"] = downloads.get(p["name"].lower(), downloads.get(p["name"].lower().replace("_", "-"), 0))
    projects.sort(key=lambda p: -p["downloads"])
    for p in projects:
        for m in p["own_modules"]:
            own_owner.setdefault(m, p["name"])
        for name, (files, ex) in p["refs"].items():
            by_name[name].append([p["name"], p["version"], p["downloads"], files, ex])

    idx = defaultdict(dict)
    for name, rows in by_name.items():
        rows.sort(key=lambda r: -r[2])
        idx[name.split(".")[0]][name] = {"total": len(rows), "rows": rows[:CAP]}

    (out / "idx").mkdir(parents=True, exist_ok=True)
    (out / "names").mkdir(exist_ok=True)
    (out / "project").mkdir(exist_ok=True)
    for top, names in idx.items():
        (out / "idx" / f"{top}.json").write_text(json.dumps(names, separators=(",", ":")))
        lst = sorted(((n, v["total"]) for n, v in names.items()), key=lambda x: -x[1])
        (out / "names" / f"{top}.json").write_text(json.dumps(lst, separators=(",", ":")))
    tops = sorted(((t, v[t]["total"] if t in v else 0) for t, v in idx.items()), key=lambda x: -x[1])
    (out / "tops.json").write_text(json.dumps(tops, separators=(",", ":")))

    stdlib = set(sys.stdlib_module_names) | {
        "distutils", "imp", "asynchat", "asyncore", "smtpd", "aifc", "audioop", "cgi", "cgitb",
        "chunk", "crypt", "imghdr", "mailcap", "msilib", "nis", "nntplib", "ossaudiodev", "pipes",
        "sndhdr", "spwd", "sunau", "telnetlib", "uu", "xdrlib", "lib2to3"}

    for p in projects:
        imports = defaultdict(list)
        for name in p["refs"]:
            if "." not in name:
                imports["stdlib" if name in stdlib else "third_party"].append(name)
        users, per_module = set(), {}
        for m in p["own_modules"]:
            if m in idx and m in idx[m]:
                names = {r[0] for r in idx[m][m]["rows"]} - {p["name"]}
                per_module[m] = len(names)
                users |= names
        used_by = len(users)
        norm = p["name"].lower().replace("-", "_").replace(".", "_")
        primary = next((m for m in p["own_modules"] if m.lower() == norm), None) or (
            max(per_module, key=per_module.get) if per_module else (p["own_modules"][0] if p["own_modules"] else None))
        doc = {
            "name": p["name"], "version": p["version"], "summary": p.get("summary"),
            "downloads": p["downloads"], "file": p["file"], "upload_time": p.get("upload_time"),
            "requires_python": p.get("requires_python"), "py_files": p["py_files"],
            "py_failed": p["py_failed"], "own_modules": p["own_modules"],
            "imports": {k: sorted(v) for k, v in imports.items()},
            "pth_with_import": p["pth_with_import"], "used_by": used_by,
            "primary_module": primary, "used_by_module": per_module,
            "upgrade_hits": [n for g in UPGRADE for n, _ in g["items"] if n in p["refs"]],
        }
        (out / "project" / f"{p['name'].lower()}.json").write_text(json.dumps(doc))

    (out / "projects.json").write_text(json.dumps(
        [[p["name"], p["downloads"], (p.get("summary") or "")[:120]] for p in projects], separators=(",", ":")))

    upgrade = []
    for g in UPGRADE:
        items = []
        for n, note in g["items"]:
            top = n.split(".")[0]
            v = idx.get(top, {}).get(n)
            items.append({"name": n, "note": note, "count": v["total"] if v else 0,
                          "top": [r[0] for r in v["rows"][:5]] if v else []})
        upgrade.append({"title": g["title"], "items": items})
    (out / "upgrade.json").write_text(json.dumps(upgrade))

    third = [(t, c) for t, c in tops if t not in stdlib]
    std = [(t, c) for t, c in tops if t in stdlib]
    stats = {
        "projects_indexed": len(projects), "projects_failed": len(errors),
        "py_files": sum(p["py_files"] for p in projects),
        "py_failed": sum(p["py_failed"] for p in projects),
        "names": sum(len(v) for v in idx.values()),
        "bytes_fetched": sum(p.get("bytes_fetched", 0) for p in projects),
        "bytes_full_files": sum(p.get("file_size", 0) for p in projects),
        "top_third_party": third[:25], "top_stdlib": std[:25],
        "pth_with_import": sorted(p["name"] for p in projects if p["pth_with_import"]),
    }
    (out / "stats.json").write_text(json.dumps(stats))
    (out / "status.json").write_text(json.dumps({
        "built": datetime.now(timezone.utc).isoformat(timespec="minutes"),
        "projects_indexed": len(projects), "scope": f"top {len(projects) + len(errors)} projects by downloads (preview)"}))
    print(f"{len(projects)} projects, {len(errors)} errors, {stats['names']} names, {len(idx)} top-level modules")


if __name__ == "__main__":
    main()
