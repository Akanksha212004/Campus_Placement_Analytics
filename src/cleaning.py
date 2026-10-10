"""Cleaning: fix what can be fixed safely, NULL what cannot be trusted, drop what is unusable.

Every action is written to an IssueLog (-> table data_quality_issues), so nothing
disappears silently. The small parse/normalise helpers are also reused by validation.py.
"""
import math
import re
from datetime import datetime

import pandas as pd

BRANCHES = {
    "CSE": "Computer Science and Engineering",
    "IT": "Information Technology",
    "ECE": "Electronics and Communication Engineering",
    "EEE": "Electrical and Electronics Engineering",
    "MECH": "Mechanical Engineering",
    "CIVIL": "Civil Engineering",
}
_BRANCH_NAMES = {
    "CSE": ["cse", "cs", "computer science", "computer science and engineering", "comp sci"],
    "IT": ["it", "information technology"],
    "ECE": ["ece", "electronics", "electronics and communication",
            "electronics and communication engineering"],
    "EEE": ["eee", "electrical", "electrical and electronics", "electrical and electronics engineering"],
    "MECH": ["mech", "mechanical", "mechanical engineering"],
    "CIVIL": ["civil", "civil engineering"],
}
MISSING_TOKENS = {"", "na", "n/a", "nan", "null", "none", "-", "?"}
STATUS_MAP = {"present": "PRESENT", "appeared": "PRESENT", "attended": "PRESENT", "y": "PRESENT",
              "absent": "ABSENT", "no show": "ABSENT", "noshow": "ABSENT", "n": "ABSENT"}
RESULT_MAP = {"selected": "SELECTED", "placed": "SELECTED", "offered": "SELECTED",
              "rejected": "REJECTED", "not selected": "REJECTED",
              "waitlisted": "WAITLISTED", "waitlist": "WAITLISTED"}
SECTOR_MAP = {"it services": "IT Services", "information technology services": "IT Services",
              "consulting": "Consulting", "product": "Product", "product company": "Product",
              "engineering": "Engineering", "manufacturing": "Manufacturing"}
DATE_FORMATS = ["%Y-%m-%d", "%d/%m/%Y", "%d-%b-%Y", "%d-%m-%Y"]  # non-ISO formats are day-first


def _key(text):
    """Lower-case, drop dots, '&' -> 'and', collapse spaces: 'C.S.E ' -> 'cse'."""
    return re.sub(r"\s+", " ", text.lower().replace(".", "").replace("&", "and")).strip()


BRANCH_ALIAS_TO_CODE = {_key(a): code for code, names in _BRANCH_NAMES.items() for a in names}


# ---- small helpers (also used by validation.py) -------------------------------------------
def clean_text(v):
    if v is None:
        return None
    s = re.sub(r"\s+", " ", str(v)).strip()
    return None if s.lower() in MISSING_TOKENS else s


def norm_id(v):
    s = clean_text(v)
    return s.replace(" ", "").upper() if s else None


def to_float(v):
    s = clean_text(v)
    if s is None:
        return None
    try:
        x = float(s.replace(",", "."))
    except ValueError:
        return None
    return x if math.isfinite(x) else None


def to_int(v):
    x = to_float(v)
    return int(x) if x is not None and x == int(x) else None


def parse_date(v):
    """Return an ISO date string or None. Impossible dates (31/02) are rejected."""
    s = clean_text(v)
    if s is None:
        return None
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def normalize_branch(v):
    s = clean_text(v)
    return BRANCH_ALIAS_TO_CODE.get(_key(s)) if s else None


def normalize_status(v):
    s = clean_text(v)
    return STATUS_MAP.get(s.lower()) if s else None


def normalize_result(v):
    s = clean_text(v)
    return RESULT_MAP.get(s.lower()) if s else None


# ---- issue log + dedupe ---------------------------------------------------------------------
class IssueLog:
    def __init__(self):
        self.rows = []

    def add(self, table, record_key, issue, action):
        self.rows.append({"table_name": table, "record_key": record_key, "issue": issue, "action": action})

    def frame(self):
        return pd.DataFrame(self.rows, columns=["table_name", "record_key", "issue", "action"])


