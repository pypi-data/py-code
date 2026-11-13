"""Aggregate the project shards into api/nerd.json for the "Stats for nerds" page.

Usage: python3 scanner/build_nerd_stats.py dist/api
"""
import collections, glob, json, os, re, sys

api = sys.argv[1] if len(sys.argv) > 1 else "dist/api"
docs = []
for path in glob.glob(os.path.join(api, "project", "*.json")):
    with open(path) as f:
        docs.extend(json.load(f).values())

n = len(docs)
kind = collections.Counter()
for d in docs:
    f = d["file"]
    if f.endswith(".whl"):
        kind["Pure-Python wheel" if f.endswith("-none-any.whl") else "Platform wheel"] += 1
    else:
        kind["Source distribution"] += 1


def floor(spec):
    """Lowest Python 3 minor version a requires_python spec allows, e.g. '>=3.9' -> '3.9'."""
    if not spec:
        return "Not set"
    m = re.findall(r">=?\s*3\.(\d+)", spec) or re.findall(r"~=\s*3\.(\d+)", spec) or re.findall(r"==\s*3\.(\d+)", spec)
    if not m:
        return "Other"
    minor = min(int(x) for x in m)
    return f"3.{minor}" if minor >= 6 else "3.5 or older"


req = collections.Counter(floor(d.get("requires_python")) for d in docs)
months = collections.Counter((d.get("upload_time") or "")[:7] for d in docs if d.get("upload_time"))

def bucket(k):
    for lim, label in [(1, "1"), (5, "2–5"), (20, "6–20"), (100, "21–100"), (500, "101–500"), (2000, "501–2,000")]:
        if k <= lim:
            return label
    return "More than 2,000"

sizes = collections.Counter(bucket(d["py_files"]) for d in docs if d["py_files"])

FEATURES = [("typing", "stdlib"), ("dataclasses", "stdlib"), ("asyncio", "stdlib"), ("__future__", "stdlib"),
            ("pathlib", "stdlib"), ("argparse", "stdlib"), ("logging", "stdlib"), ("unittest", "stdlib"),
            ("pydantic", "third_party"), ("attr", "third_party"), ("click", "third_party"), ("typer", "third_party"),
            ("pytest", "third_party"), ("six", "third_party")]
feat = []
for mod, group in FEATURES:
    c = sum(1 for d in docs if mod in d["imports"].get(group, []))
    feat.append([mod, c])
feat.sort(key=lambda x: -x[1])

largest = sorted(docs, key=lambda d: -d["py_files"])[:15]
most_imports = sorted(docs, key=lambda d: -len(d["imports"].get("third_party", [])))[:15]
best = {}
for d in docs:
    m = d.get("primary_module")
    if m and m not in sys.stdlib_module_names and d["downloads"] > best.get(m, {"downloads": -1})["downloads"]:
        best[m] = d
most_used = sorted(best.values(), key=lambda d: -d.get("used_by", 0))[:20]
upgrade = sum(1 for d in docs if d.get("upgrade_hits"))
no_third = sum(1 for d in docs if not d["imports"].get("third_party"))

out = {
    "projects": n,
    "kind": kind.most_common(),
    "requires_python": sorted(req.items(), key=lambda kv: (kv[0][0] != "3", [int(x) if x.isdigit() else 0 for x in re.findall(r"\d+", kv[0])])),
    "months": sorted(months.items())[-13:],
    "sizes": [[k, sizes[k]] for k in ["1", "2–5", "6–20", "21–100", "101–500", "501–2,000", "More than 2,000"]],
    "features": feat,
    "largest": [[d["name"], d["py_files"]] for d in largest],
    "most_imports": [[d["name"], len(d["imports"]["third_party"])] for d in most_imports],
    "most_used": [[d["name"], d["primary_module"], d["used_by"], d["downloads"]] for d in most_used],
    "with_upgrade_hits": upgrade,
    "no_third_party": no_third,
}
with open(os.path.join(api, "nerd.json"), "w") as f:
    json.dump(out, f, separators=(",", ":"), ensure_ascii=False)
print(json.dumps(out, ensure_ascii=False)[:3000])
