"""Test 1-3: the pipeline detects, repairs and enforces data quality."""
import shutil
import sqlite3

import pytest

from src.cleaning import clean_all, normalize_branch, parse_date, to_float
from src.ingestion import read_raw
from src.validation import SchemaError, run_checks, validate_schema


def test_validation_detects_every_injected_problem_type(built):
    failed = {c["check_name"] for c in run_checks(built["raw"]) if c["failed_rows"] > 0}
    expected = {"duplicate_student_id", "missing_cgpa", "invalid_cgpa", "inconsistent_branch_label",
                "inconsistent_status_label", "appearance_without_registration",
                "result_without_present_appearance", "registered_after_drive_date", "duplicate_registration"}
    assert expected <= failed


def test_cleaning_normalises_values():
    assert {normalize_branch(x) for x in ["CSE", "cse", "C.S.E", "Computer Science "]} == {"CSE"}
    assert normalize_branch("Aeronautical") is None
    assert parse_date("2025-09-01") == parse_date("01/09/2025") == parse_date("01-Sep-2025") == "2025-09-01"
    assert parse_date("31/02/2025") is None            # impossible date
    assert to_float("N/A") is None and to_float("8,5") == 8.5


def test_cleaned_data_has_no_duplicates_or_logical_violations(built):
    clean, log = clean_all(built["raw"])
    for name, keys in [("students", ["student_id"]), ("registrations", ["student_id", "drive_id"]),
                       ("appearances", ["student_id", "drive_id"]), ("selections", ["student_id", "drive_id"])]:
        assert not clean[name].duplicated(keys).any(), name
    assert clean["students"]["cgpa"].dropna().between(0, 10).all()
    assert (clean["students"]["active_backlogs"].dropna() >= 0).all()
    assert len(log.rows) > 0                            # every repair was logged


def test_database_enforces_integrity(built):
    conn = sqlite3.connect(built["db"])
    conn.execute("PRAGMA foreign_keys = ON")
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    # impossible combinations are rejected by the schema itself
    with pytest.raises(sqlite3.IntegrityError):         # appearance without registration
        conn.execute("INSERT INTO fact_appearance VALUES ('NOPE', 'D001', 'PRESENT')")
    with pytest.raises(sqlite3.IntegrityError):         # CTC on a rejection
        conn.execute("INSERT INTO fact_selection VALUES ('X', 'Y', 'REJECTED', 5.0)")
    with pytest.raises(sqlite3.IntegrityError):         # CGPA out of range
        conn.execute("INSERT INTO dim_student VALUES ('Z1', 'z', 'CSE', 11.0, 0)")
    conn.close()


def test_missing_required_column_stops_the_pipeline(built, tmp_path):
    """Failure/edge case: structurally broken input must fail fast, not load partial data."""
    raw_copy = tmp_path / "raw"
    shutil.copytree(built["raw_dir"], raw_copy)
    f = raw_copy / "selections.csv"
    f.write_text(f.read_text().replace("result", "outcome", 1))
    with pytest.raises(SchemaError, match="selections: missing columns"):
        validate_schema(read_raw(raw_copy))
