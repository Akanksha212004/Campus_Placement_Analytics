"""Web UI (Streamlit) over the same curated database.   Run:  streamlit run app.py

The UI contains no SQL and no business logic. It only calls
  analytics.run_metric()          for every number and chart, and
  nl_query.answer_question()      for the question box (same safety checks as the CLI).
"""
import altair as alt
import pandas as pd
import streamlit as st

from src import analytics, config
from src.database import connect_readonly
from src.main import ensure_raw_data, run_pipeline
from src.nl_query import (PARSER_ERROR, Answer, ClaudeIntentParser, OK, RuleBasedIntentParser,
                          answer_question)

st.set_page_config(page_title="Campus Placement Analytics", page_icon="🎓", layout="wide")

EXAMPLES = ["Show the selection funnel", "Participation rate for CSE students",
            "How many students got selected at Infosys?", "Compare branches",
            "Selection rate for Zebra Corp", "What is the average package offered?"]
BACKENDS = ["Rules (offline, no key needed)", "Claude (needs ANTHROPIC_API_KEY)"]


# ---- helpers ------------------------------------------------------------------------------
def metric_df(conn, name, branch=None, company=None):
    return pd.DataFrame(analytics.run_metric(conn, name, branch, company))


def bar_chart(df, category, value, sort=None):
    """Horizontal bar chart; sort=None keeps the row order (needed for the funnel)."""
    chart = alt.Chart(df).mark_bar().encode(
        y=alt.Y(category, sort=sort, title=None), x=alt.X(value, title=None), tooltip=list(df.columns))
    st.altair_chart(chart)


def pct(value):
    return "n/a" if value is None or pd.isna(value) else f"{value:.1f}%"


def run_question(question):
    """Answer a question and keep the result in session state (so reruns do not call the model again)."""
    question = question.strip()
    if not question:
        return
    try:
        parser = ClaudeIntentParser() if st.session_state.get("backend") == BACKENDS[1] else RuleBasedIntentParser()
    except Exception:  # e.g. no API key
        st.session_state.answer = Answer(status=PARSER_ERROR, question=question, parser="claude",
                                         message="Claude is not configured. Set ANTHROPIC_API_KEY in .env, "
                                                 "or choose the offline rules parser.")
        return
    st.session_state.answer = answer_question(question, config.DB_PATH, parser)


def ask_from_input():
    run_question(st.session_state.get("question", ""))


def ask_example(text):
    st.session_state.question = text
    run_question(text)


def show_answer(ans):
    st.markdown(f"**Q:** {ans.question}")
    if ans.status == OK:
        st.success(ans.message)
        st.dataframe(pd.DataFrame(ans.rows), hide_index=True)
        with st.expander("How this answer was produced"):
            st.json({"parser": ans.parser, "metric": ans.metric, "filters": ans.filters,
                     "confidence": ans.confidence})
            st.caption("The model only chose the metric and filters. The numbers come from a fixed SQL "
                       "query on a read-only database connection.")
    else:
        st.warning(f"**{ans.status}**: {ans.message}")
        st.caption("No SQL was run and no data was returned.")


# ---- database must exist ------------------------------------------------------------------
st.title("🎓 Campus Placement Analytics")

if not config.DB_PATH.exists():
    st.warning("The database has not been built yet.")
    if st.button("Build database from synthetic data", type="primary"):
        with st.spinner("Running pipeline: ingest, validate, clean, transform, load..."):
            ensure_raw_data()
            run_pipeline(config.RAW_DIR, config.DB_PATH)
        st.rerun()
    st.stop()

conn = connect_readonly(config.DB_PATH)

with st.sidebar:
    st.header("Filters")
    branch_choice = st.selectbox("Branch", ["All"] + analytics.list_branches(conn))
    company_choice = st.selectbox("Company", ["All"] + analytics.list_companies(conn))
    st.caption("Filters apply to the Overview, Branch-wise and Company-wise tabs.")
branch = None if branch_choice == "All" else branch_choice
company = None if company_choice == "All" else company_choice

