import pytest

from generate_data import generate
from src.main import run_pipeline
from src.ingestion import read_raw


@pytest.fixture(scope="session")
def built(tmp_path_factory):
    """Generate a small synthetic dataset and run the full pipeline once for all tests."""
    root = tmp_path_factory.mktemp("placement")
    generate(root / "raw", n_students=250, seed=7)
    db = root / "test.db"
    summary = run_pipeline(root / "raw", db)
    return {"raw_dir": root / "raw", "db": db, "summary": summary, "raw": read_raw(root / "raw")}