def _nulls(row):
    return sum(v is None for v in row.values())


def dedupe(rows, key_cols, table, log, better=None):
    """Keep one row per key. Default: the row with fewer NULLs (first one wins ties)."""
    better = better or (lambda new, old: _nulls(new) < _nulls(old))
    kept = {}
    for r in rows:
        k = tuple(r[c] for c in key_cols)
        if k not in kept:
            kept[k] = r
            continue
        if better(r, kept[k]):
            kept[k] = r
        log.add(table, "|".join(map(str, k)), "duplicate key", "kept one row")
    return list(kept.values())


# ---- table cleaners -------------------------------------------------------------------------
def clean_companies(df, log):
    rows = []
    for r in df.to_dict("records"):
        cid, name = norm_id(r["company_id"]), clean_text(r["company_name"])
        if cid is None or name is None:
            log.add("companies", cid, "missing company_id/name", "dropped row")
            continue
        sector = SECTOR_MAP.get((clean_text(r["sector"]) or "").lower(), "Other")
        rows.append({"company_id": cid, "company_name": name, "sector": sector})
    return pd.DataFrame(dedupe(rows, ["company_id"], "companies", log))


def clean_students(df, log):
    rows = []
    for r in df.to_dict("records"):
        sid = norm_id(r["student_id"])
        if sid is None:
            log.add("students", None, "missing student_id", "dropped row")
            continue
        cgpa = to_float(r["cgpa"])
        if clean_text(r["cgpa"]) is not None and (cgpa is None or not 0 <= cgpa <= 10):
            log.add("students", sid, f"invalid cgpa {r['cgpa']!r}", "set to NULL")
            cgpa = None
        backlogs = to_int(r["active_backlogs"])
        if clean_text(r["active_backlogs"]) is not None and (backlogs is None or backlogs < 0):
            log.add("students", sid, f"invalid backlogs {r['active_backlogs']!r}", "set to NULL")
            backlogs = None
        rows.append({"student_id": sid, "student_name": clean_text(r["name"]),
                     "branch_code": normalize_branch(r["branch"]), "cgpa": cgpa,
                     "active_backlogs": backlogs})
    rows = dedupe(rows, ["student_id"], "students", log)
    good = []
    for r in rows:  # branch is needed for eligibility, so a student without one is unusable
        if r["branch_code"] is None:
            log.add("students", r["student_id"], "missing/unknown branch", "dropped row")
        else:
            good.append(r)
    return pd.DataFrame(good)


def clean_drives(df, companies, log):
    """Returns (drives, drive_branches). A drive missing any eligibility rule is dropped."""
    known_companies = set(companies["company_id"])
    drives, bridge = [], []
    for r in df.to_dict("records"):
        did, cid = norm_id(r["drive_id"]), norm_id(r["company_id"])
        date = parse_date(r["drive_date"])
        min_cgpa, max_bl = to_float(r["min_cgpa"]), to_int(r["max_backlogs"])
        ctc = to_float(r["ctc_lpa"])
        branches = sorted({normalize_branch(t) for t in re.split(r"[|;,]", r["allowed_branches"])} - {None})
        problem = None
        if did is None:
            problem = "missing drive_id"
        elif cid not in known_companies:
            problem = f"unknown company {cid}"
        elif date is None:
            problem = f"invalid drive_date {r['drive_date']!r}"
        elif min_cgpa is None or not 0 <= min_cgpa <= 10:
            problem = "missing/invalid min_cgpa"
        elif max_bl is None or max_bl < 0:
            problem = "missing/invalid max_backlogs"
        elif not branches:
            problem = "no valid allowed branches"
        if problem:
            log.add("drives", did, problem, "dropped drive")
            continue
        if ctc is not None and ctc <= 0:
            log.add("drives", did, "non-positive ctc", "set to NULL")
            ctc = None
        drives.append({"drive_id": did, "company_id": cid, "drive_date": date, "role": clean_text(r["role"]),
                       "ctc_lpa": ctc, "min_cgpa": min_cgpa, "max_backlogs": max_bl})
        bridge += [{"drive_id": did, "branch_code": b} for b in branches]
    drives = dedupe(drives, ["drive_id"], "drives", log)
    return pd.DataFrame(drives), pd.DataFrame(bridge).drop_duplicates()


