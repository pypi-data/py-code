"""Write the Hugging Face dataset card (README.md) for a Parquet export, with current numbers.

Usage:
  python3 scanner/dataset_card.py data/parquet --since 2025-11-13
"""
import argparse
from datetime import datetime, timezone
from pathlib import Path

import duckdb


def human_size(n):
    return f"{n / 1e9:.1f} GB" if n >= 1e9 else f"{n / 1e6:.0f} MB"


def rows_text(n):
    return f"{n / 1e6:.1f} million" if n >= 1e6 else f"{n:,}"


def day(s):
    return datetime.strptime(s, "%Y-%m-%d").strftime("%-d %b %Y")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dir")
    ap.add_argument("--since", required=True)
    a = ap.parse_args()
    d = Path(a.dir)
    con = duckdb.connect()
    count = lambda t: con.execute(f"SELECT count(*) FROM '{d}/{t}.parquet'").fetchone()[0]  # noqa: E731
    projects, py_files = con.execute(f"SELECT count(*), sum(py_files) FROM '{d}/projects.parquet'").fetchone()
    values = {
        "projects": f"{projects:,}",
        "since": day(a.since),
        "built": datetime.now(timezone.utc).strftime("%-d %b %Y"),
        "py_files": f"{py_files / 1e6:.1f} million",
        "refs_rows": rows_text(count("refs")),
        "refs_size": human_size((d / "refs.parquet").stat().st_size),
        "projects_rows": f"{projects:,}",
        "projects_size": human_size((d / "projects.parquet").stat().st_size),
        "own_modules_rows": f"{count('own_modules'):,}",
        "pth_files_rows": f"{count('pth_files'):,}",
    }
    template = Path(__file__).with_name("dataset_card.md").read_text()
    (d / "README.md").write_text(template.format_map(values))
    print(f"wrote {d / 'README.md'}")


if __name__ == "__main__":
    main()
