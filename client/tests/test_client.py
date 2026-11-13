"""Offline tests: a fake index served from memory, laid out like data.py-code.org/api."""
import io
import json
import sys
import unittest
import zlib
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from py_code_org import api, cli, mcp, queries  # noqa: E402

IDX, PROJ = 16, 32


def crc(s):
    return zlib.crc32(s.encode())


def fake_files():
    rows = [["big-user", "1.0", 5000, 2, ["a.py:1"]], ["small-user", "2.0", 10, 1, ["b.py:3"]]]
    files = {
        "/manifest.json": {"idx_shards": IDX, "project_shards": PROJ, "big": {"bigmod": 2}, "row_cap": 2},
        "/status.json": {"built": "2026-11-13T13:11+00:00"},
        f"/idx/{crc('requests') % IDX:03x}.json": {"requests": {
            "requests.Session": {"total": 3, "rows": rows},
        }},
        f"/names/{crc('requests') % IDX:03x}.json": {"requests": [
            ["requests", 10], ["requests.Session", 3], ["requests.Session.get", 2], ["requests.Session.get.x", 1]]},
        f"/big/bigmod/{crc('bigmod.thing') % 2}.json": {"bigmod.thing": {"total": 1, "rows": rows[:1]}},
        f"/project/{crc('my-pkg') % PROJ:04x}.json": {"my-pkg": {
            "key": "my-pkg", "name": "My_Pkg", "version": "1.2", "summary": "s", "downloads": 7,
            "file": "my_pkg-1.2.whl", "upload_time": "2026-01-02T00:00:00Z", "requires_python": ">=3.9",
            "py_files": 3, "py_failed": 0, "own_modules": ["my_pkg"], "pth_with_import": [],
            "imports": {"stdlib": ["os"], "third_party": ["requests"]}, "upgrade_hits": [],
            "used_by_module": {"my_pkg": 4}, "primary_module": "my_pkg", "used_by": 4}},
    }
    return files


class FakeIndex(api.Index):
    def __init__(self):
        super().__init__(base="https://example.invalid/api")
        self.files = fake_files()

    def get(self, path):
        return self.files.get(path)


class Lookups(unittest.TestCase):
    def setUp(self):
        self.index = FakeIndex()

    def test_crc_matches_the_site(self):
        # Values computed by the crc32 function in the site's app.js.
        self.assertEqual(api.crc32("requests"), 2072368721)
        self.assertEqual(api.crc32("numpy.linalg.norm"), 2828900854)
        self.assertEqual(api.crc32("café.Ünïcode"), 3598506173)
        self.assertEqual(api.norm("My_Pkg.Name"), "my-pkg-name")

    def test_who_uses_small_module(self):
        r = queries.who_uses(self.index, "requests.Session")
        self.assertEqual(r["total"], 3)
        self.assertEqual([p["project"] for p in r["packages"]], ["big-user", "small-user"])
        self.assertEqual(r["narrower_names"], [{"name": "requests.Session.get", "projects": 2}])

    def test_who_uses_big_module(self):
        self.assertEqual(queries.who_uses(self.index, "bigmod.thing")["total"], 1)

    def test_min_downloads_and_exactness(self):
        r = queries.who_uses(self.index, "requests.Session", min_downloads=100)
        self.assertEqual(r["matching"], 1)
        self.assertTrue(r["matching_exact"])  # last listed row is below the threshold
        r = queries.who_uses(self.index, "requests.Session", min_downloads=1)
        self.assertFalse(r["matching_exact"])  # capped list, every row above the threshold

    def test_not_found_suggests_case(self):
        with self.assertRaises(queries.NotFound) as cm:
            queries.who_uses(self.index, "requests.session")
        self.assertIn("requests.Session", cm.exception.suggestions)

    def test_project_by_any_spelling(self):
        self.assertEqual(queries.project_imports(self.index, "MY.pkg")["project"], "My_Pkg")
        with self.assertRaises(queries.NotFound):
            queries.project_imports(self.index, "nope")


class Cli(unittest.TestCase):
    def run_cli(self, *argv):
        out = io.StringIO()
        orig = cli.Index
        cli.Index = FakeIndex
        try:
            with redirect_stdout(out):
                code = cli.main(list(argv))
        finally:
            cli.Index = orig
        return code, out.getvalue()

    def test_table_and_json(self):
        code, out = self.run_cli("who-uses", "requests.Session", "--limit", "1")
        self.assertEqual(code, 0)
        self.assertIn("3 packages use requests.Session", out)
        self.assertIn("Showing 1 of 3", out)
        code, out = self.run_cli("project", "my-pkg", "--json")
        self.assertEqual(json.loads(out)["third_party_imports"], ["requests"])

    def test_not_found_exit_code(self):
        code, out = self.run_cli("who-uses", "requests.session", "--json")
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(out)["did_you_mean"], ["requests.Session"])


class Mcp(unittest.TestCase):
    def talk(self, *msgs):
        orig = mcp.Index
        mcp.Index = FakeIndex
        out = io.StringIO()
        try:
            mcp.serve(io.StringIO("\n".join(json.dumps(m) for m in msgs) + "\n"), out)
        finally:
            mcp.Index = orig
        return [json.loads(line) for line in out.getvalue().splitlines()]

    def test_session(self):
        replies = self.talk(
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "who_uses", "arguments": {"name": "requests.Session"}}},
            {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "project_imports", "arguments": {"project": "zzz"}}},
            {"jsonrpc": "2.0", "id": 5, "method": "unknown"},
        )
        self.assertEqual([r["id"] for r in replies], [1, 2, 3, 4, 5])
        self.assertEqual(replies[0]["result"]["protocolVersion"], "2025-06-18")
        self.assertEqual({t["name"] for t in replies[1]["result"]["tools"]}, {"who_uses", "project_imports", "module_names"})
        self.assertEqual(replies[2]["result"]["structuredContent"]["total"], 3)
        self.assertTrue(replies[3]["result"]["isError"])
        self.assertEqual(replies[4]["error"]["code"], -32601)


if __name__ == "__main__":
    unittest.main()
