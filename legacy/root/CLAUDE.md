# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository shape

This is not yet a unified codebase — it is three independent proof-of-concept apps living side by side under one folder, each implementing one module of the Automate-X PRD. They have separate dependency stacks, separate `.env` files, and (for `nl2sql/`) a separate git history. There is no root-level build/lint/test command; each module is run and tested independently from inside its own directory.

A staged plan for actually merging these into a real monorepo (shared structure, unified UI, PRD gap-closing) is written out in `STAGE_PROMPTS.md` — read it before doing large structural work here.

| Folder | PRD module | Stack |
|---|---|---|
| `manual/` | 모듈 1: AI 사내 지식정보 저장소 및 지능형 FAQ | Flask + Jinja, local keyword search (no LLM wired up) |
| `nl2sql/` | 모듈 2: NL2SQL 기반 자연어 데이터 추출 | Python package (`im_one_agent`) + LangGraph, own `http.server`-based web UI, own git repo (`origin` → `github.com/rkdals0203/im-one.git`) |
| `rpa/` | 모듈 3: 반복 행정 업무 스케줄러/자동 실행 에이전트 | Streamlit (`app.py`), ported from a static HTML/JS prototype (`j.html`) |
| `launcher/` | (PRD 모듈 아님) 테스트용 통합 진입점, 화면상 이름 "iMAX" | Flask + 카드형 대시보드 UI. 왼쪽 카드(1~6, 실제 연동은 1/2/3만) 클릭 시 해당 모듈을 백그라운드로 기동 후 오른쪽 패널에 iframe으로 표시 |

### launcher/ — 세 모듈을 하나씩 테스트하기 위한 임시 진입점

`cd launcher && python app.py` → `http://127.0.0.1:7000`. 왼쪽 사이드바의 모듈 카드를 클릭하면
해당 모듈을 필요 시 백그라운드 서브프로세스로 기동하고, 준비되면 오른쪽 화면에 표시한다. 화면에 보이는
제목/브랜딩은 "iMAX"(`launcher/static/title.png` — iM증권 실제 로고 파일, 손으로 그린 게 아니라 진짜 이미지)이지만,
프로젝트 코드명(폴더명, PRD상 명칭)은 여전히 Automate-X/AutoX다 — 둘을 혼동하지 않는다. 접힌 사이드바에서는
같은 이미지를 확대한 뒤 폭이 좁은 `overflow:hidden` 박스로 왼쪽 아이콘 부분만 잘라 보여준다(`.brand-logo-icon`,
`templates/index.html`) — 별도의 아이콘 전용 이미지 파일은 없다.

- 카드는 총 6개(1~6)이지만 **실제 백엔드와 연동되는 건 1(manual)/2(nl2sql)/3(rpa)뿐**이다. 4(CRM)/5(전자결제)/
  6(지속확장)은 향후 확장을 위해 미리 만들어 둔 자리표시자 카드로, 클릭해도 `/api/select`를 호출하지 않고
  (`templates/index.html`의 `PLANNED_MODULES` 맵으로 분기) 가운데 화면에 "지속 확장 영역" 안내만 보여준다.
  새 모듈을 실제로 연동하려면 `launcher/app.py`의 `MODULES`/`ALIASES`에 항목을 추가하고, 이 카드를
  `PLANNED_MODULES`에서 빼서 나머지 카드들과 같은 방식(`selectModule`이 `/api/select`를 호출하는 경로)을 타게 해야 한다.
- 모듈 1(manual, :5000)·모듈 2(nl2sql, :8765)·모듈 3(rpa, :8501) 모두 iframe으로 임베드된다.
- 모듈 2(nl2sql)는 원래 자체 CSP(`frame-ancestors 'none'`)로 모든 출처의 임베딩을 차단했으나,
  런처에서 오른쪽 패널에 바로 띄우기 위해 이 런처의 출처(`http://127.0.0.1:7000`)만 명시적으로
  허용하도록 완화했다 (`nl2sql/src/im_one_agent/web.py`의 `ALLOWED_FRAME_ANCESTOR`). 다른 출처에서의
  임베딩은 여전히 차단되며, `preflight.py`의 CSP 검증도 이 예외를 반영해 갱신되어 있다.
