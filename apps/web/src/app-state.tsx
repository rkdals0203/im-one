import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import {
  exportData,
  getExpenseOverview,
  getSession,
  performExpenseAction,
  streamAssistant,
  uploadExpenseEvidence,
} from "./api";
import type {
  AgentPayload,
  AssistantResult,
  DataPayload,
  ExpensePayload,
  KnowledgePayload,
  SessionRecord,
  StreamEvent,
  Workspace,
} from "./types";

interface AppStateValue {
  role: string;
  setRole: (role: string) => void;
  branchId: number;
  setBranchId: (branchId: number) => void;
  theme: "light" | "dark";
  toggleTheme: () => void;
  sessionId?: string;
  knowledge?: KnowledgePayload;
  data?: DataPayload;
  expense?: ExpensePayload;
  clarification?: Extract<AgentPayload, { kind: "clarification" }>;
  progress: string[];
  loading: boolean;
  error?: string;
  ask: (message: string, hint?: Exclude<Workspace, "clarification">) => Promise<AssistantResult>;
  confirmExpense: (confirm: boolean) => Promise<void>;
  attachExpenseEvidence: (file: File) => Promise<void>;
  refreshExpense: () => Promise<void>;
  downloadData: (type: "csv" | "report") => Promise<void>;
  history?: SessionRecord;
  refreshHistory: () => Promise<void>;
  newConversation: () => void;
}

const AppStateContext = createContext<AppStateValue | null>(null);

export function AppStateProvider({ children }: { children: ReactNode }) {
  const [role, setRole] = useState("branch_manager");
  const [branchId, setBranchId] = useState(1);
  const [theme, setTheme] = useState<"light" | "dark">(
    () => (localStorage.getItem("imax-theme") as "light" | "dark") || "light",
  );
  const [sessionId, setSessionId] = useState<string | undefined>(() => localStorage.getItem("imax-session") || undefined);
  const [knowledge, setKnowledge] = useState<KnowledgePayload>();
  const [data, setData] = useState<DataPayload>();
  const [expense, setExpense] = useState<ExpensePayload>();
  const [clarification, setClarification] = useState<Extract<AgentPayload, { kind: "clarification" }>>();
  const [progress, setProgress] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string>();
  const [history, setHistory] = useState<SessionRecord>();
  const controller = useRef<AbortController | undefined>(undefined);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    localStorage.setItem("imax-theme", theme);
  }, [theme]);

  const applyPayload = useCallback((payload: AgentPayload) => {
    if (payload.kind === "knowledge") setKnowledge(payload);
    if (payload.kind === "data") setData(payload);
    if (payload.kind === "expense") setExpense(payload);
    if (payload.kind === "clarification") setClarification(payload);
  }, []);

  const ask = useCallback(
    async (message: string, hint?: Exclude<Workspace, "clarification">) => {
      controller.current?.abort();
      controller.current = new AbortController();
      setLoading(true);
      setError(undefined);
      setProgress([]);
      setClarification(undefined);
      try {
        const result = await streamAssistant(
          { sessionId, message, role, branchId, workspaceHint: hint },
          (event: StreamEvent) => {
            if (event.type === "stage") setProgress((items) => [...items, event.data.label]);
            if (event.type === "clarification") applyPayload(event.data.payload);
            if (event.type === "result") applyPayload(event.data.payload);
          },
          controller.current.signal,
        );
        setSessionId(result.sessionId);
        localStorage.setItem("imax-session", result.sessionId);
        applyPayload(result.payload);
        return result;
      } catch (caught) {
        const messageText = caught instanceof Error ? caught.message : "요청을 처리하지 못했습니다.";
        setError(messageText);
        throw caught;
      } finally {
        setLoading(false);
      }
    },
    [applyPayload, branchId, role, sessionId],
  );

  const refreshExpense = useCallback(async () => {
    const result = await getExpenseOverview(sessionId, role, branchId);
    setSessionId(result.sessionId);
    localStorage.setItem("imax-session", result.sessionId);
    setExpense({ kind: "expense", message: "지출업무 현황입니다.", overview: result.overview });
  }, [branchId, role, sessionId]);

  const confirmExpense = useCallback(
    async (confirm: boolean) => {
      if (!sessionId || !expense?.overview.pendingAction) return;
      setLoading(true);
      setError(undefined);
      try {
        const result = await performExpenseAction({
          sessionId,
          action: confirm ? "confirm" : "reject",
          confirmationToken: expense.overview.pendingAction.token,
          idempotencyKey: crypto.randomUUID(),
          role,
          branchId,
        });
        setExpense(result);
      } catch (caught) {
        setError(caught instanceof Error ? caught.message : "지출업무를 처리하지 못했습니다.");
      } finally {
        setLoading(false);
      }
    },
    [branchId, expense, role, sessionId],
  );

  const attachExpenseEvidence = useCallback(
    async (file: File) => {
      if (!sessionId || !expense?.overview.pendingAction) return;
      setLoading(true);
      try {
        const result = await uploadExpenseEvidence({
          sessionId,
          confirmationToken: expense.overview.pendingAction.token,
          role,
          branchId,
          file,
        });
        setExpense(result);
      } catch (caught) {
        setError(caught instanceof Error ? caught.message : "파일을 첨부하지 못했습니다.");
      } finally {
        setLoading(false);
      }
    },
    [branchId, expense, role, sessionId],
  );

  const downloadData = useCallback(
    async (type: "csv" | "report") => {
      if (!sessionId) return;
      await exportData(sessionId, type, role, branchId);
    },
    [branchId, role, sessionId],
  );

  const refreshHistory = useCallback(async () => {
    if (!sessionId) {
      setHistory(undefined);
      return;
    }
    setHistory(await getSession(sessionId));
  }, [sessionId]);

  const newConversation = useCallback(() => {
    controller.current?.abort();
    localStorage.removeItem("imax-session");
    setSessionId(undefined);
    setKnowledge(undefined);
    setData(undefined);
    setExpense(undefined);
    setClarification(undefined);
    setProgress([]);
    setError(undefined);
    setHistory(undefined);
  }, []);

  const value = useMemo<AppStateValue>(
    () => ({
      role,
      setRole,
      branchId,
      setBranchId,
      theme,
      toggleTheme: () => setTheme((current) => (current === "light" ? "dark" : "light")),
      sessionId,
      knowledge,
      data,
      expense,
      clarification,
      progress,
      loading,
      error,
      ask,
      confirmExpense,
      attachExpenseEvidence,
      refreshExpense,
      downloadData,
      history,
      refreshHistory,
      newConversation,
    }),
    [
      ask,
      attachExpenseEvidence,
      branchId,
      clarification,
      confirmExpense,
      data,
      downloadData,
      error,
      expense,
      history,
      knowledge,
      loading,
      newConversation,
      progress,
      refreshExpense,
      refreshHistory,
      role,
      sessionId,
      theme,
    ],
  );

  return <AppStateContext.Provider value={value}>{children}</AppStateContext.Provider>;
}

export function useAppState(): AppStateValue {
  const value = useContext(AppStateContext);
  if (!value) throw new Error("useAppState must be used within AppStateProvider");
  return value;
}
