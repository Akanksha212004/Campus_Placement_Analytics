"""Ingestion: read raw CSVs exactly as they are.

Everything is read as text. Nothing is coerced here, so a bad value like "N/A" or
"8.5x" reaches validation/cleaning unchanged instead of silently becoming NaN.
"""
from pathlib import Path

import pandas as pd

RAW_FILES = ["students", "companies", "drives", "registrations", "appearances", "selections"]


def read_raw(raw_dir) -> dict:
    raw_dir = Path(raw_dir)
    tables = {}
    for name in RAW_FILES:
        path = raw_dir / f"{name}.csv"
        if not path.exists():
            raise FileNotFoundError(f"Missing raw file: {path} (run `python data/generate_data.py`)")
        tables[name] = pd.read_csv(path, dtype=str, keep_default_na=False)
    return tables
