"""Analytics layer: runs the named metric queries from sql/metrics.sql.

This is the ONLY place that executes analytical SQL. Metrics are looked up by name and
filters are passed as bound parameters, so callers (CLI, NL feature) never build SQL strings.
"""
import re
from dataclasses import dataclass

from .config import SQL_DIR

FILTERS = ("branch", "company")


@dataclass(frozen=True)
class Metric:
    name: str
    description: str
    sql: str


def load_metrics(path=None):
    text = (path or SQL_DIR / "metrics.sql").read_text()
    metrics = {}
    for block in re.split(r"^-- name:\s*", text, flags=re.M)[1:]:
        name, rest = block.split("\n", 1)
        desc = re.match(r"-- description:\s*(.*)", rest)
        sql = "\n".join(l for l in rest.splitlines() if not l.startswith("-- description:")).strip()
        metrics[name.strip()] = Metric(name.strip(), desc.group(1).strip() if desc else "", sql)
    return metrics


METRICS = load_metrics()


def run_metric(conn, name, branch=None, company=None):
    """Run one named metric. Returns a list of dicts (one per result row)."""
    if name not in METRICS:
        raise KeyError(f"Unknown metric: {name}")
    cur = conn.execute(METRICS[name].sql, {"branch": branch, "company": company})
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def list_branches(conn):
    return [r[0] for r in conn.execute("SELECT branch_code FROM dim_branch ORDER BY 1")]


def list_companies(conn):
    return [r[0] for r in conn.execute("SELECT company_name FROM dim_company ORDER BY 1")]


def format_rows(rows):
    """Plain-text table for the CLI."""
    if not rows:
        return "(no rows)"
    cols = list(rows[0])
    cells = [[("" if r[c] is None else str(r[c])) for c in cols] for r in rows]
    widths = [max(len(c), *(len(row[i]) for row in cells)) for i, c in enumerate(cols)]
    line = lambda vals: "  ".join(v.ljust(w) for v, w in zip(vals, widths))
    return "\n".join([line(cols), line(["-" * w for w in widths])] + [line(r) for r in cells])
