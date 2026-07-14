import { BookOpenText, ChartNoAxesCombined, Check, ReceiptText } from "lucide-react";
import { useNavigate } from "react-router";
import { useAppState } from "../app-state";
import { AssistantComposer } from "../components/AssistantComposer";

const capabilities = [
  {
    path: "/knowledge",
    icon: BookOpenText,
    title: "업무지식",
    description: "채권과 사내 업무 매뉴얼에서 근거를 찾아 답합니다.",
    example: "회의실 예약 절차를 알려줘",
  },
  {
    path: "/data",
    icon: ChartNoAxesCombined,
    title: "데이터 분석",
    description: "자연어 질문을 검증된 조회와 차트로 바꿉니다.",
    example: "지난 3개월간 지점별 신규 계좌 수 추이는?",
  },
  {
    path: "/expenses",
    icon: ReceiptText,
    title: "지출품의",
    description: "법인카드 품의와 승인, 예산 확인을 안전하게 처리합니다.",
    example: "스타벅스 88,000원 회의비 품의해줘",
  },
];

export function HomePage() {
  const navigate = useNavigate();
  const { progress, loading, error, clarification } = useAppState();

  return (
    <div className="home-page">
      <section className="home-center">
        <img className="home-logo" src="/title.png" alt="iMAX" />
        <p className="home-kicker">사내 업무 효율을 위한 하나의 통합 창구</p>
        <h1>무엇을 도와드릴까요?</h1>
        <p className="home-subtitle">업무 언어로 질문하면 알맞은 에이전트가 이어서 처리합니다.</p>
        <div className="home-composer-wrap">
          <AssistantComposer
            large
            autoFocus
            placeholder="업무 매뉴얼, 데이터 조회, 지출품의를 편하게 질문하세요"
          />
          {loading && progress.length > 0 && (
            <div className="progress-line" aria-live="polite"><Check size={14} /> {progress.at(-1)}</div>
          )}
          {error && <div className="error-banner" role="alert">{error}</div>}
        </div>

        {clarification && (
          <div className="clarification-strip">
            <span>{clarification.message}</span>
            <div>
              {clarification.options.map((option) => (
                <button key={option.workspace} onClick={() => navigate(`/${option.workspace === "expense" ? "expenses" : option.workspace}`)}>
                  {option.label}
                </button>
              ))}
            </div>
          </div>
        )}

        <div className="capability-grid">
          {capabilities.map(({ path, icon: Icon, title, description, example }) => (
            <button key={path} className="capability-card" onClick={() => navigate(path)}>
              <span className="capability-icon"><Icon size={21} /></span>
              <strong>{title}</strong>
              <span>{description}</span>
              <small>{example}</small>
            </button>
          ))}
        </div>
      </section>
    </div>
  );
}