tab_overview, tab_branch, tab_company, tab_quality, tab_ask = st.tabs(
    ["Overview", "Branch-wise", "Company-wise", "Data quality", "Ask a question"])

# ---- Overview -----------------------------------------------------------------------------
with tab_overview:
    funnel = metric_df(conn, "funnel", branch, company)
    stages = dict(zip(funnel["stage"], funnel["students"]))
    cols = st.columns(4)
    for col, stage in zip(cols, ["Eligible", "Registered", "Appeared", "Selected"]):
        col.metric(stage, f"{int(stages[stage]):,}")

    participation = metric_df(conn, "participation", branch, company).iloc[0]
    appearance = metric_df(conn, "appearance", branch, company).iloc[0]
    selection = metric_df(conn, "selection", branch, company).iloc[0]
    overall = funnel.loc[funnel["stage"] == "Selected", "pct_of_eligible"].iloc[0]
    cols = st.columns(4)
    cols[0].metric("Participation rate", pct(participation["participation_rate_pct"]), help="registered ÷ eligible")
    cols[1].metric("Appearance rate", pct(appearance["appearance_rate_pct"]), help="appeared ÷ registered")
    cols[2].metric("Selection rate", pct(selection["selection_rate_pct"]), help="selected ÷ appeared")
    cols[3].metric("Overall conversion", pct(overall), help="selected ÷ eligible")

    st.subheader("Selection funnel")
    bar_chart(funnel, "stage", "students")
    st.dataframe(funnel, hide_index=True)
    st.caption("Counts are student × drive pairs, so one student can count once per drive.")

    st.subheader("Eligible students per drive")
    st.dataframe(metric_df(conn, "eligibility", branch, company), hide_index=True)

# ---- Branch-wise / Company-wise -----------------------------------------------------------
for tab, metric, label in [(tab_branch, "branch_metrics", "branch_code"),
                           (tab_company, "company_metrics", "company_name")]:
    with tab:
        df = metric_df(conn, metric, branch, company)
        left, right = st.columns(2)
        with left:
            st.subheader("Participation rate (%)")
            bar_chart(df, label, "participation_rate_pct", sort="-x")
        with right:
            st.subheader("Selection rate (%)")
            bar_chart(df, label, "selection_rate_pct", sort="-x")
        st.dataframe(df, hide_index=True)

# ---- Data quality -------------------------------------------------------------------------
with tab_quality:
    st.write("Problems found in the raw files, and what the cleaning step did about them.")
    failures = pd.DataFrame(analytics.validation_failures(conn))
    exceptions = metric_df(conn, "policy_exceptions", branch, company).iloc[0]
    cols = st.columns(3)
    cols[0].metric("Checks that found problems", len(failures))
    cols[1].metric("Cleaning actions logged", int(pd.DataFrame(analytics.cleaning_summary(conn))["records"].sum()))
    cols[2].metric("Registrations by ineligible students", pct(exceptions["ineligible_pct"]),
                   help="Kept in the data, but excluded from the funnel.")
    st.subheader("Validation report (failed checks)")
    st.dataframe(failures, hide_index=True)
    st.subheader("Cleaning actions")
    st.dataframe(pd.DataFrame(analytics.cleaning_summary(conn)), hide_index=True)

# ---- Ask a question -----------------------------------------------------------------------
with tab_ask:
    st.write("Ask in plain English. The model only picks one predefined metric and filters; "
             "every number comes from the database.")
    st.radio("Question parser", BACKENDS, key="backend", horizontal=True)
    st.text_input("Your question", key="question", on_change=ask_from_input,
                  placeholder="e.g. What is the selection rate for ECE students at Bosch?  (press Enter)")
    st.caption("Examples")
    for col, text in zip(st.columns(3), EXAMPLES[:3]):
        col.button(text, on_click=ask_example, args=(text,), key=f"ex_{text}")
    for col, text in zip(st.columns(3), EXAMPLES[3:]):
        col.button(text, on_click=ask_example, args=(text,), key=f"ex_{text}")
    if "answer" in st.session_state:
        show_answer(st.session_state.answer)

conn.close()