def clean_registrations(df, students, drives, log):
    drive_date = dict(zip(drives["drive_id"], drives["drive_date"]))
    known_students = set(students["student_id"])
    rows = []
    for r in df.to_dict("records"):
        sid, did = norm_id(r["student_id"]), norm_id(r["drive_id"])
        key = f"{sid}|{did}"
        if sid not in known_students or did not in drive_date:
            log.add("registrations", key, "student or drive not in clean dimensions", "dropped row")
            continue
        on = parse_date(r["registered_on"])
        if on is None and clean_text(r["registered_on"]) is not None:
            log.add("registrations", key, f"unparsable date {r['registered_on']!r}", "set to NULL")
        if on is not None and on > drive_date[did]:
            log.add("registrations", key, "registered after the drive date", "dropped row")
            continue
        rows.append({"student_id": sid, "drive_id": did, "registered_on": on})
    # prefer a row that has a date; between two dated rows keep the earlier one
    def better(new, old):
        if (new["registered_on"] is None) != (old["registered_on"] is None):
            return new["registered_on"] is not None
        return (new["registered_on"] or "") < (old["registered_on"] or "")
    return pd.DataFrame(dedupe(rows, ["student_id", "drive_id"], "registrations", log, better))


def clean_appearances(df, registrations, log):
    registered = set(zip(registrations["student_id"], registrations["drive_id"]))
    rows = []
    for r in df.to_dict("records"):
        sid, did = norm_id(r["student_id"]), norm_id(r["drive_id"])
        key = f"{sid}|{did}"
        status = normalize_status(r["status"])
        if status is None:
            log.add("appearances", key, f"unknown status {r['status']!r}", "dropped row")
        elif (sid, did) not in registered:
            log.add("appearances", key, "appearance without registration", "dropped row")
        else:
            rows.append({"student_id": sid, "drive_id": did, "status": status})
    return pd.DataFrame(dedupe(rows, ["student_id", "drive_id"], "appearances", log))


def clean_selections(df, appearances, log):
    present = {(s, d) for s, d, st in zip(appearances["student_id"], appearances["drive_id"],
                                           appearances["status"]) if st == "PRESENT"}
    rows = []
    for r in df.to_dict("records"):
        sid, did = norm_id(r["student_id"]), norm_id(r["drive_id"])
        key = f"{sid}|{did}"
        result = normalize_result(r["result"])
        if result is None:
            log.add("selections", key, f"unknown result {r['result']!r}", "dropped row")
            continue
        if (sid, did) not in present:
            log.add("selections", key, "result for a student who did not appear", "dropped row")
            continue
        ctc = to_float(r["offered_ctc_lpa"])
        if clean_text(r["offered_ctc_lpa"]) is not None and (ctc is None or ctc <= 0):
            log.add("selections", key, f"invalid ctc {r['offered_ctc_lpa']!r}", "set to NULL")
            ctc = None
        if result != "SELECTED" and ctc is not None:
            log.add("selections", key, f"ctc given for {result}", "set to NULL")
            ctc = None
        rows.append({"student_id": sid, "drive_id": did, "result": result, "offered_ctc_lpa": ctc})
    return pd.DataFrame(dedupe(rows, ["student_id", "drive_id"], "selections", log))


def clean_all(raw):
    """Run all cleaners in dependency order. Returns (clean_tables, IssueLog)."""
    log = IssueLog()
    companies = clean_companies(raw["companies"], log)
    students = clean_students(raw["students"], log)
    drives, drive_branches = clean_drives(raw["drives"], companies, log)
    registrations = clean_registrations(raw["registrations"], students, drives, log)
    appearances = clean_appearances(raw["appearances"], registrations, log)
    selections = clean_selections(raw["selections"], appearances, log)
    return {"companies": companies, "students": students, "drives": drives,
            "drive_branches": drive_branches, "registrations": registrations,
            "appearances": appearances, "selections": selections}, log
