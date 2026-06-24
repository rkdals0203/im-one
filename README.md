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

Run tests:

```bash
pytest
```

If package installation is blocked in a classroom or internal network, the local fallback runner can still execute the demo without LangGraph installed:

```bash
PYTHONPATH=src python -m im_one_agent.cli --demo
```

When LangGraph is installed, the same command path uses the LangGraph workflow.

## Project layout

```text
src/im_one_agent/
  cli.py              # command-line demo entrypoint
  graph.py            # LangGraph workflow
  sample_data.py      # synthetic SQLite database setup
  schema_retrieval.py # semantic/schema retrieval layer
  sql_generator.py    # deterministic POC SQL generator
  sql_safety.py       # SQL validation guardrails
  response.py         # user-facing answer/explanation formatting
docs/
  poc_brief.md        # business story and Friday POC scope
  demo_script.md      # suggested presentation flow
```

## Notes for the bootcamp

The current SQL generation node is deterministic so the POC can run without an LLM API key. During the bootcamp, this node can be replaced with an LLM-powered node while keeping the same safety workflow around it.

The LangGraph workflow follows the current Graph API pattern from the official LangGraph docs: `StateGraph`, nodes, edges, conditional routing, `compile()`, and `invoke()`.
