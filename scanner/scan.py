"""Scan the latest release of PyPI projects and record which names each one references.

Usage: python3 scanner/scan.py data/candidates.tsv data/projects [--since YYYY-MM-DD] [--force] [--limit N]

For each project: pick one distribution file of the latest non-yanked release, fetch only its
.py (and .pth) files, parse them with ast, and write data/projects/<name>.json.
Already-scanned projects are skipped, so the scan can be resumed. --force re-scans every listed
project (for a monthly refresh, list only the projects that changed). With --since, a project whose
latest release is older is saved as {"skipped": ...} and left out of the index. --rescan-skipped
also re-scans listed projects whose saved result is skipped or an error (used, without --since,
for the most downloaded projects).
"""

import ast
import io
import os
import re
import json
import sys
import tarfile
import threading
import time
import warnings
import zipfile

warnings.filterwarnings("ignore", category=SyntaxWarning)  # old escape sequences in scanned code
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests

UA = "py-code.org-scanner/0.1 (preview; latest-release import index)"
MAX_TAR_BYTES = 200 * 1024 * 1024
MAX_PY_FILE = 5 * 1024 * 1024
WORKERS = int(os.environ.get("SCAN_WORKERS", "8"))  # parallel downloads
MAX_DEPTH = 4  # attribute levels followed after the imported name

local = threading.local()
SINCE = os.environ.get("SCAN_SINCE", "")  # set by --since; the env var is kept for older run scripts


def session():
    if not hasattr(local, "s"):
        s = requests.Session()
        s.headers["User-Agent"] = UA
        local.s = s
    return local.s


def get(url, **kw):
    for attempt in range(5):
        try:
            r = session().get(url, timeout=60, **kw)
        except requests.RequestException:
            time.sleep(2 ** attempt)
            continue
        if r.status_code in (429, 500, 502, 503, 504):
            time.sleep(int(r.headers.get("Retry-After", 2 ** attempt)))
            continue
        return r
    raise RuntimeError(f"giving up on {url}")


