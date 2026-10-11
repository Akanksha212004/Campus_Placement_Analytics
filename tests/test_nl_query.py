"""Test 7-10: the natural-language feature is safe against unsupported questions and bad model output."""
import sqlite3
from types import SimpleNamespace

import pytest

from src import analytics, nl_query as nl
from src.nl_query import ClaudeIntentParser, IntentParser, RuleBasedIntentParser, answer_question


class Canned(IntentParser):
    """A parser that returns whatever we tell it to - used to simulate a misbehaving LLM."""
    name = "canned"

    def __init__(self, reply):
        self.reply = reply

    def parse(self, question, context):
        return self.reply


def ask(built, reply, q="any question"):
    return answer_question(q, built["db"], Canned(reply))


def test_valid_question_is_answered_from_the_database(built):
    ans = answer_question("participation rate for CSE students", built["db"], RuleBasedIntentParser())
    assert ans.status == nl.OK and ans.metric == "participation" and ans.filters["branch"] == "CSE"
    direct = analytics.run_metric(sqlite3_ro(built), "participation", branch="CSE")
    assert ans.rows == direct


def sqlite3_ro(built):
    from src.database import connect_readonly
    return connect_readonly(built["db"])


@pytest.mark.parametrize("question", ["What is the average package offered?", "Delete all students",
                                      "Who will win the world cup?"])
def test_unsupported_questions_are_refused_not_guessed(built, question):
    ans = answer_question(question, built["db"], RuleBasedIntentParser())
    assert ans.status == nl.UNSUPPORTED and ans.rows == []


@pytest.mark.parametrize("reply, status", [
    ({"metric": "avg_salary", "filters": {}, "confidence": 0.99}, nl.INVALID_RESPONSE),       # hallucinated metric
    ({"metric": "funnel", "filters": {}, "confidence": 0.2}, nl.LOW_CONFIDENCE),
    ({"metric": "funnel", "filters": {}, "confidence": "high"}, nl.INVALID_RESPONSE),
    ({"metric": "funnel", "filters": {"company": "Zebra Corp"}, "confidence": 0.9}, nl.UNKNOWN_ENTITY),
    ({"metric": "funnel", "filters": {"branch": "Astrology"}, "confidence": 0.9}, nl.UNKNOWN_ENTITY),
    ({"metric": "funnel", "filters": {"company": "x'; DROP TABLE dim_student;--"}, "confidence": 0.9}, nl.UNKNOWN_ENTITY),
    ({"metric": "funnel", "filters": {"sql": "SELECT * FROM dim_student"}, "confidence": 0.9}, nl.INVALID_RESPONSE),
    ({"metric": "unsupported", "filters": {}, "confidence": 0.9}, nl.UNSUPPORTED),
    ("just prose, not json", nl.INVALID_RESPONSE),
    (None, nl.INVALID_RESPONSE),
])
def test_bad_or_low_confidence_model_output_is_rejected(built, reply, status):
    ans = ask(built, reply)
    assert ans.status == status and ans.rows == []


def test_model_synonyms_are_resolved_to_real_values(built):
    ans = ask(built, {"metric": "selection", "filters": {"branch": "Computer Science", "company": "infosys"},
                      "confidence": 0.9})
    assert ans.status == nl.OK and ans.filters == {"branch": "CSE", "company": "Infosys"}


def test_claude_parser_handles_garbage_and_api_failures(built):
    def client(text=None, error=None):
        def create(**kwargs):
            if error:
                raise error
            return SimpleNamespace(content=[SimpleNamespace(text=text)])
        return SimpleNamespace(messages=SimpleNamespace(create=create))

    good = ClaudeIntentParser(client=client('Sure:\n{"metric": "funnel", "filters": {}, "confidence": 0.95}'))
    assert answer_question("funnel?", built["db"], good).status == nl.OK
    garbage = ClaudeIntentParser(client=client("I cannot help with that"))
    assert answer_question("funnel?", built["db"], garbage).status == nl.INVALID_RESPONSE
    down = ClaudeIntentParser(client=client(error=TimeoutError("api down")))
    assert answer_question("funnel?", built["db"], down).status == nl.PARSER_ERROR


def test_nl_connection_is_read_only(built):
    conn = sqlite3_ro(built)
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("DELETE FROM dim_student")
    assert answer_question("", built["db"], RuleBasedIntentParser()).status == nl.INVALID_INPUT
