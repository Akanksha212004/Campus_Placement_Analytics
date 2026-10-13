# Campus Placement Analytics (DAI-004 · Analytics Engineering)

Turns messy campus-placement-drive data into analytics-ready SQL tables, computes eligibility /
participation / selection-funnel metrics **in SQL**, and lets you ask questions in plain English
through a tightly constrained AI layer.

## 1. Problem statement
> Model placement-drive data into analytics-ready tables and create SQL metrics for eligibility,
> participation and selection funnel. Add a natural-language question feature over the curated data.

Raw placement data arrives as spreadsheets exported by different people: duplicated rows, "CSE" vs
"Computer Science" vs "c.s.e", dates in three formats, results for students who never appeared.
Nobody can trust a funnel built on that. This project cleans it, models it, measures it, and lets a
placement officer ask questions without writing SQL.

## 2. Approach
1. **Generate** a realistic synthetic dataset (seeded) and deliberately corrupt it.
2. **Ingest** everything as text so nothing is silently coerced.
3. **Validate** (detect and report; never modify) and fail fast on structural problems.
4. **Clean** (fix / set to NULL / drop) and log *every* action to `data_quality_issues`.
5. **Transform** into a star-style SQL model with constraints and a trigger that enforce business rules.
6. **Analyse** with named, parameterised SQL metrics (`sql/metrics.sql`). Eligibility and the funnel are
   *views*, calculated on the fly, never stored or hard-coded.
7. **Ask**: an LLM maps a question to one predefined metric; the answer is always DB output.

## 3. Architecture / data flow
```
data/generate_data.py ──► data/raw/*.csv (6 messy files)
                               │
        ingestion.py    read all columns as text
        validation.py   schema check (fail fast) + 29 quality checks ──► validation_report
        cleaning.py     normalise / NULL / drop, every action logged ──► data_quality_issues
        transformation.py   shape tables, check foreign keys before load
        database.py     create SQLite from sql/schema.sql, load
                               │
                        data/placement.db   (dims + facts + 2 views)
                               │
        analytics.py    run_metric(name, branch, company)  ◄── sql/metrics.sql
                               ▲
        nl_query.py     question ─► IntentParser (Claude | rules) ─► validate_intent ─► run_metric
```
A longer design note is in [`docs/architecture.md`](docs/architecture.md).

## 4. Technologies
Python 3.10+ · pandas (cleaning) · SQLite (stdlib `sqlite3`) · pytest · Anthropic Claude API (optional,
for the NL parser) · python-dotenv. No web framework, ORM, vector DB or orchestration tool: they would
add complexity without helping this brief.

## 5. Project structure
```
campus-placement-analytics/
├── README.md  requirements.txt  .env.example  .gitignore  pytest.ini
├── data/
│   ├── generate_data.py        synthetic data generator (seeded)
│   └── raw/                    generated CSVs (committed, 204 KB; regenerated if missing)
├── src/
│   ├── config.py               paths + env settings
│   ├── ingestion.py            read raw CSVs as text
│   ├── validation.py           schema check + quality checks
│   ├── cleaning.py             cleaning rules + issue log
│   ├── transformation.py       final tables + integrity check
│   ├── database.py             create/load/connect (incl. read-only)
│   ├── analytics.py            loads metrics.sql, runs metrics by name
│   ├── nl_query.py             parsers, safety gate, answer_question()
│   └── main.py                 CLI
├── sql/  schema.sql  metrics.sql
├── tests/                      25 pytest cases (14 test functions, some parametrised)
└── docs/architecture.md
```

## 6. Setup (clean machine)
```bash
python3 -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                                    # optional: only needed for the Claude parser
```

## 7. Execution
```bash
python -m src.main all                    # pipeline + all metrics + sample questions (regenerates data if missing)
python -m src.main pipeline               # raw -> validated -> cleaned -> SQLite
python -m src.main report                 # all SQL metrics
python -m src.main ask "What is the participation rate for CSE students?"
python -m src.main ask "..." --backend rules|claude
python -m src.main demo-failures          # failure / edge-case demonstration
python data/generate_data.py --students 1000 --seed 1     # different dataset
python -m pytest -v                       # tests
```
**No API key?** Everything works; the NL feature uses the offline rule-based parser. Set
`ANTHROPIC_API_KEY` in `.env` to use Claude (`NL_BACKEND=auto` picks it automatically).

## 8. Database schema
Full DDL: [`sql/schema.sql`](sql/schema.sql).

