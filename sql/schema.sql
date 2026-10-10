-- Star-style model for placement drives.
--   dimensions : dim_branch, dim_company, dim_student, dim_drive (+ bridge_drive_branch)
--   facts      : fact_registration -> fact_appearance -> fact_selection  (grain: student x drive)
--   audit      : validation_report, data_quality_issues
--   views      : v_student_drive_eligibility, v_funnel  (eligibility + funnel are derived, never stored)
PRAGMA foreign_keys = ON;

CREATE TABLE dim_branch (
    branch_code TEXT PRIMARY KEY,
    branch_name TEXT NOT NULL
);

CREATE TABLE dim_company (
    company_id   TEXT PRIMARY KEY,
    company_name TEXT NOT NULL UNIQUE,
    sector       TEXT NOT NULL
);

CREATE TABLE dim_student (
    student_id      TEXT PRIMARY KEY,
    student_name    TEXT,
    branch_code     TEXT NOT NULL REFERENCES dim_branch(branch_code),
    cgpa            REAL CHECK (cgpa IS NULL OR cgpa BETWEEN 0 AND 10),     -- NULL = unknown
    active_backlogs INTEGER CHECK (active_backlogs IS NULL OR active_backlogs >= 0)
);

CREATE TABLE dim_drive (
    drive_id     TEXT PRIMARY KEY,
    company_id   TEXT NOT NULL REFERENCES dim_company(company_id),
    drive_date   TEXT NOT NULL,                                             -- ISO yyyy-mm-dd
    role         TEXT,
    ctc_lpa      REAL CHECK (ctc_lpa IS NULL OR ctc_lpa > 0),
    min_cgpa     REAL NOT NULL CHECK (min_cgpa BETWEEN 0 AND 10),          -- eligibility rule
    max_backlogs INTEGER NOT NULL CHECK (max_backlogs >= 0)                 -- eligibility rule
);

-- which branches a drive is open to (eligibility rule)
CREATE TABLE bridge_drive_branch (
    drive_id    TEXT NOT NULL REFERENCES dim_drive(drive_id),
    branch_code TEXT NOT NULL REFERENCES dim_branch(branch_code),
    PRIMARY KEY (drive_id, branch_code)
);

CREATE TABLE fact_registration (
    student_id    TEXT NOT NULL REFERENCES dim_student(student_id),
    drive_id      TEXT NOT NULL REFERENCES dim_drive(drive_id),
    registered_on TEXT,
    PRIMARY KEY (student_id, drive_id)
);

-- an appearance can only exist for a registration (composite foreign key)
CREATE TABLE fact_appearance (
    student_id TEXT NOT NULL,
    drive_id   TEXT NOT NULL,
    status     TEXT NOT NULL CHECK (status IN ('PRESENT', 'ABSENT')),
    PRIMARY KEY (student_id, drive_id),
    FOREIGN KEY (student_id, drive_id) REFERENCES fact_registration(student_id, drive_id)
);

CREATE TABLE fact_selection (
    student_id      TEXT NOT NULL,
    drive_id        TEXT NOT NULL,
    result          TEXT NOT NULL CHECK (result IN ('SELECTED', 'REJECTED', 'WAITLISTED')),
    offered_ctc_lpa REAL CHECK (offered_ctc_lpa IS NULL OR offered_ctc_lpa > 0),
    PRIMARY KEY (student_id, drive_id),
    FOREIGN KEY (student_id, drive_id) REFERENCES fact_appearance(student_id, drive_id),
    CHECK (result = 'SELECTED' OR offered_ctc_lpa IS NULL)                  -- CTC only for offers
);

-- a result is only valid for a student who was PRESENT
CREATE TRIGGER trg_selection_requires_present
BEFORE INSERT ON fact_selection
WHEN COALESCE((SELECT status FROM fact_appearance
               WHERE student_id = NEW.student_id AND drive_id = NEW.drive_id), '') <> 'PRESENT'
BEGIN
    SELECT RAISE(ABORT, 'selection requires a PRESENT appearance');
END;

CREATE TABLE validation_report (
    check_name  TEXT NOT NULL,
    table_name  TEXT NOT NULL,
    failed_rows INTEGER NOT NULL,
    total_rows  INTEGER NOT NULL,
    severity    TEXT NOT NULL
);

CREATE TABLE data_quality_issues (
    table_name TEXT NOT NULL,
    record_key TEXT,
    issue      TEXT NOT NULL,
    action     TEXT NOT NULL
);

-- Eligibility rule, evaluated for every student x drive pair.
-- Unknown CGPA/backlogs (NULL) cannot be verified, so the student is NOT eligible.
CREATE VIEW v_student_drive_eligibility AS
SELECT s.student_id, s.branch_code, d.drive_id, d.company_id,
       CASE WHEN s.cgpa IS NOT NULL AND s.active_backlogs IS NOT NULL
                 AND s.cgpa >= d.min_cgpa
                 AND s.active_backlogs <= d.max_backlogs
                 AND EXISTS (SELECT 1 FROM bridge_drive_branch b
                             WHERE b.drive_id = d.drive_id AND b.branch_code = s.branch_code)
            THEN 1 ELSE 0 END AS is_eligible
FROM dim_student s
CROSS JOIN dim_drive d;

-- One row per student x drive with a 0/1 flag for each funnel stage.
CREATE VIEW v_funnel AS
SELECT e.student_id, e.branch_code, e.drive_id, e.company_id, c.company_name, e.is_eligible,
       CASE WHEN r.student_id IS NOT NULL THEN 1 ELSE 0 END AS is_registered,
       CASE WHEN a.status = 'PRESENT'     THEN 1 ELSE 0 END AS is_appeared,
       CASE WHEN x.result = 'SELECTED'    THEN 1 ELSE 0 END AS is_selected
FROM v_student_drive_eligibility e
JOIN dim_company c ON c.company_id = e.company_id
LEFT JOIN fact_registration r ON r.student_id = e.student_id AND r.drive_id = e.drive_id
LEFT JOIN fact_appearance   a ON a.student_id = e.student_id AND a.drive_id = e.drive_id
LEFT JOIN fact_selection    x ON x.student_id = e.student_id AND x.drive_id = e.drive_id;
