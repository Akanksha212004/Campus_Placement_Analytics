"""Transformation: shape cleaned data into the tables defined in sql/schema.sql.

Selects/orders the final columns, enforces types (ints stay ints, NaN -> None) and checks
referential integrity before anything is loaded.
"""
import pandas as pd

from .cleaning import BRANCHES

COLUMNS = {
    "dim_branch": ["branch_code", "branch_name"],
    "dim_company": ["company_id", "company_name", "sector"],
    "dim_student": ["student_id", "student_name", "branch_code", "cgpa", "active_backlogs"],
    "dim_drive": ["drive_id", "company_id", "drive_date", "role", "ctc_lpa", "min_cgpa", "max_backlogs"],
    "bridge_drive_branch": ["drive_id", "branch_code"],
    "fact_registration": ["student_id", "drive_id", "registered_on"],
    "fact_appearance": ["student_id", "drive_id", "status"],
    "fact_selection": ["student_id", "drive_id", "result", "offered_ctc_lpa"],
}


def _to_python(df):
    """Replace NaN/NA with None so SQLite stores NULL; keep whole numbers as int."""
    df = df.astype(object)
    return df.where(pd.notna(df), None)


def build_tables(clean):
    tables = {
        "dim_branch": pd.DataFrame(list(BRANCHES.items()), columns=COLUMNS["dim_branch"]),
        "dim_company": clean["companies"],
        "dim_student": clean["students"],
        "dim_drive": clean["drives"],
        "bridge_drive_branch": clean["drive_branches"],
        "fact_registration": clean["registrations"],
        "fact_appearance": clean["appearances"],
        "fact_selection": clean["selections"],
    }
    out = {}
    for name, cols in COLUMNS.items():
        df = tables[name][cols].copy()
        if name == "dim_student":
            df["active_backlogs"] = df["active_backlogs"].astype("Int64")
        if name == "dim_drive":
            df["max_backlogs"] = df["max_backlogs"].astype("Int64")
        out[name] = _to_python(df)
    check_integrity(out)
    return out


def check_integrity(t):
    """Fail early (with a clear message) if a foreign key would be violated at load time."""
    def keys(name, *cols):
        return set(zip(*(t[name][c] for c in cols)))

    checks = [
        ("dim_student.branch_code", set(t["dim_student"]["branch_code"]) - set(t["dim_branch"]["branch_code"])),
        ("dim_drive.company_id", set(t["dim_drive"]["company_id"]) - set(t["dim_company"]["company_id"])),
        ("fact_registration.student_id", set(t["fact_registration"]["student_id"]) - set(t["dim_student"]["student_id"])),
        ("fact_registration.drive_id", set(t["fact_registration"]["drive_id"]) - set(t["dim_drive"]["drive_id"])),
        ("fact_appearance", keys("fact_appearance", "student_id", "drive_id") - keys("fact_registration", "student_id", "drive_id")),
        ("fact_selection", keys("fact_selection", "student_id", "drive_id") - keys("fact_appearance", "student_id", "drive_id")),
    ]
    for name, orphans in checks:
        if orphans:
            raise ValueError(f"Referential integrity broken in {name}: {sorted(orphans)[:5]}")