| Table / view | Grain & key | Purpose |
|---|---|---|
| `dim_branch` | `branch_code` | 6 canonical branches |
| `dim_company` | `company_id` | name, sector |
| `dim_student` | `student_id` | branch, `cgpa`, `active_backlogs` (NULL = unknown) |
| `dim_drive` | `drive_id` | company, date, role, CTC, **eligibility rules** `min_cgpa`, `max_backlogs` |
| `bridge_drive_branch` | `(drive_id, branch_code)` | branches a drive is open to |
| `fact_registration` | `(student_id, drive_id)` | who registered |
| `fact_appearance` | `(student_id, drive_id)` | `PRESENT`/`ABSENT`; **FK to registration** |
| `fact_selection` | `(student_id, drive_id)` | `SELECTED`/`REJECTED`/`WAITLISTED`, CTC; **FK to appearance** + trigger requiring `PRESENT` |
| `validation_report`, `data_quality_issues` | audit | what was found / what was done |
| `v_student_drive_eligibility` | view | eligibility rule for every student × drive |
| `v_funnel` | view | 0/1 flag per funnel stage per student × drive |

Business rules in the schema: a selection cannot exist without a present appearance, an appearance
cannot exist without a registration, CTC is only allowed on `SELECTED`, CGPA is 0–10, backlogs ≥ 0.

**Metric definitions** (unit = a student × drive pair; funnel counts only *eligible* pairs):
eligible = CGPA ≥ min, backlogs ≤ max, branch allowed · participation rate = registered ÷ eligible ·
appearance rate = appeared ÷ registered · selection rate = selected ÷ appeared ·
funnel = eligible → registered → appeared → selected. Metrics: `eligibility`, `participation`,
`appearance`, `selection`, `funnel`, `branch_metrics`, `company_metrics`, `policy_exceptions`.

## 9. AI approach
The model is used for what it is good at, **understanding the question**, and nothing else.

- `ClaudeIntentParser` gets the question plus a *catalogue* (metric names + descriptions, valid branches,
  valid companies) and returns JSON: `{metric, filters:{branch, company}, confidence, reason}`.
  It handles paraphrases ("how many Mech students got offers from Bosch?"), abbreviations and synonyms
  ("Computer Science" → `CSE`).
- The model **never writes SQL and never sees student rows**. It can only pick a name from `metrics.sql`.
- Its reply is **untrusted** and passes `validate_intent`: known metric? sane confidence (≥ 0.6)?
  only `branch`/`company` filters? values actually exist in the DB? Failures return a status
  (`UNSUPPORTED`, `LOW_CONFIDENCE`, `INVALID_RESPONSE`, `UNKNOWN_ENTITY`, `PARSER_ERROR`, `INVALID_INPUT`)
  and **no SQL is run**.
- Queries run on a **read-only** SQLite connection with bound parameters, so even a bypassed check
  cannot modify data or inject SQL.
- Because numbers come from the database and not the model, there is nothing to hallucinate.
- `IntentParser` is the interface; `RuleBasedIntentParser` is the offline fallback and the deterministic
  test double. The tests also fake the Anthropic client, so no network is ever needed.

## 10. Tests (`python -m pytest -v` → 25 passed)
| Test | What it proves |
|---|---|
| `test_validation_detects_every_injected_problem_type` | validation finds duplicates, missing/invalid values, inconsistent labels, orphan/illogical events |
| `test_cleaning_normalises_values` | branch aliases, 3 date formats, impossible dates, `N/A`, decimal comma |
| `test_cleaned_data_has_no_duplicates_or_logical_violations` | no duplicate keys, CGPA in range, every repair logged |
| `test_database_enforces_integrity` | FK check is clean; schema rejects orphan appearance, CTC on rejection, CGPA 11 |
| `test_missing_required_column_stops_the_pipeline` | **failure case**: broken structure raises `SchemaError` |
| `test_sql_funnel_matches_independent_python_calculation` | SQL funnel = plain-Python recomputation from base tables; funnel is monotone |
| `test_rates_and_group_totals_are_consistent` | rates recomputed by hand; branch/company totals add up to the overall funnel |
| `test_metrics_are_dynamic_not_hard_coded` | delete 5 selections in a copy → metric drops by exactly 5 |
| `test_valid_question_is_answered_from_the_database` | NL answer equals the direct SQL result |
| `test_unsupported_questions_are_refused_not_guessed` | salary / "delete all students" / off-topic → `UNSUPPORTED`, no rows |
| `test_bad_or_low_confidence_model_output_is_rejected` (10 cases) | hallucinated metric, low/invalid confidence, unknown company/branch, SQL-injection string, extra filter keys, prose instead of JSON, `None` |
| `test_model_synonyms_are_resolved_to_real_values` | "Computer Science" / "infosys" → `CSE` / `Infosys` |
| `test_claude_parser_handles_garbage_and_api_failures` | fake client: JSON wrapped in text OK; garbage → `INVALID_RESPONSE`; API down → `PARSER_ERROR` |
| `test_nl_connection_is_read_only` | `DELETE` fails on the NL connection; empty question rejected |

