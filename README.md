# iM One Agent POC

AI agent bootcamp POC for a securities-company workflow: a Korean natural-language-to-SQL agent that helps business users explore internal operational data safely.

This repository uses only synthetic demo data. Do not add real customer, account, transaction, employee, or internal confidential data.

## POC idea

Many securities-company reporting requests start with a business question:

- "지난 3개월간 지점별 신규 계좌 수 추이는?"
- "이번 달 고위험 상품 가입 건수가 많은 지점은?"
- "최근 30일 VOC 유형별 처리 현황 알려줘."
- "영업점별 ELS 가입 금액과 민원 건수를 비교해줘."

Today these requests often require someone to know the database schema, write SQL, check the result, and explain assumptions. The POC turns that into a controlled agent workflow:

1. Semantic Layer: map business terms such as 신규 계좌, 고위험 상품, VOC, ELS to database tables and metrics.
2. Schema Retrieval: select only relevant tables, columns, metric definitions, and sample queries.
3. SQL Generation: create a read-only SQL query from the narrowed context.
4. SQL Validation: block unsafe statements, unauthorized tables, missing limits, and risky patterns before execution.
5. Query Execution: run validated SQL against a demo SQLite database.
6. Explanation + Audit Log: explain criteria, referenced tables, and result shape while recording the question and SQL.

## Quick start

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -e ".[dev]"
python -m im_one_agent.cli --question "지난 3개월간 지점별 신규 계좌 수 추이는?"
```

Run the bundled demo questions:

```bash
python -m im_one_agent.cli --demo
```

Run the one-page web UI:

```bash
python -m im_one_agent.web
```

Then open `http://127.0.0.1:8765`.

Use `.env.example` as the POC/Pilot readiness checklist for LLM, auth,
read-only database, feedback-store, and embedding settings. CLI, web,
evaluation, preflight, and evidence commands load `.env` automatically without
overwriting values already exported in the shell. Set `IM_ONE_ENV_FILE` to load
a different local env file.

Enable LLM SQL generation:

```bash
export OPENAI_API_KEY="..."
export IM_ONE_LLM_MODEL="gpt-5.6-luna"
export IM_ONE_LLM_TIMEOUT="10"
python -m im_one_agent.web
```

Use a localhost OpenAI-compatible runtime:

```bash
export IM_ONE_LLM_BASE_URL="http://127.0.0.1:11434/v1"
export IM_ONE_LLM_MODEL="local-nl2sql"
export IM_ONE_LLM_ALLOW_LOCAL_NO_AUTH="1"
export IM_ONE_LLM_TIMEOUT="10"
python -m im_one_agent.web
```

The SQL generation node calls an approved OpenAI-compatible LLM endpoint and the
SQL Validation Layer gates execution. If the LLM endpoint is not configured or
the call fails, the agent returns a blocked execution state instead of running SQL.

Optional API protection and monitoring:

```bash
export IM_ONE_API_TOKEN="local-demo-token"
python -m im_one_agent.web
```

When `IM_ONE_API_TOKEN` is set, `/api/query`, `/api/export`, and `/api/metrics`
require `Authorization: Bearer <token>` or `X-IM-One-Token`. `/api/health`
remains available for minimal health checks and only returns runtime model,
base URL, and database-path details when a valid credential is supplied.
Authorized monitoring endpoints include `/api/metrics` and `/api/audit-summary`.
The audit summary groups executions by validation status, execution status,
role, model, semantic metric, referenced table, and blocked reason.
The role-scoped semantic catalog is available at `/api/catalog?role=<role>` and
is rendered in the Workbench context panel.
When retrieval confidence is low, the Workbench AI panel renders clarification
chips that can be clicked to rerun the question with a more explicit metric.
Report export produces a Markdown draft with semantic metrics, referenced
schema, validation evidence, execution trace, SQL, result preview, and synthetic
data caution text.
Query execution is bounded by `IM_ONE_QUERY_TIMEOUT_MS` and defaults to 10000 ms.

Feedback for semantic-layer improvement can be posted to `/api/feedback`.
Feedback is appended to `logs/feedback.jsonl` by default, or to
`IM_ONE_FEEDBACK_PATH` when configured. The Workbench AI panel includes compact
feedback controls that send the current session context with the note.
Authorized users can review the aggregated semantic-layer backlog through
`/api/feedback-summary`.
The left-rail Catalog view exposes role-scoped semantic metrics, allowed
tables, role coverage, and catalog governance issues for semantic-layer
management.

For an internal SSO/API-gateway setup, enable trusted header mode:

```bash
export IM_ONE_AUTH_MODE="trusted_headers"
python -m im_one_agent.web
```

In this mode the gateway must provide `X-IM-One-User`, and may provide
`X-IM-One-Role` and `X-IM-One-Branch-ID`. Those values are used for RBAC scope
and audit logging.

Run tests:

```bash
python -c "from im_one_agent.graph import build_agent; print(type(build_agent()).__name__)"
pytest
```

The LangGraph runtime check should print `CompiledStateGraph` in the activated
project environment.

Run the evaluation set:

