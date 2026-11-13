# py-code.org

A "who uses this?" search over the code inside PyPI packages. For the latest release of every
project released in the last year, the scanner records which modules, functions and classes the
code imports and calls. The output is static JSON, so it can be hosted for free.

This repo holds the scanner. The pages of py-code.org live in a separate repo and read this
data from `https://data.py-code.org/api/`.

From 2023 to 2026 this domain hosted Tom Forbes' PyPI Data project. This is a new, independent
project and does not reuse its code or data.

## Rebuild the index

Requirements: Python 3.11 or newer, about 10 GB of free disk, and an internet connection.

```sh
python3 -m pip install -r requirements.txt
scanner/build.sh
```

`build.sh` runs five steps. Each one logs to `data/build-<step>.log` and can be rerun, because
finished work is skipped.

| Step | Script | Source | Output |
|---|---|---|---|
| 1. List projects | `scanner/project_list.py` | PyPI Simple API and XML-RPC changelog | `data/candidates.tsv` |
| 2. Scan code | `scanner/scan.py` | PyPI JSON API and files.pythonhosted.org | `data/projects/<name>.json` |
| 3. Download counts | `scanner/downloads.py` | ClickHouse public PyPI demo | `data/downloads.tsv` |
| 4. Build index | `scanner/build_index.py` | steps 2 and 3 | `dist/api/` |
| 5. Stats page data | `scanner/build_nerd_stats.py` | `dist/api/project/` | `dist/api/nerd.json` |

Publish the result to data.py-code.org (Cloudflare Pages project `py-code-data`; unchanged
files are skipped, so a refresh uploads only what changed):

```sh
scanner/deploy_data.sh
```

Monthly refresh: `scanner/build.sh refresh` lists the projects that changed in the last 31 days,
re-scans them, and rebuilds the index. Projects whose latest release is older than the window
(`DAYS`, default 365) are left out of the index.

### How each step works

1. **Project list.** The Simple API lists every project with the serial number of its newest
   event. A binary search over `changelog_since_serial` finds the serial at the cut-off date, and
   every project with a newer serial is a candidate. Some candidates only had a yank or a metadata
   change, so step 2 checks the upload date of the latest release (`--since`).
2. **Scan.** For each project, take the latest release and pick one file: a pure-Python wheel,
   then a platform wheel, then the source distribution. Wheels are read with HTTP range requests,
   so only the zip index and the `.py` and `.pth` files are downloaded. Source distributions up to
   200 MB are downloaded in full. Each file is parsed with `ast`: imports, and attribute chains on
   imported names up to four levels deep, with the first file and line of each.
3. **Downloads.** 30-day download counts for the top `TOP` projects (default 60,000). The demo
   server allows 10,000 rows per query and 60,000 rows an hour, so the script pages through and
   waits for the hourly reset. Projects outside the top `TOP` rank with 0 downloads.
4. **Index.** DuckDB aggregates the scan results on disk, and the builder writes JSON files
   sharded by CRC32 of the name: 4,096 search shards, 8,192 project shards, and extra parts for very
   common modules. At most 2,000 users are stored per name, plus the true total. The browser
   computes the same CRC32 to find the right file.

### Measured on the first run (13 Nov 2026)

- 196,568 projects scanned in 6.6 hours with 8 parallel downloads on one home PC.
- 45 GB downloaded, against 261 GB for the full files.
- Index build: 173 seconds, 17,167 files, 1.6 GB (about 330 MB gzipped).

The first run's project list came from ClickHouse and missed about 150,000 projects. A second
scan of the 155,885 projects from `project_list.py` that the first run had not covered took 5.0
hours: 5,157 had no release in the window and 3,693 failed. After both runs:

- 335,146 projects indexed with `--since 2025-11-13` (2,344 more had a latest release older than
  the window), 12.4 million Python files parsed, 4.16 million distinct names.
- 73 GB downloaded in total, against 447 GB for the full files.
- Index build: 290 seconds, 2.4 GB.

## Known limits

- Only the latest release of each project is indexed.
- Dynamic imports (`importlib.import_module`, `getattr`) and names passed through variables are missed.
- Files that do not parse as current Python, mostly Python 2 code, are counted but not indexed.
- The ClickHouse demo is a free public service; if it changes, step 3 needs another source of
  download counts, such as PyPI's BigQuery dataset.

## Layout

```
scanner/            the pipeline above
scanner/history/    one-off scripts from the first run, kept for the build-log articles
dist/               the search data (api/ plus _headers); generated and not committed
data/               scan results and lists; generated and not committed
```

`scanner/history/` holds the first run's project list, which came from ClickHouse's
`pypi.projects` table. That table turned out to miss about 150,000 projects released in the last
year, so `project_list.py` replaced it.
