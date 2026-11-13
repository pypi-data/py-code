"""Read the static py-code.org index from data.py-code.org.

The index is a set of JSON files sharded by CRC32 (zlib) of a name, so a lookup is two small
GET requests: the manifest, then the shard that holds the name.
"""
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
import zlib

from . import __version__

BASE = os.environ.get("PY_CODE_API", "https://data.py-code.org/api").rstrip("/")
USER_AGENT = f"py-code-org/{__version__} (+https://py-code.org)"


class ApiError(Exception):
    pass


class Index:
    def __init__(self, base=BASE, timeout=30):
        self.base = base
        self.timeout = timeout
        self._cache = {}

    def get(self, path):
        """Fetch one JSON file. Returns None when it does not exist."""
        if path in self._cache:
            return self._cache[path]
        req = urllib.request.Request(self.base + path, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                data = json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                data = None
            else:
                raise ApiError(f"{self.base}{path}: HTTP {e.code}") from e
        except (urllib.error.URLError, OSError) as e:
            raise ApiError(f"could not reach {self.base}: {getattr(e, 'reason', e)}") from e
        self._cache[path] = data
        return data

    @property
    def manifest(self):
        m = self.get("/manifest.json")
        if not m:
            raise ApiError(f"{self.base}/manifest.json is missing")
        return m

    def name_shard(self, name):
        """Path of the shard file that holds a dotted name such as requests.Session."""
        m, top = self.manifest, name.split(".")[0]
        if top in m["big"]:
            return f"/big/{urllib.parse.quote(top)}/{crc32(name) % m['big'][top]}.json"
        return f"/idx/{crc32(top) % m['idx_shards']:03x}.json"

    def who_uses(self, name):
        """{'total': int, 'rows': [[project, version, downloads_30d, files, [path:line, ...]], ...]} or None.

        rows hold at most manifest['row_cap'] projects, sorted by downloads; total is the true count.
        """
        top = name.split(".")[0]
        d = self.get(self.name_shard(name)) or {}
        return d.get(name) if top in self.manifest["big"] else (d.get(top) or {}).get(name)

    def names(self, module):
        """[[name, projects], ...] for every indexed name under a top-level module, most used first."""
        m = self.manifest
        d = self.get(f"/names/{crc32(module) % m['idx_shards']:03x}.json") or {}
        return d.get(module) or []

    def project(self, name):
        """One project's document: version, summary, downloads, imports, own modules, users. None if not indexed."""
        k = norm(name)
        d = self.get(f"/project/{crc32(k) % self.manifest['project_shards']:04x}.json") or {}
        return d.get(k)

    def status(self):
        return self.get("/status.json") or {}


def crc32(s):
    return zlib.crc32(s.encode("utf-8"))


def norm(name):
    """PEP 503 normalized project name."""
    return re.sub(r"[-_.]+", "-", name).lower()
