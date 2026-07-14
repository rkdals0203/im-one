import { BookOpenText, ExternalLink, Sparkles } from "lucide-react";
import type { ReactNode } from "react";
import { useAppState } from "../app-state";
import { AssistantComposer } from "../components/AssistantComposer";

export function KnowledgePage() {
  const { knowledge, loading, progress, error } = useAppState();

  return (
    <div className="workspace-page">
      <WorkspaceHeading
        eyebrow="Knowledge agent"
        title="업무지식"
        description="사내 매뉴얼의 근거를 확인하고 필요한 절차만 정리합니다."
        icon={<BookOpenText size={21} />}
      />
      <div className="workspace-composer"><AssistantComposer hint="knowledge" placeholder="화면번호, 처리 절차, 회의실 예약 등을 질문하세요" /></div>
      <StatusArea loading={loading} progress={progress} error={error} />

      {knowledge ? (
        <div className="knowledge-layout">
          <article className="answer-panel">
            <div className="answer-label"><Sparkles size={16} /> 답변</div>
            <div className="answer-copy">{knowledge.answer}</div>
            <div className="answer-meta">근거 {knowledge.citations.length}개 · {knowledge.generationEngine === "llm" ? "AI 정리" : "매뉴얼 검색"}</div>
          </article>
          <aside className="citation-panel">
            <div className="section-heading"><h2>확인한 근거</h2><span>{knowledge.citations.length}</span></div>
            <div className="citation-list">
              {knowledge.citations.map((citation, index) => (
                <article className="citation-card" key={`${citation.source}-${citation.section}`}>
                  <div><span>[{index + 1}]</span><strong>{citation.section}</strong></div>
                  <p>{citation.excerpt}</p>
                  <small><ExternalLink size={12} /> {citation.source}</small>
                </article>
              ))}
            </div>
          </aside>
        </div>
      ) : (
        <EmptyWorkspace icon={<BookOpenText size={24} />} title="찾고 싶은 업무를 질문하세요" description="채권 매뉴얼과 회의실 예약 안내가 준비되어 있습니다." />
      )}
    </div>
  );
}

export function WorkspaceHeading({
  eyebrow,
  title,
  description,
  icon,
  actions,
}: {
  eyebrow: string;
  title: string;
  description: string;
  icon: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <header className="workspace-heading">
      <div className="workspace-title-icon">{icon}</div>
      <div><span>{eyebrow}</span><h1>{title}</h1><p>{description}</p></div>
      {actions && <div className="workspace-actions">{actions}</div>}
    </header>
  );
}

export function StatusArea({ loading, progress, error }: { loading: boolean; progress: string[]; error?: string }) {
  if (error) return <div className="error-banner" role="alert">{error}</div>;
  if (!loading) return null;
  return <div className="workspace-progress"><span className="status-pulse" />{progress.at(-1) ?? "처리를 시작하고 있습니다"}</div>;
}

export function EmptyWorkspace({ icon, title, description }: { icon: ReactNode; title: string; description: string }) {
  return <div className="empty-workspace"><span>{icon}</span><h2>{title}</h2><p>{description}</p></div>;
}
