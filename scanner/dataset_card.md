---
license: cc-by-4.0
pretty_name: py-code.org PyPI code usage
language:
- en
tags:
- python
- pypi
- code
- dependencies
size_categories:
- 10M<n<100M
configs:
- config_name: refs
  default: true
  data_files: refs.parquet
- config_name: projects
  data_files: projects.parquet
- config_name: own_modules
  data_files: own_modules.parquet
- config_name: pth_files
  data_files: pth_files.parquet
---

# py-code.org: which PyPI packages use which names

For the latest release of {projects} PyPI projects (every project released between {since} and
{built}, plus the most downloaded projects even when their latest release is older), these
tables list every module, function and class the code imports or reaches by attribute,
such as `requests.Session` or `numpy.linalg.norm`. This is the data behind the search on
[py-code.org](https://py-code.org). The scanner that built it is
[pypi-data/py-code](https://github.com/pypi-data/py-code) (MIT).

The tables hold names, counts and file paths. They hold no source code.

## Tables

### `refs` ({refs_rows} rows, {refs_size})

One row per project and name. Sorted by `name`, then `project`.

| Column | Type | Meaning |
|---|---|---|
| `project` | string | PyPI project name |
| `name` | string | Full dotted name, e.g. `requests.Session` |
| `module` | string | Top-level module of `name`, e.g. `requests` |
| `files` | int | Number of `.py` files in the project that use `name` |
| `locations` | list of string | Up to 3 places where it is used, as `path:line` |

A name is recorded with every prefix: `os.path.join` also adds `os` and `os.path`. Attribute
chains are followed up to 4 levels after the imported name. Names from the project's own
top-level modules are left out, so a package does not count as a user of itself.

### `projects` ({projects_rows} rows, {projects_size})

| Column | Type | Meaning |
|---|---|---|
| `project` | string | PyPI project name |
| `version` | string | Latest release at scan time |
| `upload_time` | timestamp | Upload time of the scanned file (UTC) |
| `requires_python` | string | `Requires-Python` of the release, if set |
| `summary` | string | PyPI summary, cut at 300 characters |
| `file` | string | File that was scanned |
| `file_size` | int | Size of that file in bytes |
| `bytes_fetched` | int | Bytes the scanner downloaded to read the `.py` files |
| `py_files` | int | `.py` files in the file |
| `py_failed` | int | `.py` files that do not parse as current Python (mostly Python 2) |
| `downloads_30d` | int | Downloads in the 30 days before the scan, for the top 60,000 projects; 0 for the rest |

### `own_modules` ({own_modules_rows} rows)

Top-level modules each project provides, from its file layout. Use it to go from an import
name to a project: `PIL` to `pillow`, `yaml` to `PyYAML`.

### `pth_files` ({pth_files_rows} rows)

`.pth` files that contain an `import` line. Python runs these at every start-up.

## How it was built

1. List every project whose latest event is newer than the cut-off date (PyPI Simple API and
   XML-RPC changelog), plus the 60,000 most downloaded projects.
2. For each one, take the latest release and pick one file: a pure-Python wheel, then a
   platform wheel, then the source distribution. Wheels are read with HTTP range requests,
   so only the `.py` and `.pth` files are downloaded.
3. Parse each `.py` file with Python's `ast` module and record imports and attribute access
   on imported names.
4. Add 30-day download counts from ClickHouse's public PyPI dataset.

{py_files} `.py` files were parsed. The index was last rebuilt on {built}. The scanner's README
has the details and the measured run times.

## Known limits

- Only the latest release of each project. Projects with no release in the window are not
  included unless they are among the 60,000 most downloaded.
- Dynamic imports (`importlib.import_module`, `__import__`, `getattr`) are missed.
- Names reached through variables (`x = np; x.foo`) are missed.
- Files that do not parse as current Python are counted in `py_failed` but not indexed.
- Projects whose authors asked to be left out are not included.

## Example queries

Which packages use `requests.Session`, by downloads:

```sql
SELECT r.project, p.downloads_30d
FROM refs r JOIN projects p USING (project)
WHERE r.name = 'requests.Session'
ORDER BY p.downloads_30d DESC
LIMIT 20;
```

Which packages still import `distutils`:

```sql
SELECT count(DISTINCT project) FROM refs WHERE module = 'distutils';
```

With DuckDB, without downloading the files first:

```sql
SELECT count(DISTINCT project)
FROM 'hf://datasets/pypi-data/py-code/refs.parquet'
WHERE name = 'httpx.AsyncClient';
```

## Removal

If you maintain a package and want it left out, email dev@py-code.org from an address listed
on its PyPI page. It is removed at the next update.

## Credit

From 2023 to 2026 py-code.org hosted [Tom Forbes](https://tomforb.es)' PyPI Data project. This
dataset is a new, independent project on the same domain and does not reuse its data.
