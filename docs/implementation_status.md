# iMAX 통합 앱 구현 현황

Updated: 2026-07-14

## 완료

- 기존 NL2SQL 변경사항과 launcher, manual, rpa 스냅샷을 원격 백업 브랜치와 annotated tag로 보존했습니다.
- React 19, Vite 7, React Router 7 기반 단일 웹 앱으로 홈과 세 전문 업무 화면을 구성했습니다.
- FastAPI가 API와 React 빌드 결과를 한 주소에서 제공합니다.
- LangGraph Supervisor가 입력 정규화, 의도 분류, 전문 서브그래프, 결과 조립을 수행합니다.
- 업무지식 에이전트는 검색 근거와 파일·섹션 인용을 반환합니다.
- NL2SQL 에이전트는 Semantic Layer, Schema Retrieval, LLM SQL 생성, SQL Validation, 역할·지점 필터, 실행, 설명, 감사 로그를 유지합니다.
- 지출품의 에이전트는 확인 토큰, PDF 근거 검증, 예산 규칙, 중복 실행 방지를 적용합니다.
- 앱 세션, LangGraph 체크포인트, 조회 대상 데이터베이스를 분리했습니다.
- 데이터 결과는 설명과 추천 차트를 우선 표시하고 SQL·스키마·trace는 분석 근거 drawer에 숨겼습니다.
- 큰 결과 표는 가상 스크롤과 내부 스크롤 영역을 사용해 viewport 하단이 잘리지 않습니다.
- 390px, 768px, 1440px, 1920px 반응형 화면과 라이트·다크 모드를 검증합니다.
- OpenAPI에서 TypeScript API 타입을 생성합니다.
- 개발자의 `.env`, 실행 DB, 체크포인트, 로그, 업로드 파일은 Git과 테스트에서 격리됩니다.

## 실행 경계

- 부트캠프 시연용 로컬 POC가 현재 범위입니다.
- 데이터와 지출 초기값은 모두 합성 데이터입니다.
- 사내 SSO는 포함하지 않지만 trusted header와 API token 확장 지점을 유지합니다.
- 모델은 `IM_ONE_LLM_MODEL`, `IM_ONE_LLM_BASE_URL`, `OPENAI_API_KEY`로만 설정합니다.

## 복구 지점

- 브랜치: `backup/pre-react-unification-20260714`
- 태그: `pre-react-unification-20260714`
