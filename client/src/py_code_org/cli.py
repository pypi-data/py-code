"""py-code: which PyPI packages use a module, function or class.

  py-code who-uses requests.Session
  py-code project fastapi
  py-code names httpx
  py-code mcp            run as an MCP server over stdio
"""
import argparse
import json
import sys

from . import __version__
from .api import ApiError, Index
from .queries import NotFound, module_names, project_imports, who_uses


def human(n):
    for size, unit in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if n >= size:
            return f"{n / size:.1f}{unit}"
    return str(n)


def table(header, rows, right=()):
    widths = [max(len(str(x)) for x in col) for col in zip(header, *rows)]
    fmt = lambda row: "  ".join(  # noqa: E731
        str(x).rjust(w) if i in right else str(x).ljust(w) for i, (x, w) in enumerate(zip(row, widths))
    ).rstrip()
    return "\n".join([fmt(header)] + [fmt(r) for r in rows])


def print_who_uses(r):
    n = r["total"]
    print(f"{n:,} package{'s' if n != 1 else ''} use {r['name']}" + (f" (index built {r['index_built'][:10]})" if r["index_built"] else ""))
    if r["min_downloads"]:
        at_least = "" if r["matching_exact"] else "At least "
        print(f"{at_least}{r['matching']:,} of them have {human(r['min_downloads'])} or more downloads a month")
    if r["packages"]:
        print()
        rows = [
            (i, p["project"], p["version"], human(p["downloads_30d"]), p["files"], p["locations"][0] if p["locations"] else "")
            for i, p in enumerate(r["packages"], 1)
        ]
        print(table(("#", "Package", "Version", "Downloads/mo", "Files", "First use"), rows, right=(0, 3, 4)))
    of = r["matching"] if r["min_downloads"] and r["matching_exact"] else r["total"]
    if len(r["packages"]) < r["matching"]:
        print(f"\nShowing {len(r['packages']):,} of {of:,}. --limit 0 shows " + (
            f"all {r['matching']:,}." if r["matching"] == of else f"the {r['matching']:,} most downloaded."))
    elif len(r["packages"]) < of:
        print(f"\nThe index lists the {r['listed']:,} most downloaded of the {r['total']:,}.")
    if r["narrower_names"]:
        print("\nNarrower names: " + ", ".join(f"{x['name']} ({x['projects']:,})" for x in r["narrower_names"]))
    print(f"\n{r['url']}")


def print_project(p):
    print(f"{p['project']} {p['version']}" + (f": {p['summary']}" if p["summary"] else ""))
    lines = [
        ("Downloads", f"{human(p['downloads_30d'])} in the last 30 days"),
        ("Released", p["released"] or "unknown"),
        ("Requires Python", p["requires_python"] or "not set"),
        ("Indexed file", f"{p['file']} ({p['py_files']} .py files" + (f", {p['py_failed']} did not parse)" if p["py_failed"] else ")")),
        ("Its modules", ", ".join(p["own_modules"]) or "none"),
        ("Used by", ", ".join(f"{n:,} packages import {m}" for m, n in p["used_by"].items()) or "no indexed package"),
        ("Third-party imports", ", ".join(p["third_party_imports"]) or "none"),
        ("Stdlib imports", ", ".join(p["stdlib_imports"]) or "none"),
    ]
    if p["removed_or_deprecated_names"]:
        lines.append(("Removed or deprecated", ", ".join(p["removed_or_deprecated_names"])))
    if p["pth_files_with_import"]:
        lines.append((".pth with import", ", ".join(p["pth_files_with_import"])))
    w = max(len(k) for k, _ in lines)
    print()
    for k, v in lines:
        print(f"{k.ljust(w)}  {v}")
    print(f"\n{p['url']}")


def print_names(r):
    capped = " (the index keeps the 5,000 most used names per module)" if r["count"] >= 5000 and not r["prefix"] else ""
    print(f"{r['count']:,} indexed names under {r['prefix'] or r['module']}{capped}\n")
    print(table(("Name", "Packages"), [(x["name"], f"{x['projects']:,}") for x in r["names"]], right=(1,)))
    if r["count"] > len(r["names"]):
        print(f"\n{r['count'] - len(r['names']):,} more. Use --limit 0 to list all.")


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="py-code",
        description="Which PyPI packages use a module, function or class. Data from https://py-code.org.",
    )
    ap.add_argument("--version", action="version", version=f"py-code-org {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True, metavar="command")

    p = sub.add_parser("who-uses", help="packages that import or call a name, e.g. requests.Session")
    p.add_argument("name", help="dotted name, case-sensitive: a module, function or class")
    p.add_argument("--min-downloads", type=int, default=0, metavar="N", help="only packages with at least N downloads a month")
    p.add_argument("--limit", type=int, default=20, help="rows to show, 0 for all (default 20)")
    p.add_argument("--json", action="store_true", help="print JSON")

    p = sub.add_parser("project", help="what a PyPI project imports and who uses it")
    p.add_argument("name", help="PyPI project name")
    p.add_argument("--json", action="store_true", help="print JSON")

    p = sub.add_parser("names", help="indexed names under a top-level module, most used first")
    p.add_argument("module", help="top-level module, e.g. numpy")
    p.add_argument("--prefix", default="", help="only names that start with this, e.g. numpy.linalg")
    p.add_argument("--limit", type=int, default=50, help="rows to show, 0 for all (default 50)")
    p.add_argument("--json", action="store_true", help="print JSON")

    sub.add_parser("mcp", help="run an MCP server over stdio for AI coding tools")

    a = ap.parse_args(argv)
    if a.cmd == "mcp":
        from .mcp import serve

        return serve()

    index = Index()
    try:
        if a.cmd == "who-uses":
            r, show = who_uses(index, a.name, a.min_downloads, a.limit), print_who_uses
        elif a.cmd == "project":
            r, show = project_imports(index, a.name), print_project
        else:
            r, show = module_names(index, a.module, a.prefix, a.limit), print_names
    except NotFound as e:
        if a.json:
            print(json.dumps({"error": str(e), "did_you_mean": e.suggestions}, indent=2))
        else:
            print(e, file=sys.stderr)
            if e.suggestions:
                print("Did you mean: " + ", ".join(e.suggestions), file=sys.stderr)
        return 1
    except ApiError as e:
        print(f"py-code: {e}", file=sys.stderr)
        return 2
    if a.json:
        print(json.dumps(r, indent=2))
    else:
        show(r)
    return 0


if __name__ == "__main__":
    sys.exit(main())