class RangeFile(io.RawIOBase):
    """Seekable read-only file over HTTP range requests, with a small block cache."""

    BLOCK = 256 * 1024

    def __init__(self, url, size):
        self.url, self.size, self.pos = url, size, 0
        self.cache = {}
        self.fetched = 0

    def seekable(self):
        return True

    def readable(self):
        return True

    def tell(self):
        return self.pos

    def seek(self, off, whence=0):
        self.pos = off if whence == 0 else self.pos + off if whence == 1 else self.size + off
        return self.pos

    def _block(self, i):
        if i not in self.cache:
            start = i * self.BLOCK
            end = min(start + self.BLOCK, self.size) - 1
            r = get(self.url, headers={"Range": f"bytes={start}-{end}"})
            if r.status_code != 206:
                raise RuntimeError(f"range not supported: {r.status_code}")
            self.cache[i] = r.content
            self.fetched += len(r.content)
            if len(self.cache) > 64:
                self.cache.pop(next(iter(self.cache)))
        return self.cache[i]

    def readinto(self, b):
        n = min(len(b), self.size - self.pos)
        if n <= 0:
            return 0
        out, pos, need = bytearray(), self.pos, n
        while need:
            blk = self._block(pos // self.BLOCK)
            o = pos % self.BLOCK
            chunk = blk[o : o + need]
            out += chunk
            pos += len(chunk)
            need -= len(chunk)
        b[:n] = out
        self.pos += n
        return n


def pick_file(urls):
    files = [u for u in urls if not u.get("yanked")]
    wheels = [u for u in files if u["filename"].endswith(".whl")]

    def score(u):
        f = u["filename"]
        if "-none-any.whl" in f:
            return 0
        s = 10
        if "manylinux" in f and "x86_64" in f:
            s = 1
        elif "x86_64" in f or "amd64" in f:
            s = 3
        if "cp312" in f or "cp313" in f or "abi3" in f:
            s -= 0.5
        return s

    if wheels:
        return sorted(wheels, key=score)[0]
    sd = [u for u in files if u["filename"].endswith((".tar.gz", ".zip", ".tgz"))]
    return sd[0] if sd else None


class Refs(ast.NodeVisitor):
    def __init__(self):
        self.alias = {}
        self.found = {}  # name -> first line

    def add(self, name, line):
        if name and name not in self.found:
            self.found[name] = line

    def add_prefixes(self, dotted, line):
        parts = dotted.split(".")
        for i in range(1, len(parts) + 1):
            self.add(".".join(parts[:i]), line)

    def visit_Import(self, node):
        for a in node.names:
            self.add_prefixes(a.name, node.lineno)
            if a.asname:
                self.alias[a.asname] = a.name
            else:
                top = a.name.split(".")[0]
                self.alias[top] = top
        self.generic_visit(node)

    def visit_ImportFrom(self, node):
        if node.level or not node.module:
            return
        self.add_prefixes(node.module, node.lineno)
        for a in node.names:
            if a.name == "*":
                continue
            full = f"{node.module}.{a.name}"
            self.add(full, node.lineno)
            self.alias[a.asname or a.name] = full
        self.generic_visit(node)

    def visit_Attribute(self, node):
        chain, cur = [], node
        while isinstance(cur, ast.Attribute):
            chain.append(cur.attr)
            cur = cur.value
        if isinstance(cur, ast.Name) and cur.id in self.alias:
            base = self.alias[cur.id]
            chain.reverse()
            for i in range(1, min(len(chain), MAX_DEPTH) + 1):
                self.add(base + "." + ".".join(chain[:i]), node.lineno)
            return  # inner Attribute nodes are covered by the prefixes above
        self.generic_visit(node)


def strip_root(path, is_sdist):
    if is_sdist and "/" in path:
        path = path.split("/", 1)[1]
    if path.startswith("src/"):
        path = path[4:]
    return path


def own_top_levels(paths):
    tops = set()
    for p in paths:
        if ".dist-info/" in p or ".egg-info/" in p or ".data/" in p:
            continue
        first = p.split("/")[0]
        if first.endswith(".py"):
            first = first[:-3]
        if first.isidentifier() and first not in ("tests", "test", "docs", "examples", "setup"):
            tops.add(first)
    return tops


def scan_project(name, known=None):
    """Scan the latest release of `name`. If `known` is (version, file) and PyPI's latest release
    still has that file, return {"unchanged": True} without downloading it."""
    r = get(f"https://pypi.org/pypi/{name}/json")
    if r.status_code != 200:
        return {"name": name, "error": f"json {r.status_code}"}
    meta = r.json()
    info = meta["info"]
    since = SINCE
    if since and max((u.get("upload_time_iso_8601") or "" for u in meta.get("urls", [])), default="") < since:
        return {"name": info["name"], "version": info["version"], "skipped": f"no release since {since}"}
    f = pick_file(meta.get("urls", []))
    if not f:
        return {"name": info["name"], "version": info["version"], "error": "no usable file"}
    fname, url, size = f["filename"], f["url"], f["size"]
    if known and known == (info["version"], fname):
        return {"unchanged": True}
    is_sdist = not fname.endswith(".whl")
    members = {}  # path -> bytes
    fetched = 0
    if fname.endswith((".whl", ".zip")):
        rf = RangeFile(url, size)
        with zipfile.ZipFile(io.BufferedReader(rf, buffer_size=RangeFile.BLOCK)) as z:
            for zi in z.infolist():
                if zi.filename.endswith((".py", ".pth")) and zi.file_size <= MAX_PY_FILE:
                    members[zi.filename] = z.read(zi)
        fetched = rf.fetched
    else:
        if size > MAX_TAR_BYTES:
            return {"name": info["name"], "version": info["version"], "error": "sdist too large"}
        data = get(url).content
        fetched = len(data)
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as t:
            for m in t.getmembers():
                if m.isfile() and m.name.endswith((".py", ".pth")) and m.size <= MAX_PY_FILE:
                    members[m.name] = t.extractfile(m).read()

    paths = {strip_root(p, is_sdist): p for p in members}
    own = own_top_levels(paths)
    refs, failed, pth = {}, 0, []
    py_count = 0
    for rel, orig in paths.items():
        src = members[orig]
        if rel.endswith(".pth"):
            text = src.decode("utf-8", "replace")
            if any(l.lstrip().startswith("import") for l in text.splitlines()):
                pth.append(rel)
            continue
        py_count += 1
        try:
            tree = ast.parse(src)
        except (SyntaxError, ValueError):
            failed += 1
            continue
        v = Refs()
        v.visit(tree)
        for n, line in v.found.items():
            if n.split(".")[0] in own:
                continue
            e = refs.setdefault(n, [0, []])
            e[0] += 1
            if len(e[1]) < 3:
                e[1].append(f"{rel}:{line}")
    return {
        "name": info["name"],
        "version": info["version"],
        "file": fname,
        "file_size": size,
        "bytes_fetched": fetched,
        "upload_time": f.get("upload_time_iso_8601"),
        "requires_python": info.get("requires_python"),
        "summary": info.get("summary"),
        "py_files": py_count,
        "py_failed": failed,
        "own_modules": sorted(own),
        "pth_with_import": pth,
        "refs": refs,
    }


def canonical(name):
    """PEP 503 normalized name, so each project is saved once (ab_engine -> ab-engine)."""
    return re.sub(r"[-_.]+", "-", name).lower()


def main():
    global SINCE
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("out")
    ap.add_argument("legacy_limit", nargs="?", type=int)
    ap.add_argument("--limit", type=int)
    ap.add_argument("--since", default=SINCE)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--changed", action="store_true",
                    help="re-check every listed project, but download only those whose latest file changed")
    ap.add_argument("--rescan-skipped", action="store_true")
    a = ap.parse_args()
    SINCE = a.since
    src, out = Path(a.src), Path(a.out)
    limit = a.limit or a.legacy_limit
    out.mkdir(parents=True, exist_ok=True)
    names = [l.split("\t")[0] for l in src.read_text().splitlines() if l.strip()][:limit]
    names = list(dict.fromkeys(canonical(n) for n in names))
    removed_file = Path(__file__).with_name("removed.txt")
    removed = {canonical(l.split("#", 1)[0].strip()) for l in removed_file.read_text().splitlines()
               if l.split("#", 1)[0].strip()} if removed_file.exists() else set()
    names = [n for n in names if n not in removed]  # authors asked to be left out
    def usable(n):
        f = out / f"{n}.json"
        if not f.exists():
            return False
        if not a.rescan_skipped:
            return True
        try:
            d = json.loads(f.read_text())
        except ValueError:
            return False
        return "skipped" not in d and "error" not in d

    def known(n):
        """(version, file) of a stored result, for --changed."""
        try:
            d = json.loads((out / f"{n}.json").read_text())
        except (OSError, ValueError):
            return None
        return None if "error" in d or "skipped" in d else (d.get("version"), d.get("file"))

    todo = names if a.force or a.changed else [n for n in names if not usable(n)]
    print(f"{len(names)} projects, {len(todo)} to scan", flush=True)
    t0, done, errs, skipped, unchanged = time.time(), 0, 0, 0, 0
    with ThreadPoolExecutor(WORKERS) as ex:
        futs = {ex.submit(scan_project, n, known(n) if a.changed else None): n for n in todo}
        for fu in as_completed(futs):
            n = futs[fu]
            try:
                res = fu.result()
            except Exception as e:  # keep going; record the failure
                res = {"name": n, "error": repr(e)[:300]}
            done += 1
            if "unchanged" in res:
                unchanged += 1
                continue
            if "error" in res:
                errs += 1
            elif "skipped" in res:
                skipped += 1
            (out / f"{n}.json").write_text(json.dumps(res))
            if done % 100 == 0:
                print(f"{done}/{len(todo)} in {time.time()-t0:.0f}s, errors {errs}", flush=True)
    print(f"done {done} in {time.time()-t0:.0f}s, errors {errs}, skipped {skipped}, unchanged {unchanged}", flush=True)


if __name__ == "__main__":
    main()
