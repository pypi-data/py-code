"""Lookups shared by the command-line tool and the MCP server. Each returns plain JSON-ready dicts."""
import difflib
from urllib.parse import quote

from .api import Index


class NotFound(Exception):
    def __init__(self, message, suggestions=()):
        super().__init__(message)
        self.suggestions = list(suggestions)


def who_uses(index: Index, name, min_downloads=0, limit=20):
    name = name.strip()
    hit = index.who_uses(name)
    if not hit:
        raise NotFound(
            f"No indexed package references {name}. Names are matched exactly and are case-sensitive.",
            suggest_names(index, name),
        )
    rows = [r for r in hit["rows"] if r[2] >= min_downloads]
    shown = rows if not limit else rows[:limit]
    return {
        "name": name,
        "total": hit["total"],
        "listed": len(hit["rows"]),
        "matching": len(rows),
        # rows are capped and sorted by downloads, so the count is exact when the cap was not hit
        # or the last listed package is already below the threshold
        "matching_exact": hit["total"] == len(hit["rows"]) or (bool(hit["rows"]) and hit["rows"][-1][2] < min_downloads),
        "min_downloads": min_downloads,
        "packages": [
            {"project": r[0], "version": r[1], "downloads_30d": r[2], "files": r[3], "locations": r[4]}
            for r in shown
        ],
        "narrower_names": narrower(index, name),
        "url": f"https://py-code.org/search/?q={quote(name)}",
        "index_built": index.status().get("built"),
    }


def project_imports(index: Index, name):
    p = index.project(name.strip())
    if not p:
        raise NotFound(
            f"{name} is not in the index. It covers the latest release of PyPI projects released in the last year, plus the most downloaded projects."
        )
    return {
        "project": p["name"],
        "version": p["version"],
        "summary": p.get("summary") or "",
        "released": (p.get("upload_time") or "")[:10],
        "requires_python": p.get("requires_python"),
        "downloads_30d": p.get("downloads", 0),
        "file": p.get("file"),
        "py_files": p.get("py_files", 0),
        "py_failed": p.get("py_failed", 0),
        "own_modules": p.get("own_modules", []),
        "third_party_imports": p.get("imports", {}).get("third_party", []),
        "stdlib_imports": p.get("imports", {}).get("stdlib", []),
        "used_by": p.get("used_by_module", {}),
        "removed_or_deprecated_names": p.get("upgrade_hits", []),
        "pth_files_with_import": p.get("pth_with_import", []),
        "url": f"https://py-code.org/projects/view/?name={quote(p['key'])}",
    }


def module_names(index: Index, module, prefix="", limit=50):
    module = module.strip().split(".")[0]
    rows = [r for r in index.names(module) if not prefix or r[0].startswith(prefix)]
    if not rows:
        raise NotFound(f"No indexed package imports the module {module}.", suggest_modules(index, module))
    shown = rows if not limit else rows[:limit]
    return {"module": module, "prefix": prefix, "count": len(rows),
            "names": [{"name": n, "projects": c} for n, c in shown]}


def narrower(index, name, limit=15):
    """Names one level below `name`, e.g. requests.Session.get under requests.Session."""
    depth = name.count(".") + 1
    return [
        {"name": n, "projects": c}
        for n, c in index.names(name.split(".")[0])
        if n.startswith(name + ".") and n.count(".") == depth
    ][:limit]


def suggest_names(index, name, limit=5):
    """Indexed names that match ignoring case, else the closest spellings under the same module."""
    top = name.split(".")[0]
    names = [n for n, _ in index.names(top)]
    if not names:
        rest = name[len(top):]
        return [m + rest for m in suggest_modules(index, top) if index.who_uses(m + rest)] or suggest_modules(index, top)
    low = name.lower()
    same = [n for n in names if n.lower() == low]
    if same:
        return same[:limit]
    by_lower = {}
    for n in names:
        by_lower.setdefault(n.lower(), n)
    return [by_lower[n] for n in difflib.get_close_matches(low, list(by_lower), n=limit, cutoff=0.6)]


def suggest_modules(index, module):
    """Try common case variants of a top-level module name (Django vs django, PIL vs pil)."""
    out = []
    for v in dict.fromkeys([module.lower(), module.upper(), module.capitalize()]):
        if v != module and index.names(v):
            out.append(v)
    return out