**Edge-case demonstration:** `python -m src.main demo-failures` (structural failure + 7 unsafe AI cases).

## 11. Sample outputs
Seed 42, 600 students, 18 drives. (Your numbers will match because the generator is seeded.)

**Pipeline**
```
Raw rows     : {'students': 624, 'companies': 13, 'drives': 21, 'registrations': 2711, 'appearances': 2725, 'selections': 2219}
Loaded rows  : {'dim_student': 576, 'dim_drive': 18, 'fact_registration': 2518, 'fact_appearance': 2487, 'fact_selection': 2039, ...}
Validation   : 25 of 29 checks found problems; 701 cleaning actions logged
```
**Funnel** (`python -m src.main ask "Show the selection funnel"`)
```
stage_no  stage       students  pct_of_eligible  pct_of_previous_stage
1         Eligible    3175      100.0
2         Registered  2214      69.73            69.73
3         Appeared    1788      56.31            80.76
4         Selected    401       12.63            22.43
```
**Branch-wise**
```
branch_code  eligible  registered  appeared  selected  participation_rate_pct  selection_rate_pct
CIVIL        173       113         84        16        65.32                   19.05
CSE          771       515         417       93        66.8                    22.3
ECE          982       708         572       131       72.1                    22.9
EEE          569       378         307       62        66.43                   20.2
IT           477       347         283       70        72.75                   24.73
MECH         203       153         125       29        75.37                   23.2
```
**Natural language**
```
Q: How many ECE students are eligible for Bosch?
[OK] (parser: rules) metric=eligibility filters={'branch': 'ECE', 'company': 'Bosch'} confidence=0.90
D015  Bosch  Graduate Engineer Trainee  eligible_students=64  total_students=112  eligible_pct=57.14

Q: What is the selection rate for Zebra Corp?
[UNKNOWN_ENTITY] Unknown company 'Zebra Corp'. Known: Accenture, Amazon, Bosch, ...

Q: What is the average package offered?
[UNSUPPORTED] That question is not covered by the available metrics.

Q: Funnel   (simulated LLM replies with a SQL-injection string as company)
[UNKNOWN_ENTITY] Unknown company "x'; DROP TABLE dim_student;--". Known: ...
```

## 12. Assumptions
- Unknown CGPA/backlogs (missing or invalid) are stored as NULL and the student is **not eligible**: eligibility cannot be verified, and imputing would invent data.
- A student with no resolvable branch is dropped (eligibility depends on it); their events become orphans and are dropped.
- Non-ISO dates are **day-first** (`05/08/2025` = 5 Aug).
- Duplicates: keep the more complete row (registrations: the earliest dated one).
- A registration dated after the drive is invalid → dropped, and downstream appearance/selection rows are dropped with it (cascade, all logged).
- A drive missing its date, company or eligibility rules is dropped (cannot compute eligibility).
- Registrations by ineligible students are kept in the facts but excluded from the funnel and reported as `policy_exceptions` (12.07 % in the sample).
- Metrics count student × drive pairs, not distinct students: a student eligible for 10 drives counts 10 times.
- CTC on a non-selected row is set to NULL; a SELECTED row with no CTC stays NULL (no imputation).

## 13. Limitations
- Cleaning drops ~4 % of students (unknown/missing branch); a real system would send them to a review queue.
- The rule-based parser is keyword-based and can miss unusual phrasing; the Claude parser handles that, but the Claude path was verified only with a faked client in this environment, **not against the live API**.
- One metric and at most one branch + one company filter per question; no multi-turn context, date ranges or per-student answers (deliberate: privacy and safety).
- Branch/company aliases are hard-coded in `cleaning.py`; no fuzzy matching.
- Single batch load; the database is rebuilt on each run (no incremental/merge).
- Synthetic data uses real company names as labels only; its statistics are not real.

## 14. Production improvements
Incremental loads with upserts and a quarantine table for rejected rows; data-contract checks in CI
(e.g. Great Expectations/dbt tests); versioned metric definitions; Postgres/warehouse instead of SQLite;
per-user access control and row-level security; caching, rate limiting and audit logging of questions;
an evaluation set of questions to track parser accuracy across model versions; fuzzy entity matching
with a "did you mean…?" reply; a BI dashboard over the same views.
