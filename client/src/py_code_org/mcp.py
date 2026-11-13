"""A minimal MCP server over stdio (newline-delimited JSON-RPC 2.0), with no dependencies.

Tools: who_uses, project_imports, module_names. Each result is JSON text, plus structuredContent.
"""
import json
import sys

from . import __version__
from .api import ApiError, Index
from .queries import NotFound, module_names, project_imports, who_uses

PROTOCOL_VERSIONS = ["2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05"]

INSTRUCTIONS = (
    "py-code.org indexes the code inside the latest release of every PyPI project released in the last "
    "year (about 335,000 projects) and records which modules, functions and classes each one imports or "
    "reaches by attribute. Use who_uses before changing or removing a public name in a library, to see "
    "which packages call it and where. Names are case-sensitive and fully qualified as imported, e.g. "
    "requests.Session or numpy.linalg.norm. If a name is not found, use module_names to find the exact spelling."
)

TOOLS = [
    {
        "name": "who_uses",
        "title": "Who uses this name on PyPI",
        "description": (
            "List the PyPI packages whose latest release imports or calls a module, function or class, "
            "most downloaded first, with the file and line of the first uses. Returns the true total, "
            "names one level deeper, and suggestions when the name is not indexed."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Dotted name as imported, case-sensitive, e.g. requests.Session"},
                "min_downloads": {"type": "integer", "minimum": 0, "default": 0, "description": "Only packages with at least this many downloads in 30 days"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 500, "default": 20, "description": "Packages to return"},
            },
            "required": ["name"],
        },
        "annotations": {"readOnlyHint": True, "openWorldHint": True},
    },
    {
        "name": "project_imports",
        "title": "What a PyPI project imports",
        "description": (
            "For one PyPI project: its latest release, Requires-Python, downloads, the top-level modules it "
            "provides, the third-party and standard-library modules it imports, how many indexed packages "
            "import it, and names it uses that recent Python or NumPy versions removed or deprecated."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"project": {"type": "string", "description": "PyPI project name, e.g. fastapi"}},
            "required": ["project"],
        },
        "annotations": {"readOnlyHint": True, "openWorldHint": True},
    },
    {
        "name": "module_names",
        "title": "Names used under a module",
        "description": (
            "List the indexed names under a top-level module with the number of packages that use each, "
            "most used first. Use it to find the exact spelling for who_uses or to see which parts of a "
            "library people actually call."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "module": {"type": "string", "description": "Top-level import name, e.g. numpy"},
                "prefix": {"type": "string", "default": "", "description": "Only names starting with this, e.g. numpy.linalg"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 1000, "default": 50},
            },
            "required": ["module"],
        },
        "annotations": {"readOnlyHint": True, "openWorldHint": True},
    },
]


def call_tool(index, name, args):
    if name == "who_uses":
        return who_uses(index, args["name"], int(args.get("min_downloads") or 0), min(int(args.get("limit") or 20), 500))
    if name == "project_imports":
        return project_imports(index, args["project"])
    if name == "module_names":
        return module_names(index, args["module"], args.get("prefix") or "", min(int(args.get("limit") or 50), 1000))
    raise KeyError(name)


def tool_result(data, error=False):
    return {"content": [{"type": "text", "text": json.dumps(data, indent=1)}], "structuredContent": data, "isError": error}


def handle(index, msg):
    """Return the response for one JSON-RPC message, or None for notifications."""
    method, mid, params = msg.get("method"), msg.get("id"), msg.get("params") or {}
    if mid is None:
        return None
    ok = lambda result: {"jsonrpc": "2.0", "id": mid, "result": result}  # noqa: E731
    err = lambda code, text: {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": text}}  # noqa: E731

    if method == "initialize":
        asked = params.get("protocolVersion")
        return ok({
            "protocolVersion": asked if asked in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0],
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "py-code", "title": "py-code.org", "version": __version__},
            "instructions": INSTRUCTIONS,
        })
    if method == "ping":
        return ok({})
    if method == "tools/list":
        return ok({"tools": TOOLS})
    if method == "tools/call":
        name, args = params.get("name"), params.get("arguments") or {}
        if name not in {t["name"] for t in TOOLS}:
            return err(-32602, f"Unknown tool: {name}")
        try:
            return ok(tool_result(call_tool(index, name, args)))
        except NotFound as e:
            return ok(tool_result({"error": str(e), "did_you_mean": e.suggestions}, error=True))
        except (KeyError, TypeError, ValueError) as e:
            return ok(tool_result({"error": f"Missing or bad argument: {e}"}, error=True))
        except ApiError as e:
            return ok(tool_result({"error": str(e)}, error=True))
    return err(-32601, f"Method not found: {method}")


def serve(stdin=sys.stdin, stdout=sys.stdout):
    index = Index()
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            resp = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}
        else:
            resp = handle(index, msg) if isinstance(msg, dict) else {
                "jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Invalid request"}}
        if resp is not None:
            stdout.write(json.dumps(resp) + "\n")
            stdout.flush()
    return 0
