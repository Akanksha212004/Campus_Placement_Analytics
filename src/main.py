"""Command line entry point.   Run from the repository root:

    python -m src.main pipeline          raw CSV -> validate -> clean -> transform -> SQLite
    python -m src.main report            print all SQL metrics
    python -m src.main ask "question"    natural-language question
    python -m src.main demo-failures     show how bad input / bad AI output is handled
    python -m src.main all               pipeline + report + sample questions
"""
import argparse
import os
import shutil
import sys
import tempfile
from pathlib import Path

from . import analytics, config
from .cleaning import clean_all
from .database import connect_readonly, create_database, load_all, load_table
from .ingestion import read_raw
from .nl_query import (ClaudeIntentParser, RuleBasedIntentParser, answer_question, render_answer)
from .transformation import build_tables
from .validation import SchemaError, run_checks, validate_schema

SAMPLE_QUESTIONS = [
    "Show the selection funnel",
    "What is the participation rate for CSE students?",
    "How many students got selected at Infosys?",
    "Compare branches",
    "Which company has the best selection rate?",
    "What is the average package offered?",             # not modelled -> refused
    "What is the selection rate for Zebra Corp?",       # unknown company
]


def ensure_raw_data():
    """Generate the synthetic CSVs if they are missing (shared by the CLI and the web UI)."""
    if config.RAW_DIR.joinpath("students.csv").exists():
        return
    sys.path.insert(0, str(config.ROOT / "data"))
    from generate_data import generate
    print("Raw data not found; generating synthetic data:", generate(config.RAW_DIR))


def run_pipeline(raw_dir=config.RAW_DIR, db_path=config.DB_PATH):
    """raw -> ingestion -> validation -> cleaning -> transformation -> SQL tables. Returns a summary."""
    import pandas as pd

    raw = read_raw(raw_dir)
    validate_schema(raw)                       # fail fast on structural problems
    checks = run_checks(raw)                   # detect (does not modify data)
    clean, log = clean_all(raw)                # fix / NULL / drop, every action logged
    tables = build_tables(clean)               # shape into the SQL model
    conn = create_database(db_path)
    load_all(conn, tables)
    load_table(conn, "validation_report", pd.DataFrame(checks))
    load_table(conn, "data_quality_issues", log.frame())
    conn.commit()
    conn.close()
    return {"raw_rows": {k: len(v) for k, v in raw.items()},
            "loaded_rows": {k: len(v) for k, v in tables.items()},
            "checks": checks, "issues": len(log.rows)}


def print_pipeline_summary(summary):
    print("Raw rows     :", summary["raw_rows"])
    print("Loaded rows  :", summary["loaded_rows"])
    print(f"Validation   : {sum(c['failed_rows'] > 0 for c in summary['checks'])} of "
          f"{len(summary['checks'])} checks found problems; {summary['issues']} cleaning actions logged")
    print("\nValidation report (failed checks only):")
    print(analytics.format_rows([c for c in summary["checks"] if c["failed_rows"]]))


def print_report(db_path):
    conn = connect_readonly(db_path)
    for name, metric in analytics.METRICS.items():
        print(f"\n== {name}: {metric.description}")
        rows = analytics.run_metric(conn, name)
        print(analytics.format_rows(rows[:8]) + (f"\n... ({len(rows)} rows)" if len(rows) > 8 else ""))
    conn.close()


def make_parser(backend=None):
    backend = backend or config.NL_BACKEND
    if backend == "auto":
        backend = "claude" if os.getenv("ANTHROPIC_API_KEY") else "rules"
    return ClaudeIntentParser() if backend == "claude" else RuleBasedIntentParser()


class _FakeModel(RuleBasedIntentParser):
    """Stands in for a misbehaving LLM in the failure demo (no API call needed)."""
    name = "simulated-bad-llm"

    def __init__(self, reply):
        self.reply = reply

    def parse(self, question, context):
        return self.reply


def demo_failures(db_path):
    print("1) Structural failure: raw students.csv is missing the 'cgpa' column")
    tmp = Path(tempfile.mkdtemp())
    try:
        shutil.copytree(config.RAW_DIR, tmp / "raw")
        broken = (tmp / "raw" / "students.csv")
        lines = broken.read_text().splitlines()
        header = lines[0].split(",")
        i = header.index("cgpa")
        broken.write_text("\n".join(",".join(c for j, c in enumerate(l.split(",")) if j != i) for l in lines))
        try:
            validate_schema(read_raw(tmp / "raw"))
        except SchemaError as exc:
            print("   -> SchemaError raised, pipeline stops before touching the database:", exc)
    finally:
        shutil.rmtree(tmp)

    print("\n2) Unsupported / unsafe / unreliable AI behaviour (all handled without running SQL on guesses)")
    cases = [
        ("What is the average package offered?", RuleBasedIntentParser()),
        ("Selection rate for Zebra Corp", RuleBasedIntentParser()),
        ("Delete all students", RuleBasedIntentParser()),
        ("Participation rate", _FakeModel({"metric": "participation", "filters": {}, "confidence": 0.3})),
        ("Average salary by branch", _FakeModel({"metric": "avg_salary", "filters": {}, "confidence": 0.95})),
        ("Funnel", _FakeModel({"metric": "funnel", "filters": {"company": "x'; DROP TABLE dim_student;--"}, "confidence": 0.95})),
        ("Funnel", _FakeModel("sure! here is some prose instead of JSON")),
    ]
    for question, parser in cases:
        print("\n" + render_answer(answer_question(question, db_path, parser)))


def main(argv=None):
    ap = argparse.ArgumentParser(description="Campus placement analytics")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("pipeline")
    sub.add_parser("report")
    ask = sub.add_parser("ask")
    ask.add_argument("question")
    ask.add_argument("--backend", choices=["auto", "claude", "rules"])
    sub.add_parser("demo-failures")
    sub.add_parser("all")
    args = ap.parse_args(argv)

    ensure_raw_data()

    if args.cmd in ("pipeline", "all"):
        print_pipeline_summary(run_pipeline())
    elif not config.DB_PATH.exists():
        print("Database not found; run `python -m src.main pipeline` first.")
        return 1

    if args.cmd in ("report", "all"):
        print_report(config.DB_PATH)
    if args.cmd == "ask":
        print(render_answer(answer_question(args.question, config.DB_PATH, make_parser(args.backend))))
    if args.cmd == "all":
        parser = make_parser()
        print(f"\nNatural-language questions (parser: {parser.name})")
        for q in SAMPLE_QUESTIONS:
            print("\n" + render_answer(answer_question(q, config.DB_PATH, parser)))
    if args.cmd == "demo-failures":
        demo_failures(config.DB_PATH)
    return 0


if __name__ == "__main__":
    sys.exit(main())
