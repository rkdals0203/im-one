import { Check, Paperclip, ReceiptText, WalletCards, X } from "lucide-react";
import { useEffect } from "react";
import { useAppState } from "../app-state";
import { AssistantComposer } from "../components/AssistantComposer";
import { EmptyWorkspace, StatusArea, WorkspaceHeading } from "./KnowledgePage";

export function ExpensePage() {
  const {
    expense,
    loading,
    progress,
    error,
    refreshExpense,
    confirmExpense,
    attachExpenseEvidence,
  } = useAppState();

  useEffect(() => {
    if (!expense) refreshExpense().catch(() => undefined);
  }, [expense, refreshExpense]);

  const pending = expense?.overview.pendingAction;
  const evidenceRequired = pending?.type === "create" && pending.draft?.requiresMinutes && !pending.draft.evidencePath;

  return (
    <div className="workspace-page expense-workspace">
      <WorkspaceHeading
        eyebrow="Expense agent"
        title="지출품의"
        description="법인카드 품의부터 부서장 승인과 예산 확인까지 이어서 처리합니다."
        icon={<ReceiptText size={21} />}
      />
      <div className="workspace-composer"><AssistantComposer hint="expense" placeholder="예: 7월 14일 스타벅스 88,000원 회의비 품의해줘" /></div>
      <StatusArea loading={loading} progress={progress} error={error} />

      {expense ? (
        <div className="expense-layout">
          <section className="expense-response">
            <span className="result-status">업무 검토</span>
            <h2>{expense.message}</h2>
          </section>

          {pending && (
            <section className="pending-action-panel">
              <div className="pending-head"><span><WalletCards size={17} /> 확인이 필요합니다</span><small>{pending.type === "create" ? "품의 등록" : pending.type === "approve" ? "문서 승인" : "문서 반려"}</small></div>
              {pending.draft && (
                <div className="draft-grid">
                  <div><span>부서</span><strong>{pending.draft.dept}</strong></div>
                  <div><span>계정</span><strong>{pending.draft.account}</strong></div>
                  <div><span>금액</span><strong>{pending.draft.amount.toLocaleString("ko-KR")}원</strong></div>
                  <div><span>일자</span><strong>{pending.draft.date}</strong></div>
                </div>
              )}
              {pending.items && <div className="pending-items">{pending.items.map((item) => <span key={item.id}>#{item.id} {item.title} · {item.amount.toLocaleString("ko-KR")}원</span>)}</div>}
              {pending.type === "create" && pending.draft?.requiresMinutes && (
                <label className={`evidence-upload ${pending.draft.evidencePath ? "attached" : ""}`}>
                  <Paperclip size={16} />
                  <span>{pending.draft.evidencePath ? "회의록 PDF 첨부 완료" : "10만원 이상 회의비 회의록 PDF 첨부"}</span>
                  <input type="file" accept="application/pdf" onChange={(event) => event.target.files?.[0] && attachExpenseEvidence(event.target.files[0])} />
                </label>
              )}
              <div className="confirmation-actions">
                <button className="secondary-button" onClick={() => confirmExpense(false)}><X size={16} /> 취소</button>
                <button className="primary-button" onClick={() => confirmExpense(true)} disabled={Boolean(evidenceRequired)}><Check size={16} /> 확인하고 실행</button>
              </div>
            </section>
          )}

          <section className="budget-section">
            <div className="section-heading"><h2>예산 현황</h2><span>{expense.overview.budgets.length}</span></div>
            <div className="budget-grid">
              {expense.overview.budgets.map((budget) => {
                const percent = Math.min((budget.used / budget.allocated) * 100, 100);
                return <article className="budget-card" key={budget.code}><div><span>{budget.code}</span><strong>{budget.name}</strong></div><p>{budget.remaining.toLocaleString("ko-KR")}원 <small>남음</small></p><div className="budget-track"><span style={{ width: `${percent}%` }} /></div></article>;
              })}
            </div>
          </section>

          <section className="expense-list-section">
            <div className="section-heading"><h2>최근 품의</h2><span>미승인 {expense.overview.pendingCount}</span></div>
            <div className="simple-table-wrap"><table><thead><tr><th>번호</th><th>부서</th><th>적요</th><th>금액</th><th>상태</th></tr></thead><tbody>{expense.overview.items.slice(0, 12).map((item) => <tr key={item.id}><td>#{item.id}</td><td>{item.dept}</td><td>{item.title}</td><td>{item.amount.toLocaleString("ko-KR")}원</td><td><span className={`status-badge ${item.status === "승인" ? "approved" : "pending"}`}>{item.status}</span></td></tr>)}</tbody></table></div>
          </section>
        </div>
      ) : <EmptyWorkspace icon={<ReceiptText size={24} />} title="지출업무 현황을 불러오는 중입니다" description="잠시 후 품의와 예산 내역이 표시됩니다." />}
    </div>
  );
}
