# py-code-org

Find which PyPI packages use a module, function or class, from the command line or from an AI
coding tool. It reads the index behind [py-code.org](https://py-code.org), which covers the latest
release of 335,146 PyPI projects released in the last year and records every name their code
imports or reaches by attribute.

Use it before you rename or remove something in a library, to see who calls it and where.

```
$ uvx py-code-org who-uses httpx.AsyncClient --limit 3
26,709 packages use httpx.AsyncClient (index built 2026-11-13)

#  Package                  Version  Downloads/mo  Files  First use
1  google-genai             3.2.0          124.9M      8  google/genai/_api_client.py:735
2  google-cloud-aiplatform  2.9.0           70.8M      3  agentplatform/_genai/_runtimes_utils.py:1810
3  sentry-sdk               2.75.0          66.7M      1  sentry_sdk/integrations/httpx.py:28

Showing 3 of 26,709. --limit 0 shows the 2,000 most downloaded.

Narrower names: httpx.AsyncClient.send (179), httpx.AsyncClient.__init__ (57), ...
```

It has no dependencies and needs Python 3.9 or newer.

## Install

```sh
pipx install py-code-org     # or: uv tool install py-code-org
```

Or run it without installing: `uvx py-code-org who-uses requests.Session`.

## Commands

| Command | What it shows |
|---|---|
| `py-code who-uses NAME` | Packages that import or call `NAME`, most downloaded first, with the file and line of the first uses |
| `py-code project NAME` | What a PyPI project imports, the modules it provides, and how many packages import it |
| `py-code names MODULE` | Every indexed name under a top-level module and how many packages use each |
| `py-code mcp` | Run as an MCP server over stdio |

Options:

- `who-uses --min-downloads N`: only packages with at least N downloads in the last 30 days.
- `who-uses --limit N`, `names --limit N`: rows to show; `0` shows all.
- `names --prefix numpy.linalg`: only names that start with the prefix.
- `--json` on every lookup command prints JSON for scripts.

Names are case-sensitive and written as the code imports them: `requests.Session`,
`numpy.linalg.norm`, `PIL.Image`. When a name is not found, the tool suggests the closest
indexed spelling. The exit code is 0 when found, 1 when not found and 2 when the index could not be
reached.

## Use it from an AI coding tool (MCP)

The `mcp` command gives AI coding tools three read-only tools:

- `who_uses(name, min_downloads, limit)`
- `project_imports(project)`
- `module_names(module, prefix, limit)`

Claude Code:

```sh
claude mcp add py-code -- uvx py-code-org mcp
```

Cursor, Claude Desktop and other tools that read an `mcpServers` JSON file:

```json
{
  "mcpServers": {
    "py-code": { "command": "uvx", "args": ["py-code-org", "mcp"] }
  }
}
```

Then ask, for example: "Which popular packages call `requests.Session.merge_environment_settings`?"

## Where the data comes from

Every lookup is two small requests to the static JSON files at `https://data.py-code.org/api`: a
manifest, then the shard that holds the name. Nothing is sent except the name you look up. The
index is rebuilt monthly. Set `PY_CODE_API` to read a copy hosted elsewhere.

What it misses: older releases, dynamic imports (`importlib.import_module`, `getattr`), names
passed through variables, and files that do not parse as current Python. For bulk queries, the
full tables are on Hugging Face as Parquet:
[pypi-data/py-code](https://huggingface.co/datasets/pypi-data/py-code).

The scanner that builds the index is in the same repository:
[pypi-data/py-code](https://github.com/pypi-data/py-code).

## Licence

MIT.
