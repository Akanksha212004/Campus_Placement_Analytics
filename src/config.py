"""Paths and environment-driven settings (see .env.example)."""
import os
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:  # python-dotenv is optional; plain environment variables still work
    pass

ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = ROOT / "data" / "raw"
SQL_DIR = ROOT / "sql"
DB_PATH = Path(os.getenv("DB_PATH", ROOT / "data" / "placement.db"))

NL_BACKEND = os.getenv("NL_BACKEND", "auto")            # auto | claude | rules
NL_MIN_CONFIDENCE = float(os.getenv("NL_MIN_CONFIDENCE", "0.6"))
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-haiku-5-5")
