"""Validation: detect problems in the raw data and report them. Nothing is changed here.

1. validate_schema  -> fail fast (SchemaError) if a file lacks required columns.
2. run_checks       -> row-level quality checks; returns one result per check
                       (check, table, failed_rows, total_rows, severity) -> table validation_report.
"""
import re

from .cleaning import (clean_text, norm_id, normalize_branch, normalize_result, normalize_status,
                       parse_date, to_float, to_int, BRANCHES)

REQUIRED_COLUMNS = {
    "students": ["student_id", "name", "branch", "cgpa", "active_backlogs"],
    "companies": ["company_id", "company_name", "sector"],
    "drives": ["drive_id", "company_id", "drive_date", "role", "ctc_lpa", "min_cgpa",
               "max_backlogs", "allowed_branches"],
    "registrations": ["student_id", "drive_id", "registered_on"],
    "appearances": ["student_id", "drive_id", "status"],
    "selections": ["student_id", "drive_id", "result", "offered_ctc_lpa"],
}


class SchemaError(Exception):
    """Raised when raw input does not have the structure the pipeline needs."""


def validate_schema(raw):
    problems = []
    for table, cols in REQUIRED_COLUMNS.items():
        missing = [c for c in cols if c not in raw[table].columns]
        if missing:
            problems.append(f"{table}: missing columns {missing}")
        elif raw[table].empty:
            problems.append(f"{table}: file has no rows")
    if problems:
        raise SchemaError("; ".join(problems))


def run_checks(raw):
    results = []

    def add(table, check, failed, total, severity="warning"):
        results.append({"check_name": check, "table_name": table, "failed_rows": int(failed),
                        "total_rows": int(total), "severity": severity})

    def dup_count(keys):
        keys = [k for k in keys if None not in k]
        return len(keys) - len(set(keys))

    # students
    s = raw["students"].to_dict("records")
    ids = [norm_id(r["student_id"]) for r in s]
    add("students", "missing_student_id", sum(i is None for i in ids), len(s), "error")
    add("students", "duplicate_student_id", dup_count([(i,) for i in ids]), len(s))
    add("students", "missing_branch", sum(clean_text(r["branch"]) is None for r in s), len(s), "error")
    add("students", "unknown_branch",
        sum(clean_text(r["branch"]) is not None and normalize_branch(r["branch"]) is None for r in s), len(s), "error")
    add("students", "inconsistent_branch_label",
        sum(normalize_branch(r["branch"]) not in (None, clean_text(r["branch"])) for r in s), len(s))
    add("students", "missing_cgpa", sum(clean_text(r["cgpa"]) is None for r in s), len(s))
    add("students", "invalid_cgpa",
        sum(clean_text(r["cgpa"]) is not None and not (to_float(r["cgpa"]) is not None and 0 <= to_float(r["cgpa"]) <= 10)
            for r in s), len(s))
    add("students", "missing_backlogs", sum(clean_text(r["active_backlogs"]) is None for r in s), len(s))
    add("students", "invalid_backlogs",
        sum(clean_text(r["active_backlogs"]) is not None and not (to_int(r["active_backlogs"]) is not None and to_int(r["active_backlogs"]) >= 0)
            for r in s), len(s))
    student_ids = {i for i in ids if i}

    # companies
    c = raw["companies"].to_dict("records")
    add("companies", "duplicate_company_id", dup_count([(norm_id(r["company_id"]),) for r in c]), len(c))
    add("companies", "missing_company_name", sum(clean_text(r["company_name"]) is None for r in c), len(c), "error")
    company_ids = {norm_id(r["company_id"]) for r in c}

    # drives
    d = raw["drives"].to_dict("records")
    # a drive is usable only if its date, eligibility rules and company are valid
    drive_date = {norm_id(r["drive_id"]): parse_date(r["drive_date"]) for r in d
                  if parse_date(r["drive_date"]) and norm_id(r["company_id"]) in company_ids
                  and to_float(r["min_cgpa"]) is not None}
    add("drives", "invalid_drive_date", sum(parse_date(r["drive_date"]) is None for r in d), len(d), "error")
    add("drives", "missing_or_invalid_min_cgpa",
        sum(to_float(r["min_cgpa"]) is None or not 0 <= to_float(r["min_cgpa"]) <= 10 for r in d), len(d), "error")
    add("drives", "unknown_company", sum(norm_id(r["company_id"]) not in company_ids for r in d), len(d), "error")
    add("drives", "inconsistent_branch_label_in_allowed_list",
        sum(any(t.strip() not in BRANCHES for t in re.split(r"[|;,]", r["allowed_branches"]))
            for r in d), len(d))

    # registrations
    r_ = raw["registrations"].to_dict("records")
    pairs = [(norm_id(r["student_id"]), norm_id(r["drive_id"])) for r in r_]
    add("registrations", "duplicate_registration", dup_count(pairs), len(r_))
    add("registrations", "orphan_student_or_drive",
        sum(p[0] not in student_ids or p[1] not in drive_date for p in pairs), len(r_), "error")
    add("registrations", "unparsable_date",
        sum(clean_text(r["registered_on"]) is not None and parse_date(r["registered_on"]) is None for r in r_), len(r_))
    add("registrations", "registered_after_drive_date",
        sum((parse_date(r["registered_on"]) or "") > (drive_date.get(p[1]) or "9") for r, p in zip(r_, pairs)
            if parse_date(r["registered_on"])), len(r_), "error")
    registered = set(pairs)

    # appearances
    a = raw["appearances"].to_dict("records")
    apairs = [(norm_id(r["student_id"]), norm_id(r["drive_id"])) for r in a]
    add("appearances", "unknown_status", sum(normalize_status(r["status"]) is None for r in a), len(a), "error")
    add("appearances", "inconsistent_status_label",
        sum(normalize_status(r["status"]) not in (None, clean_text(r["status"])) for r in a), len(a))
    add("appearances", "appearance_without_registration", sum(p not in registered for p in apairs), len(a), "error")
    add("appearances", "duplicate_appearance", dup_count(apairs), len(a))
    present = {p for p, r in zip(apairs, a) if normalize_status(r["status"]) == "PRESENT"}

    # selections
    x = raw["selections"].to_dict("records")
    xpairs = [(norm_id(r["student_id"]), norm_id(r["drive_id"])) for r in x]
    add("selections", "unknown_result", sum(normalize_result(r["result"]) is None for r in x), len(x), "error")
    add("selections", "inconsistent_result_label",
        sum(normalize_result(r["result"]) not in (None, clean_text(r["result"])) for r in x), len(x))
    add("selections", "result_without_present_appearance", sum(p not in present for p in xpairs), len(x), "error")
    add("selections", "invalid_ctc",
        sum(clean_text(r["offered_ctc_lpa"]) is not None and not (to_float(r["offered_ctc_lpa"]) or 0) > 0 for r in x), len(x))
    add("selections", "ctc_on_non_selected",
        sum(normalize_result(r["result"]) not in (None, "SELECTED") and (to_float(r["offered_ctc_lpa"]) or 0) > 0
            for r in x), len(x), "error")
    add("selections", "duplicate_selection", dup_count(xpairs), len(x))
    return results