- 첫 화면은 사이드바가 아니라 **랜딩 페이지**(`#landing`, Genspark 워크스페이스 UI를 참고해 만든 로고+장식용
  검색창+"핵심 업무"(1~3)/"확장 영역"(4~6) 아이콘 그룹)다. `<body class="landing">`일 때는 CSS로 `#sidebar`/`#main`
  자체가 `display:none`이라 완전히 안 보이고, 랜딩의 아이콘 버튼을 클릭하면 JS가 `body`에서 `landing` 클래스를
  떼어내면서 그제서야 사이드바+실행화면 레이아웃이 나타난다. 사이드바 카드를 눌러 모듈을 재선택하는 기존 동작은
  이 전환 이후에만 의미가 있다(랜딩 상태에서는 사이드바가 숨겨져 있어 클릭할 수 없음).
- 사이드바 자체는(랜딩을 벗어난 뒤) 처음엔 아이콘+제목이 다 보이는 카드로 시작하고, 카드를 하나 클릭해 모듈을
  선택하는 순간 좁은 아이콘+제목 레일로 자동으로 접힌다(`#sidebar.collapsed`, `templates/index.html`의
  `collapseSidebar()`) — 오른쪽 실행 화면을 더 넓게 쓰기 위함이며, 맨 아래 토글 버튼으로 언제든 다시 펼칠 수 있다.
- 각 모듈의 venv가 이 머신에서 깨져 있어(원래 Unix식 `.venv/bin/` 구조로, Windows `Scripts/`가 없음)
  런처는 세 모듈 모두 전역 Python 환경(`pip install -e nl2sql`, `pip install -r rpa/requirements.txt`로 설치됨)을 사용한다.
- PRD가 말하는 "하나의 채팅/대시보드 인터페이스"의 최종 형태가 아니라, 3개 모듈을 순서대로
  개별 테스트하기 위한 임시 도구다. 실제 UI 통합 설계는 `STAGE_PROMPTS.md`의 3단계 프롬프트를 따른다.

`manual/CLAUDE.md` and `rpa/CLAUDE.md` already document their own module in depth (architecture, gotchas, working conventions) — read those directly rather than expecting duplicate detail here. `nl2sql/` has no CLAUDE.md yet; its README covers commands and layout, summarized below.

**Secrets**: `AutoX/.env` and `AutoX/manual/.env` contain real-looking API keys in plaintext. Never print, log, or commit their contents.

## manual/ — commands

```bash
pip install -r requirements.txt   # just flask
python app.py                     # serves at http://127.0.0.1:5000
```

