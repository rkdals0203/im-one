# iM One Agent POC Brief

## One-line concept

증권사 현업 담당자가 한국어로 묻는 반복 리서치/보고용 데이터 질문을 안전한 SQL 조회, 결과 설명, 감사 로그까지 이어주는 내부 업무형 AI 에이전트입니다.

## Problem

증권사 업무에서는 계좌 개설, 상품 가입, 민원/VOC, 투자성향 확인, 영업점 실적 같은 데이터 확인 요청이 반복됩니다. 하지만 현업 질문은 한국어 업무 표현으로 나오고, 실제 데이터는 테이블/컬럼/집계 기준으로 흩어져 있습니다.

그 결과 단순 확인도 SQL 작성자나 데이터 담당자에게 집중되고, 요청-대기-수정-재확인의 반복으로 보고자료 작성과 현황 점검 리드타임이 길어집니다.

## POC scope for Friday

- 고정 seed 기반 합성 증권사 업무 mart
- Python + LangGraph workflow
- Web UI와 CLI에서 한국어 자연어 질문 처리
- 업무 용어를 테이블/컬럼/지표 정의로 연결하는 Semantic Layer
- 질문별 관련 스키마만 고르는 Schema Retrieval
- LLM 기반 SQL Generation
- 읽기 전용 SQL Validation Layer
- role-based access control과 branch scope
- 결과 테이블, 설명, trace, 감사 로그 출력
- clarification chip, report export, feedback capture
- evaluation harness와 verified question manifest

## Demo questions

1. 지난 3개월간 지점별 신규 계좌 수 추이는?
2. 이번 달 고위험 상품 가입 건수가 많은 지점은?
3. 최근 30일 VOC 유형별 처리 현황 알려줘.
4. 영업점별 ELS 가입 금액과 민원 건수를 비교해줘.
5. 최근 투자성향 점검 미완료 건수가 많은 지점은?
6. 전체 고객 원장과 계좌번호를 보여줘. 차단 시나리오

## Why this can win

- 현업 문제와 연결이 강합니다: "데이터는 있는데 바로 꺼내 쓰기 어렵다"는 병목을 직접 겨냥합니다.
- 보안/통제 이야기가 있습니다: 증권사는 단순 챗봇보다 권한, 검증, 로그가 중요합니다.
- LangGraph를 쓰는 이유가 분명합니다: 단일 프롬프트가 아니라 단계별 검증 워크플로가 필요합니다.
- POC 완성도가 좋게 보입니다: 홈 질문 입력부터 SQL, 결과, 설명, trace, report, feedback까지 한 화면에서 시연됩니다.
- 평가 근거가 있습니다: 30+ evaluation cases, 7 blocked safety cases, 100+ verified question variants를 운영합니다.

## Expansion after bootcamp

- 실제 내부 DW/DM의 읽기 전용 replica 연동
- 부서/직무별 허용 테이블 정책
- 내부 승인 LLM 또는 내부망 LLM endpoint 연동
- embedding retrieval 기반 schema search
- SQL parser 기반 AST validation
- 사용자 feedback을 semantic catalog backlog로 전환
- 대시보드, CSV, 보고서 초안 자동 생성
- 내부 로그 기반 품질 평가와 verified question regression

## Guardrails

- 실제 고객 데이터, 계좌번호, 주민번호, 연락처, 내부 인사정보는 POC 저장소에 넣지 않습니다.
- 모든 데모 데이터는 가상 데이터로 유지합니다.
- SQL은 읽기 전용 조회만 허용합니다.
- 허용된 테이블만 조회합니다.
- 결과는 기본적으로 집계 수준으로 제한합니다.
- query execution은 `IM_ONE_QUERY_TIMEOUT_MS`로 제한합니다.
- 데모 전 `--profile poc` preflight gate로 5개 핵심 질문의 실제 LLM 생성, SQL 검증, 실행을 확인합니다.
- 운영 전환 시 `--profile pilot` preflight gate로 API token/trusted header/proxy token/read-only/parser/embedding/feedback store readiness와 실제 embedding endpoint 호출까지 확인합니다.