```bash
python -m im_one_agent.evaluate \
  --output logs/evaluation_report.json \
  --markdown-output logs/evaluation_summary.md
```

The evaluation runner uses the same LangGraph workflow and requires the LLM
environment to be configured for successful non-blocked cases.
Use `--case-group block`, `--blocked-only`, `--non-blocked-only`, or repeated
`--case-id <id>` filters to generate focused safety/core/follow-up evidence.

Gate the PRD POC success metrics after configuring the LLM:

```bash
python -m im_one_agent.evaluate --strict-prd --output logs/evaluation_report.json
```

`--strict-prd` requires full PRD coverage before the success rates are trusted:
at least 30 total cases, 5 core demo cases, 30 non-blocked/gold-compared
cases, 2 blocked safety cases, 100% core demo success, at least 70%
non-blocked execution success, 100% blocked-query rejection, and all evaluated
cases under the 10-second latency target. Focused runs such as `--case-group
core` remain useful for debugging but fail the strict PRD gate.

Export the verified question manifest used for analyst review and regression:

```bash
python -m im_one_agent.evaluate \
  --output logs/evaluation_report.json \
  --markdown-output logs/evaluation_summary.md \
  --verified-output logs/verified_questions.json
```

The same manifest is available from `/api/verified-questions` for authorized
users. It includes the evaluation gold-SQL cases plus paraphrased verified
variants, keeping the pilot question bank above 100 entries.

Build a single review bundle with readiness, external-readiness, completion
audit, audit-log, evaluation, verified-question, catalog, and governance
evidence:

```bash
python -m im_one_agent.evidence \
  --output-dir logs/evidence_pack \
  --profile poc \
  --blocked-only
```

Add `--live-checks` only when the approved LLM and embedding endpoints are
configured and should be called during evidence generation.

The evidence pack writes `external_readiness.json` and
`external_readiness_commands.sh` with each external gate's required environment
keys, verification command, and evidence expectation. For example, the POC LLM
gateway item points reviewers to:

```bash
python -m im_one_agent.evidence --profile poc --live-checks --strict
```

The generated command script preserves the evidence run's DB path, audit path,
role, and branch scope. Focused case filters such as `--blocked-only` are not
carried over because external-gate replay should generate full PRD evidence.
The replay script uses `--strict` for profile-level evidence commands, so
automation exits non-zero when required readiness or PRD evaluation gates still
fail.
The evidence pack manifest also includes a single `evidence_gate` result that
combines readiness, external readiness, and PRD evaluation thresholds.
`evaluation_diff_summary.json` records only failed or gold-mismatched evaluation
cases, including missing tables, missing columns, missing SQL fragments,
row-count deltas, first mismatch details, issues, generated SQL, and gold SQL.
It also writes `completion_audit.json`, which summarizes each mapped PRD
requirement, the checks still requiring attention, external evidence gaps, and
the blocking conditions that prevent claiming full PRD completion. The audit
attaches PRD evaluation-threshold failures to `FR-012`, so a safety-only or
partial evaluation run cannot mark the Evaluation Harness requirement complete.
The audit's final `passed` value requires both the integrated evidence gate and
every mapped PRD/NFR requirement to pass.
The bundle also copies the run's JSONL audit evidence into `audit_log.jsonl`,
including PRD audit fields such as original question, generated SQL, validation
status, execution status, row count, and blocked reason. Audit events also
separate the raw LLM-generated SQL from policy-applied and validated SQL when
role policy rewrites the query before execution. `audit_summary.json`
adds the same grouped monitoring view used by `/api/audit-summary`, including
execution status, validation status, role, generation engine, metric, table, and
blocked-reason counts. `database_audit_log.json` snapshots the SQLite
`query_audit_log` rows when the audit table is available.
`sql_validation_probes.json` records the SQL Validation Layer probe set,
including read-only enforcement, DML/DDL blocking, unknown table/column checks,
`SELECT *`, row-limit, operational-table, dangerous-function, cartesian-join,
raw-detail, and branch-scope cases.
`schema_retrieval_probes.json` records the Schema Retrieval probes, including
selected tables, matched metrics, confidence, clarification options, retrieval
scores, role-policy exclusion, and follow-up context merging.
`query_execution_samples.json` records validated SQLite execution samples,
including column metadata, row count, pre-execution row-count checks, query-plan
summary, empty-result handling, and branch-scoped execution.
`role_policy_matrix.json` records the role-to-table policy matrix plus probes
for role-disallowed tables, branch-manager scope enforcement, schema-retrieval
exclusion, and operational audit-table blocking.
`llm_prompt_contract.json` records the LLM prompt payload contract, including
selected schema, matched metric definitions, role policy, SQL rules, synthetic
dataset metadata, deterministic generation parameters, explicit response
contract, sanitized follow-up context, and core demo prompt contracts.
`live_llm_generation_samples.json`
records per-core-question live LLM generation, validation, execution, latency,
result-shape, prompt payload hash, response-contract checks, query-plan and
pre-execution row-count metadata, and missing-table/column evidence when
`--live-checks` is enabled.
`llm_evaluation_diagnostics.json` summarizes the remaining LLM/evaluation gate
state, sanitized endpoint configuration, failure-reason counts, sample failed
cases, and exact verification commands needed to close the PRD gates.
`result_explanation_samples.json` records allowed and blocked response
explanation samples with metric, period, grouping, table, validation, row-count,
assumption, and synthetic-data notice coverage.
`ui_layout_contract.json` records the static UI layout contract for the home
entry flow, desktop workbench grid, result-table scroll safety, AI chat/trace
panel, responsive breakpoints, and dynamic result-height calculation.
The Web Monitoring view also shows POC/Pilot `readiness_gate` status and flags
profiles that still need live checks, full PRD evaluation evidence, or restored
evaluation coverage.