No build/lint/test commands exist. See `manual/CLAUDE.md` for the retrieval architecture (`bond_manual.md` chunking/scoring) and the reloader gotcha (editing `bond_manual.md` requires a process restart; template edits don't).

## nl2sql/ — commands

```bash
pip install -e ".[dev]"                     # from nl2sql/, installs im_one_agent
python -m im_one_agent.cli --demo           # run bundled demo questions
python -m im_one_agent.cli --question "..." # single ad-hoc question
python -m im_one_agent.web                  # web UI at http://127.0.0.1:8765
pytest                                       # full test suite (pythonpath=src via pyproject.toml)
pytest tests/path_to_test.py::test_name     # single test
```

Evaluation / readiness gates (require LLM env vars to be configured — see below):

```bash
python -m im_one_agent.evaluate --output logs/evaluation_report.json --markdown-output logs/evaluation_summary.md
python -m im_one_agent.evaluate --strict-prd --output logs/evaluation_report.json   # gates PRD success-rate thresholds
python -m im_one_agent.preflight --profile poc     # or --profile pilot
python -m im_one_agent.evidence --output-dir logs/evidence_pack --profile poc --blocked-only
```

Env vars are loaded from `nl2sql/.env` automatically (override the path with `IM_ONE_ENV_FILE`); key ones: `OPENAI_API_KEY`, `IM_ONE_LLM_MODEL`, `IM_ONE_LLM_BASE_URL` (for a local OpenAI-compatible runtime), `IM_ONE_API_TOKEN` (gates `/api/query`, `/api/export`, `/api/metrics`), `IM_ONE_AUTH_MODE=trusted_headers` (SSO/gateway mode), `IM_ONE_DB_READONLY`, embedding-related `IM_ONE_EMBEDDING_*`.

**Stale editable-install gotcha**: on this machine a second, unrelated copy of this repo exists at `C:\Users\moon\AutoX` (outside `Last\`, alongside a same-named `.zip`). `pip`'s editable install for `im_one_agent` has pointed at that stale copy before — `pip show im_one_agent` reveals the actual `Editable project location`. When it's wrong, code edits under this repo's `nl2sql/` silently have no effect on anything launched via `python -m im_one_agent.web` (pytest is unaffected, since `pythonpath = src` in `pyproject.toml` makes it import the local `src/` directly regardless of the installed package). Fix by rerunning `pip install -e ".[dev]"` from *this* repo's `nl2sql/` directory.

### nl2sql/ architecture

The whole module is a LangGraph `StateGraph` pipeline (`src/im_one_agent/graph.py`, built via `build_agent()`) implementing PRD §3.2 Step 1–7:

1. **Intent / semantic layer** — business terms (신규 계좌, 고위험 상품, VOC, ELS) map to tables and metrics.
2. `schema_retrieval.py` — narrows to relevant tables/columns/metric definitions (local vector scoring by default; can use a configured embedding endpoint).
3. `sql_generator.py` — LLM-based SQL generation from the narrowed schema context. If no LLM endpoint is configured or the call fails, the agent returns a **blocked** execution state rather than running anything.
4. `sql_safety.py` — the validation/guardrail layer: blocks non-read-only statements, unauthorized tables, missing row limits, dangerous functions, cartesian joins, etc., before execution.
5. Execution runs against a fixed-seed synthetic SQLite mart (`sample_data.py` / `scripts/generate_demo_data.py`) — never real data (this repo is explicitly synthetic-only, per its README).
6. `response.py` — formats the natural-language explanation + result shape.
7. Every step is audit-logged; `web.py` exposes this over a one-page UI plus a JSON API (`/api/query`, `/api/export`, `/api/metrics`, `/api/feedback`, `/api/catalog`, `/api/audit-summary`, `/api/verified-questions`).

`evaluate.py`/`evaluation.py` measure the PRD's "≥80% execution accuracy" target against a gold-SQL case set; `--strict-prd` enforces the full PRD coverage gate (case counts, success rates, latency). `preflight.py` runs pre-demo/pre-pilot readiness checks (LLM reachability, API-token protection, read-only DB mode, synthetic-data-only policy on the mart).

## rpa/ — commands

```bash
pip install -r requirements.txt
streamlit run app.py
```

`j.html` is the original static-prototype version of the same app (no build step; open directly in a browser) — kept for reference, not the primary implementation.

`rpa/PRD.md` is the source of truth for business rules (budget codes, shortfall-split logic, approval rules); `rpa/CLAUDE.md` documents the chatbot state machine and data model in detail. When either the business rules or the chatbot behavior change, keep `PRD.md`, `CLAUDE.md`, and `app.py` in sync — this module has previously drifted (a PDF-proof rule applied inconsistently between the two) and the fix was to correct the doc to match the code, not the reverse, after confirming intent. Don't resolve a doc/code mismatch unilaterally in either direction — ask which one is correct.

There is no automated test suite for `rpa/`; verification is manual, by running the Streamlit app and exercising the flows listed in `rpa/CLAUDE.md`'s "Running / testing" section.
