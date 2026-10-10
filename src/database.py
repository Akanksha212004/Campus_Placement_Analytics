"""SQLite helpers: build the database from sql/schema.sql, load tables, open connections."""
import sqlite3
from pathlib import Path

from .config import SQL_DIR


def create_database(db_path):
    """Create a fresh database (an existing file is replaced) from sql/schema.sql."""
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()
    conn = sqlite3.connect(db_path)
    conn.executescript((SQL_DIR / "schema.sql").read_text())
    return conn


def load_table(conn, name, df):
    cols = list(df.columns)
    sql = f"INSERT INTO {name} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})"
    conn.executemany(sql, df.itertuples(index=False, name=None))


def load_all(conn, tables):
    """Load in dependency order so foreign keys are satisfied."""
    for name in ["dim_branch", "dim_company", "dim_student", "dim_drive", "bridge_drive_branch",
                 "fact_registration", "fact_appearance", "fact_selection"]:
        load_table(conn, name, tables[name])
    conn.commit()


def connect_readonly(db_path):
    """Read-only connection used by analytics and the NL feature: writes are impossible."""
    conn = sqlite3.connect(f"file:{Path(db_path).resolve()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn
