"""Synthetic campus-placement data generator.

All students and events are randomly generated (seeded, so the output is reproducible).
Company names are only labels. The generator first builds *correct* data, then
deliberately corrupts it so the pipeline has real problems to find:

  duplicates, missing values, non-numeric / out-of-range values, inconsistent labels,
  mixed date formats, orphan keys and logically impossible event sequences.

Usage:  python data/generate_data.py [--out data/raw] [--students 600] [--seed 42]
"""
import argparse
import csv
import random
from datetime import date, timedelta
from pathlib import Path

BRANCH_VARIANTS = {
    "CSE": ["CSE", "cse", "Computer Science", "C.S.E", "Comp Sci"],
    "IT": ["IT", "it", "Information Technology", "I.T."],
    "ECE": ["ECE", "ece", "Electronics", "Electronics & Communication"],
    "EEE": ["EEE", "eee", "Electrical", "Electrical and Electronics"],
    "MECH": ["MECH", "Mech", "Mechanical", "Mechanical Engineering"],
    "CIVIL": ["CIVIL", "Civil", "Civil Engineering"],
}
BRANCH_WEIGHTS = {"CSE": 25, "IT": 15, "ECE": 20, "EEE": 15, "MECH": 15, "CIVIL": 10}

# (company_id, name, sector, kind) -- "it" companies hire software branches, "core" hire core branches
COMPANIES = [
    ("C001", "TCS", "IT Services", "it"), ("C002", "Infosys", "IT Services", "it"),
    ("C003", "Wipro", "IT Services", "it"), ("C004", "Accenture", "Consulting", "it"),
    ("C005", "Cognizant", "IT Services", "it"), ("C006", "Capgemini", "Consulting", "it"),
    ("C007", "Amazon", "Product", "product"), ("C008", "Microsoft", "Product", "product"),
    ("C009", "Larsen & Toubro", "Engineering", "core"), ("C010", "Bosch", "Manufacturing", "core"),
    ("C011", "Tata Motors", "Manufacturing", "core"), ("C012", "Deloitte", "Consulting", "it"),
]
SECTOR_VARIANTS = {"IT Services": ["it services", "IT services", "Information Technology Services"],
                   "Consulting": ["consulting"], "Product": ["product", "Product Company"],
                   "Engineering": ["engineering"], "Manufacturing": ["manufacturing"]}
ALLOWED = {"it": ["CSE", "IT", "ECE", "EEE"], "product": ["CSE", "IT", "ECE"],
           "core": ["MECH", "CIVIL", "EEE", "ECE"]}
ROLES = {"it": "Software Engineer", "product": "SDE", "core": "Graduate Engineer Trainee"}


def fmt_date(rng, d):
    """Render a date in one of three formats (day-first when not ISO)."""
    r = rng.random()
    if r < 0.70:
        return d.strftime("%Y-%m-%d")
    if r < 0.90:
        return d.strftime("%d/%m/%Y")
    return d.strftime("%d-%b-%Y")


def messy_id(rng, value):
    r = rng.random()
    if r < 0.01:
        return " " + value.lower()
    if r < 0.02:
        return value + " "
    return value


