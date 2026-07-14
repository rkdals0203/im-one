# iMAX API

FastAPI와 LangGraph로 구성한 iMAX 통합 백엔드입니다. 하나의 Supervisor가 질문을 분류하고 업무지식, 데이터 분석, 지출품의 서브그래프로 연결합니다.

## 구성

- `src/imax_api`: FastAPI 라우터, Supervisor, 세션·체크포인트, 업무지식·지출 서비스
- `src/im_one_agent`: Semantic Layer, Schema Retrieval, LLM SQL 생성, SQL 검증·실행·감사 로그
- `resources/manual`: 합성 데모용 매뉴얼 지식
- `resources/expense_seed.json`: 합성 지출품의 초기 데이터
- `tests`: API, LangGraph, NL2SQL 안전성, 권한, 재시작 복원 테스트

앱 상태 DB, LangGraph 체크포인트 DB, NL2SQL 조회 대상 DB는 서로 분리됩니다. 실제 고객·계좌·임직원 데이터는 저장소에 넣지 않습니다.

## 실행

저장소 루트에서 실행합니다.

```bash
make bootstrap
make dev
```

단일 URL 프로덕션 빌드:

```bash
make start
```

브라우저와 API는 `http://127.0.0.1:8000`에서 함께 제공됩니다. API 명세는 `/docs`, 상태 확인은 `/api/v1/health`에서 볼 수 있습니다.

## 주요 API

- `POST /api/v1/assistant/messages`: SSE 기반 통합 질문 처리
- `GET /api/v1/sessions/{sessionId}`: 저장된 대화 복원
- `POST /api/v1/knowledge/query`: 근거가 있는 매뉴얼 검색
- `POST /api/v1/data/query`: 검증된 NL2SQL 조회
- `POST /api/v1/data/export`: CSV 또는 Markdown 보고서 내보내기
- `GET /api/v1/data/catalog`: 역할별 Semantic Layer 조회
- `GET /api/v1/expenses/overview`: 지출·예산 현황
- `POST /api/v1/expenses/actions`: 확인 토큰과 중복 방지 키를 사용한 변경 실행
- `POST /api/v1/expenses/evidence`: 회의록 PDF 첨부

모델과 경로는 루트 `.env`에서만 설정합니다. 예시는 루트 `.env.example`을 따릅니다. 테스트는 개발자의 `.env`를 읽지 않으며 실제 모델 호출도 하지 않습니다.

```bash
make test-api
```
