# Architecture & Design Decisions

**Goal.** Make messy placement-drive data trustworthy, measure eligibility → participation → selection
in SQL, and let non-technical users ask questions without giving a language model database access.

## Pipeline
`raw CSV → ingestion → validation → cleaning → transformation → SQLite → analytics → NL query`.
Each stage is one small module with one job, so any stage can be explained, tested or replaced alone.

## Key decisions

**1. Read raw data as text; validate before cleaning.**
Letting pandas guess types turns `"N/A"` into NaN and `"8.5x"` into an error, hiding the problem.
Validation *reports* (29 checks, stored in `validation_report`); cleaning *acts* and logs each action in
`data_quality_issues`. Structural problems (missing file/column) stop the run; row-level problems never do.
*Trade-off:* a few rules (label maps, date parsing) are shared between the two modules.

**2. Fix, NULL or drop, by what is safe.**
Label variants are normalised (safe). A CGPA of `85` becomes NULL rather than a guess (honest unknown).
A selection for a student who never appeared is dropped (logically impossible). Unknown CGPA means "not
eligible", because eligibility cannot be proven.

**3. Star-style model with the rules inside the database.**
Dimensions hold attributes and *eligibility rules* (`min_cgpa`, `max_backlogs`, allowed branches on the
drive). Three facts at student × drive grain form a chain, each with a foreign key to the previous one,
so "selected but never registered" is *unrepresentable*. A trigger and CHECK constraints cover what a
foreign key cannot. SQLite was chosen because it needs no server; SQL is plain ANSI and ports easily.

**4. Eligibility and the funnel are views, not stored data.**
`v_student_drive_eligibility` cross-joins students and drives and applies the rule; `v_funnel` adds a 0/1
flag per stage. Change a drive's CGPA cut-off and every metric follows. Nothing analytical is hard-coded.
Funnel stages multiply flags (`is_eligible * is_registered * …`), which guarantees each stage is a subset
of the previous one. *Trade-off:* the cross join is O(students × drives); fine at campus scale, would be
materialised or indexed in production.

**5. Named metrics in a SQL file, run through one function.**
`metrics.sql` holds parameterised queries; `analytics.run_metric(name, branch, company)` is the only way to
run them. The CLI and the AI feature share this path, so there is a single definition of every metric.

**6. AI as an intent router, not a SQL author.**
*Why not text-to-SQL?* It needs broad schema access, is hard to constrain, can produce plausible but wrong
joins, and cannot be exhaustively tested. Here the model returns `{metric, filters, confidence}` chosen
from a closed catalogue. This still uses the model for what rule matching does poorly (paraphrase,
synonyms, entity names) while the answer stays a deterministic database result.

**7. Treat model output as untrusted input.**
`validate_intent` checks the metric against the catalogue, confidence against a threshold, filter keys
against an allow-list, and filter *values* against what exists in the database. Every failure maps to an
explicit status. Parameters are bound (never concatenated) and the connection is read-only, so there are
three independent layers: closed vocabulary → validation → read-only execution.

**8. One interface, two implementations.**
`IntentParser.parse(question, context)` is implemented by `ClaudeIntentParser` and
`RuleBasedIntentParser`. The rules parser gives the project a working offline mode and makes tests
deterministic; the Claude parser's client is injectable, so tests simulate bad replies and API outages
without network access.

**9. A thin UI, no logic in it.**
`app.py` (Streamlit) is a second front end next to the CLI. It calls only `run_metric()` and
`answer_question()`, so there is still one definition of every metric and one safety path for questions.
Streamlit was chosen because it is pure Python and needs no separate frontend build or API layer.

## What was deliberately left out
No ORM, orchestrator, vector store, agent framework, REST API or LLM-written narrative. The brief asks for
a pipeline, SQL metrics and a safe NL feature; the UI is a thin optional layer, and anything beyond that is
complexity a reviewer would have to understand and defend.

## Known risks
Answers are only as good as the cleaning rules (e.g. ~4 % of students are dropped for lacking a branch).
The rule parser can mis-route unusual wording (mitigated by an explicit "unsupported measure" list). The
live-API parser is verified by simulation only.