def write_csv(path, header, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def generate(out_dir, n_students=600, seed=42):
    rng = random.Random(seed)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    # ---- 1. clean "truth" ---------------------------------------------------
    students = []
    for i in range(1, n_students + 1):
        branch = rng.choices(list(BRANCH_WEIGHTS), weights=list(BRANCH_WEIGHTS.values()))[0]
        cgpa = round(min(9.9, max(4.5, rng.gauss(7.2, 1.0))), 2)
        backlogs = 0 if rng.random() < 0.7 else rng.randint(1, 4)
        students.append({"id": f"S{i:04d}", "name": f"Student {i:04d}", "branch": branch,
                         "cgpa": cgpa, "backlogs": backlogs})

    drives = []
    n = 0
    for idx, (cid, _, _, kind) in enumerate(COMPANIES):
        for _ in range(2 if idx % 2 == 0 else 1):  # 6 companies run two drives
            n += 1
            drives.append({
                "id": f"D{n:03d}", "company_id": cid, "kind": kind,
                "date": date(2025, 8, 10) + timedelta(days=rng.randint(0, 150)),
                "role": ROLES[kind], "ctc": round(rng.uniform(3.5, 12.0) if kind != "product" else rng.uniform(15, 35), 2),
                "min_cgpa": rng.choice([6.0, 6.5, 7.0, 7.5, 8.0]),
                "max_backlogs": rng.choice([0, 0, 1, 2]),
                "branches": ALLOWED[kind],
            })

    regs, apps, sels = [], [], []
    for d in drives:
        for s in students:
            eligible = (s["cgpa"] >= d["min_cgpa"] and s["backlogs"] <= d["max_backlogs"]
                        and s["branch"] in d["branches"])
            p_reg = min(0.95, 0.45 + (s["cgpa"] - 6) * 0.15) if eligible else 0.02
            if rng.random() >= p_reg:
                continue
            regs.append({"s": s["id"], "d": d["id"], "on": d["date"] - timedelta(days=rng.randint(1, 14))})
            present = rng.random() < 0.82
            apps.append({"s": s["id"], "d": d["id"], "status": "PRESENT" if present else "ABSENT"})
            if present:
                r = rng.random()
                p_sel = min(0.5, max(0.05, 0.08 + (s["cgpa"] - 6) * 0.08))
                if r < p_sel:
                    sels.append({"s": s["id"], "d": d["id"], "res": "SELECTED",
                                 "ctc": round(d["ctc"] * rng.uniform(0.95, 1.1), 2)})
                elif r < p_sel + 0.05:
                    sels.append({"s": s["id"], "d": d["id"], "res": "WAITLISTED", "ctc": None})
                else:
                    sels.append({"s": s["id"], "d": d["id"], "res": "REJECTED", "ctc": None})

    drive_date = {d["id"]: d["date"] for d in drives}

    # ---- 2. corrupt: students ----------------------------------------------
    rows = []
    for s in students:
        branch = s["branch"]
        r = rng.random()
        if r < 0.01:
            branch = ""                                   # missing branch
        elif r < 0.04:
            branch = rng.choice(["Aeronautical", "??"])   # unknown branch
        elif r < 0.45:
            branch = rng.choice(BRANCH_VARIANTS[branch])  # inconsistent label
        cgpa = s["cgpa"]
        r = rng.random()
        if r < 0.02:
            cgpa = ""                                     # missing
        elif r < 0.03:
            cgpa = "N/A"                                  # invalid type
        elif r < 0.04:
            cgpa = rng.choice([11.5, -1, 85])             # out of range
        backlogs = s["backlogs"]
        r = rng.random()
        if r < 0.02:
            backlogs = ""
        elif r < 0.03:
            backlogs = "two"
        elif r < 0.04:
            backlogs = -1
        name = "" if rng.random() < 0.01 else s["name"]
        rows.append([messy_id(rng, s["id"]), name, branch, cgpa, backlogs])
    for r in rng.sample(rows, int(0.04 * len(rows))):     # duplicates
        dup = list(r)
        if rng.random() < 0.5:
            dup[3] = ""                                   # less complete copy
        rows.append(dup)
    rng.shuffle(rows)
    write_csv(out / "students.csv", ["student_id", "name", "branch", "cgpa", "active_backlogs"], rows)

    # ---- companies ----------------------------------------------------------
    rows = []
    for cid, name, sector, _ in COMPANIES:
        if rng.random() < 0.4:
            sector = rng.choice(SECTOR_VARIANTS[sector])
        rows.append([cid, "  " + name if rng.random() < 0.15 else name, sector])
    rows.append(list(rows[0]))                            # exact duplicate
    write_csv(out / "companies.csv", ["company_id", "company_name", "sector"], rows)

    # ---- drives -------------------------------------------------------------
    rows = []
    for d in drives:
        names = d["branches"]
        r = rng.random()
        if r < 0.3:
            text = "|".join(rng.choice(BRANCH_VARIANTS[b]) for b in names)
        elif r < 0.6:
            text = ", ".join(rng.choice(BRANCH_VARIANTS[b]) for b in names)
        else:
            text = ";".join(names)
        rows.append([d["id"], d["company_id"], fmt_date(rng, d["date"]), d["role"], d["ctc"],
                     d["min_cgpa"], d["max_backlogs"], text])
    rows.append(["D901", "C001", "2025-09-15", "Analyst", 5.0, "", 1, "CSE|IT"])      # missing min_cgpa
    rows.append(["D902", "C999", "2025-09-20", "Analyst", 5.0, 7.0, 1, "CSE"])        # unknown company
    rows.append(["D903", "C002", "31/02/2025", "Analyst", 5.0, 7.0, 1, "CSE"])        # impossible date
    write_csv(out / "drives.csv", ["drive_id", "company_id", "drive_date", "role", "ctc_lpa",
                                   "min_cgpa", "max_backlogs", "allowed_branches"], rows)

    # ---- registrations ------------------------------------------------------
    rows = []
    for r in regs:
        on = r["on"]
        if rng.random() < 0.01:
            on = drive_date[r["d"]] + timedelta(days=rng.randint(1, 5))   # registered after drive
        shown = "" if rng.random() < 0.01 else fmt_date(rng, on)
        rows.append([messy_id(rng, r["s"]), r["d"], shown])
    for r in rng.sample(rows, int(0.03 * len(rows))):
        rows.append(list(r))                                              # duplicates
    rows += [["S9999", "D001", "2025-08-01"], ["S0001", "D901", "2025-09-10"],   # orphans
             ["S0002", "D902", "2025-09-12"], ["S0003", "D903", "2025-02-01"]]
    rng.shuffle(rows)
    write_csv(out / "registrations.csv", ["student_id", "drive_id", "registered_on"], rows)

    # ---- appearances --------------------------------------------------------
    status_variants = {"PRESENT": ["PRESENT", "present", "Appeared", "Y"],
                       "ABSENT": ["ABSENT", "absent", "No Show", "N"]}
    rows = []
    for a in apps:
        status = a["status"]
        r = rng.random()
        if r < 0.01:
            status = "??"                                                 # unknown label
        elif r < 0.50:
            status = rng.choice(status_variants[status])
        rows.append([messy_id(rng, a["s"]), a["d"], status])
    registered = {(r["s"], r["d"]) for r in regs}
    for _ in range(int(0.02 * len(apps))):                                # appearance w/o registration
        s, d = rng.choice(students)["id"], rng.choice(drives)["id"]
        if (s, d) not in registered:
            rows.append([s, d, "PRESENT"])
    for r in rng.sample(rows, int(0.02 * len(rows))):
        rows.append(list(r))
    rng.shuffle(rows)
    write_csv(out / "appearances.csv", ["student_id", "drive_id", "status"], rows)

    # ---- selections ---------------------------------------------------------
    result_variants = {"SELECTED": ["SELECTED", "selected", "Placed", "Offered"],
                       "REJECTED": ["REJECTED", "rejected", "Not Selected"],
                       "WAITLISTED": ["WAITLISTED", "Waitlist", "waitlisted"]}
    rows = []
    for s in sels:
        res = rng.choice(result_variants[s["res"]]) if rng.random() < 0.5 else s["res"]
        ctc = s["ctc"] if s["ctc"] is not None else ""
        r = rng.random()
        if s["res"] == "SELECTED" and r < 0.01:
            ctc = ""                                                      # selected, CTC missing
        elif s["res"] == "SELECTED" and r < 0.02:
            ctc = -5                                                      # negative CTC
        elif s["res"] == "REJECTED" and r < 0.01:
            ctc = 6.5                                                     # CTC on a rejection
        rows.append([messy_id(rng, s["s"]), s["d"], res, ctc])
    absent = [a for a in apps if a["status"] == "ABSENT"]
    for a in rng.sample(absent, max(1, int(0.03 * len(absent)))):         # selected but never appeared
        rows.append([a["s"], a["d"], "SELECTED", 6.0])
    for r in rng.sample(rows, int(0.02 * len(rows))):
        rows.append(list(r))
    rng.shuffle(rows)
    write_csv(out / "selections.csv", ["student_id", "drive_id", "result", "offered_ctc_lpa"], rows)

    return {"students": len(students), "drives": len(drives), "registrations": len(regs)}


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", default=str(Path(__file__).parent / "raw"))
    ap.add_argument("--students", type=int, default=600)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()
    print("generated:", generate(args.out, args.students, args.seed), "->", args.out)