Generate the fixed-seed synthetic mart and optional snapshots:

```bash
python scripts/generate_demo_data.py \
  --db-path data/im_one_demo.sqlite \
  --csv-dir data/snapshots/csv \
  --gold-output data/snapshots/gold_results.json
```

The generator recreates the SQLite demo database, stores synthetic-dataset
metadata in `demo_dataset_metadata`, can export table-level CSV snapshots, and
can write gold result snapshots for the evaluation set.

Run operational preflight checks:

```bash
python -m im_one_agent.preflight --require-llm --require-api-token
python -m im_one_agent.preflight --profile pilot --json --output logs/preflight_pilot.json
```

Preflight also validates the synthetic demo mart policy and Web UI readiness:
business tables must not contain sensitive columns or email, phone,
resident-registration-number, or account-number patterns, the mart must carry
synthetic POC metadata, and the static app must use local scripts/styles/icons
with a same-origin CSP.

Apply grouped readiness profiles:

```bash
python -m im_one_agent.preflight --profile poc
python -m im_one_agent.preflight --profile pilot
```

Standard preflight requires SQL parser readiness because `sqlglot` is a project
dependency. `poc` additionally requires live LLM generation for the five core
demo questions. `pilot` adds API-token protection, trusted-header auth with
`IM_ONE_TRUSTED_PROXY_TOKEN`, read-only DB mode, embedding configuration,
writable feedback backlog store, and live embedding endpoint checks.

To call the configured LLM gateway and verify the five core demo questions
through SQL generation, validation, and SQLite execution before a demo:

```bash
python -m im_one_agent.preflight --require-llm --check-llm
```

For a trusted-header deployment gate:

```bash
IM_ONE_AUTH_MODE=trusted_headers IM_ONE_TRUSTED_PROXY_TOKEN=proxy-secret \
  python -m im_one_agent.preflight --require-trusted-auth --require-trusted-proxy-token
```

To verify the feedback backlog store before a pilot:

```bash
python -m im_one_agent.preflight --require-feedback-store
```

Enable an approved OpenAI-compatible embedding endpoint for schema retrieval:

```bash
export OPENAI_API_KEY="..."
export IM_ONE_EMBEDDING_MODEL="text-embedding-3-small"
python -m im_one_agent.preflight --require-embedding --check-embedding
```

Use a localhost embedding runtime without an API key:

```bash
export IM_ONE_EMBEDDING_BASE_URL="http://127.0.0.1:11434/v1"
export IM_ONE_EMBEDDING_MODEL="local-embedding"
export IM_ONE_EMBEDDING_ALLOW_LOCAL_NO_AUTH="1"
python -m im_one_agent.preflight --require-embedding --check-embedding
```

Without embedding configuration, schema retrieval uses the bundled local vector
scoring path.

Use `IM_ONE_DB_READONLY=1` when pointing the app at a read-only SQLite replica:

```bash
export IM_ONE_DB_READONLY=1
python -m im_one_agent.preflight --db-path data/im_one_demo.sqlite --expect-read-only
```

## Project layout

```text
src/im_one_agent/
  cli.py              # command-line demo entrypoint
  evaluate.py         # evaluation set runner
  evaluation.py       # evaluation cases and report writer
  generate_demo_data.py # demo mart, CSV snapshot, and gold snapshot generator
  preflight.py        # operational readiness checks
  graph.py            # LangGraph workflow
  web.py              # one-page local web UI server
  sample_data.py      # fixed-seed synthetic mart generator
  schema_retrieval.py # semantic/schema retrieval layer
  sql_generator.py    # LLM SQL generator
  sql_safety.py       # SQL validation guardrails
  response.py         # user-facing answer/explanation formatting
  static/             # database-console web UI assets
docs/
  poc_brief.md        # business story and Friday POC scope
  demo_script.md      # suggested presentation flow
  im_bank_design_research.md # iM Bank color research notes
  database_saas_ui_references.md # database SaaS UI reference notes
```

## Notes for the bootcamp

The SQL generation node is LLM-based. The demo data is a fixed-seed synthetic
mart with branches, accounts, product sales, VOC cases, investment reviews,
branch targets, and an audit-log table shape.

The LangGraph workflow follows the current Graph API pattern from the official LangGraph docs: `StateGraph`, nodes, edges, conditional routing, `compile()`, and `invoke()`.
