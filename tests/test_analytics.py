"""Test 4-6: SQL metrics are computed from the database, and match an independent recomputation."""
import shutil
import sqlite3

from src import analytics
from src.database import connect_readonly


def _python_funnel(db):
    """Recompute the funnel with plain Python loops over the base tables (no views, no metrics.sql)."""
    c = sqlite3.connect(db)
    students = {r[0]: r[1:] for r in c.execute("SELECT student_id, branch_code, cgpa, active_backlogs FROM dim_student")}
    drives = {r[0]: r[1:] for r in c.execute("SELECT drive_id, min_cgpa, max_backlogs FROM dim_drive")}
    allowed = {(d, b) for d, b in c.execute("SELECT drive_id, branch_code FROM bridge_drive_branch")}
    reg = set(c.execute("SELECT student_id, drive_id FROM fact_registration"))
    present = set(c.execute("SELECT student_id, drive_id FROM fact_appearance WHERE status='PRESENT'"))
    sel = set(c.execute("SELECT student_id, drive_id FROM fact_selection WHERE result='SELECTED'"))
    c.close()
    out = dict(eligible=0, registered=0, appeared=0, selected=0)
    for s, (branch, cgpa, bl) in students.items():
        for d, (min_cgpa, max_bl) in drives.items():
            if cgpa is None or bl is None or cgpa < min_cgpa or bl > max_bl or (d, branch) not in allowed:
                continue
            out["eligible"] += 1
            out["registered"] += (s, d) in reg
            out["appeared"] += (s, d) in present
            out["selected"] += (s, d) in sel
    return out


def test_sql_funnel_matches_independent_python_calculation(built):
    conn = connect_readonly(built["db"])
    rows = {r["stage"]: r["students"] for r in analytics.run_metric(conn, "funnel")}
    expected = _python_funnel(built["db"])
    assert rows == {"Eligible": expected["eligible"], "Registered": expected["registered"],
                    "Appeared": expected["appeared"], "Selected": expected["selected"]}
    # funnel is monotone: each stage is a subset of the previous one
    values = list(rows.values())
    assert values == sorted(values, reverse=True)


def test_rates_and_group_totals_are_consistent(built):
    conn = connect_readonly(built["db"])
    overall = analytics.run_metric(conn, "participation")[0]
    assert overall["participation_rate_pct"] == round(100 * overall["registered"] / overall["eligible"], 2)
    for metric in ("branch_metrics", "company_metrics"):   # groups must add up to the overall funnel
        rows = analytics.run_metric(conn, metric)
        assert sum(r["selected"] for r in rows) == analytics.run_metric(conn, "selection")[0]["selected"]
        assert sum(r["eligible"] for r in rows) == overall["eligible"]
    assert analytics.run_metric(conn, "participation", branch="CSE")[0]["eligible"] < overall["eligible"]


def test_metrics_are_dynamic_not_hard_coded(built, tmp_path):
    """Change the data -> the metric changes. Proves answers come from the database."""
    db_copy = tmp_path / "copy.db"
    shutil.copy(built["db"], db_copy)
    before = analytics.run_metric(connect_readonly(db_copy), "selection")[0]["selected"]
    w = sqlite3.connect(db_copy)
    w.execute("""DELETE FROM fact_selection WHERE rowid IN (
                     SELECT x.rowid FROM fact_selection x
                     JOIN v_funnel f ON f.student_id = x.student_id AND f.drive_id = x.drive_id
                     WHERE x.result = 'SELECTED' AND f.is_eligible = 1 LIMIT 5)""")
    w.commit()
    w.close()
    after = analytics.run_metric(connect_readonly(db_copy), "selection")[0]["selected"]
    assert after == before - 5
