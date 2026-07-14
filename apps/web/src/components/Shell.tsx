import * as Dialog from "@radix-ui/react-dialog";
import * as Tooltip from "@radix-ui/react-tooltip";
import {
  BookOpenText,
  ChartNoAxesCombined,
  History,
  Home,
  Moon,
  Plus,
  ReceiptText,
  Sun,
  X,
} from "lucide-react";
import { NavLink, Outlet, useNavigate } from "react-router";
import { useAppState } from "../app-state";

const navItems = [
  { to: "/", label: "홈", icon: Home, end: true },
  { to: "/knowledge", label: "업무지식", icon: BookOpenText },
  { to: "/data", label: "데이터", icon: ChartNoAxesCombined },
  { to: "/expenses", label: "지출품의", icon: ReceiptText },
];

export function Shell() {
  const {
    role,
    setRole,
    branchId,
    setBranchId,
    theme,
    toggleTheme,
    sessionId,
    history,
    refreshHistory,
    newConversation,
  } = useAppState();
  const navigate = useNavigate();

  function startNew() {
    newConversation();
    navigate("/");
  }

  return (
    <Tooltip.Provider delayDuration={250}>
      <div className="app-shell">
        <header className="global-header">
          <button className="brand-button" onClick={() => navigate("/")} aria-label="iMAX 홈">
            <img src="/title.png" alt="iMAX" />
          </button>
          <nav className="desktop-nav" aria-label="주요 업무">
            {navItems.map(({ to, label, icon: Icon, end }) => (
              <NavLink key={to} to={to} end={end} className={({ isActive }) => isActive ? "nav-link active" : "nav-link"}>
                <Icon size={17} />
                <span>{label}</span>
              </NavLink>
            ))}
          </nav>
          <div className="header-actions">
            <label className="compact-select">
              <span className="sr-only">사용자 역할</span>
              <select value={role} onChange={(event) => setRole(event.target.value)}>
                <option value="branch_manager">지점 관리자</option>
                <option value="sales_planning">영업기획</option>
                <option value="compliance">준법감시</option>
              </select>
            </label>
            {role === "branch_manager" && (
              <label className="branch-picker">
                <span>지점</span>
                <input type="number" min={1} max={10} value={branchId} onChange={(event) => setBranchId(Number(event.target.value))} />
              </label>
            )}
            <Dialog.Root onOpenChange={(open) => open && refreshHistory()}>
              <Tooltip.Root>
                <Tooltip.Trigger asChild>
                  <Dialog.Trigger asChild>
                    <button className="icon-button" aria-label="대화 이력" disabled={!sessionId}><History size={18} /></button>
                  </Dialog.Trigger>
                </Tooltip.Trigger>
                <Tooltip.Portal><Tooltip.Content className="tooltip">대화 이력</Tooltip.Content></Tooltip.Portal>
              </Tooltip.Root>
              <Dialog.Portal>
                <Dialog.Overlay className="dialog-overlay" />
                <Dialog.Content className="history-dialog">
                  <div className="dialog-head">
                    <div><Dialog.Title>현재 대화</Dialog.Title><Dialog.Description>{sessionId?.slice(0, 12)}</Dialog.Description></div>
                    <Dialog.Close asChild><button className="icon-button" aria-label="닫기"><X size={18} /></button></Dialog.Close>
                  </div>
                  <div className="history-list">
                    {history?.messages.map((message) => (
                      <div key={message.id} className={`history-message ${message.role}`}>
                        <span>{message.role === "user" ? "나" : "iMAX"}</span>
                        <p>{message.content}</p>
                      </div>
                    )) ?? <div className="empty-inline">대화가 아직 없습니다.</div>}
                  </div>
                </Dialog.Content>
              </Dialog.Portal>
            </Dialog.Root>
            <Tooltip.Root>
              <Tooltip.Trigger asChild><button className="icon-button" onClick={startNew} aria-label="새 대화"><Plus size={18} /></button></Tooltip.Trigger>
              <Tooltip.Portal><Tooltip.Content className="tooltip">새 대화</Tooltip.Content></Tooltip.Portal>
            </Tooltip.Root>
            <button className="theme-button" onClick={toggleTheme} aria-label={`${theme === "light" ? "다크" : "라이트"} 모드`}>
              {theme === "light" ? <Moon size={17} /> : <Sun size={17} />}
            </button>
          </div>
        </header>
        <main className="app-main"><Outlet /></main>
        <nav className="mobile-nav" aria-label="모바일 주요 업무">
          {navItems.map(({ to, label, icon: Icon, end }) => (
            <NavLink key={to} to={to} end={end} className={({ isActive }) => isActive ? "mobile-link active" : "mobile-link"}>
              <Icon size={19} /><span>{label}</span>
            </NavLink>
          ))}
        </nav>
      </div>
    </Tooltip.Provider>
  );
}
