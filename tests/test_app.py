"""UI tests: run app.py headlessly (Streamlit AppTest) against the test database."""
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from src import analytics, config
from src.database import connect_readonly

APP = str(Path(__file__).resolve().parent.parent / "app.py")


def start(db, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", db)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    return AppTest.from_file(APP, default_timeout=30).run()


def kpis(at):
    return {m.label: m.value for m in at.metric}


def test_dashboard_shows_numbers_from_the_database(built, monkeypatch):
    at = start(built["db"], monkeypatch)
    assert not at.exception
    stages = {r["stage"]: r["students"] for r in analytics.run_metric(connect_readonly(built["db"]), "funnel")}
    for stage, students in stages.items():
        assert kpis(at)[stage] == f"{students:,}"


def test_sidebar_filter_changes_the_numbers(built, monkeypatch):
    at = start(built["db"], monkeypatch)
    at.sidebar.selectbox[0].select("CSE").run()
    assert not at.exception
    expected = analytics.run_metric(connect_readonly(built["db"]), "funnel", branch="CSE")[0]["students"]
    assert kpis(at)["Eligible"] == f"{expected:,}"


def test_example_question_is_answered(built, monkeypatch):
    at = start(built["db"], monkeypatch)
    next(b for b in at.button if b.label == "Participation rate for CSE students").click().run()
    assert not at.exception
    ans = at.session_state["answer"]
    assert ans.status == "OK" and ans.metric == "participation" and ans.filters["branch"] == "CSE"


@pytest.mark.parametrize("question, status", [("What is the average package offered?", "UNSUPPORTED"),
                                              ("Selection rate for Zebra Corp", "UNKNOWN_ENTITY")])
def test_unsupported_questions_are_refused_in_the_ui(built, monkeypatch, question, status):
    at = start(built["db"], monkeypatch)
    at.text_input(key="question").set_value(question).run()
    assert not at.exception
    assert at.session_state["answer"].status == status
    assert at.session_state["answer"].rows == []


def test_claude_choice_without_api_key_fails_safely(built, monkeypatch):
    at = start(built["db"], monkeypatch)
    at.radio(key="backend").set_value("Claude (needs ANTHROPIC_API_KEY)").run()
    at.text_input(key="question").set_value("funnel").run()
    assert not at.exception
    assert at.session_state["answer"].status == "PARSER_ERROR"


def test_missing_database_offers_to_build_it(tmp_path, monkeypatch):
    at = start(tmp_path / "missing.db", monkeypatch)
    assert not at.exception
    assert any("Build database" in b.label for b in at.button)
