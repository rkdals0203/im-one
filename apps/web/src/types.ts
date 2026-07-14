import type { components } from "./generated/api-schema";

export type Workspace = "knowledge" | "data" | "expense" | "clarification";
export type AssistantInput = components["schemas"]["AssistantRequest"];

export interface Citation {
  source: string;
  section: string;
  excerpt: string;
  score: number;
}

export interface KnowledgePayload {
  kind: "knowledge";
  question: string;
  answer: string;
  citations: Citation[];
  generationEngine: string;
  llmModel?: string | null;
}

export interface ColumnMetadata {
  name?: string;
  type?: string;
  semanticType?: string;
}

export interface TraceStep {
  node: string;
  status: string;
  detail: string;
}

export interface DataPayload {
  kind: "data";
  question: string;
  answer: string;
  explanation: string;
  columns: string[];
  columnMetadata: ColumnMetadata[];
  rows: Record<string, unknown>[];
  rowCount: number;
  sql: string;
  generatedSql?: string;
  executionMs?: number;
  validation?: { allowed?: boolean; issues?: string[] };
  metrics?: Array<{ name?: string; definition?: string }>;
  tables?: Array<{ name?: string }>;
  executionTrace?: TraceStep[];
  conversationContext?: Record<string, unknown>;
  sessionId?: string;
}

export interface ExpenseItem {
  id: number;
  dept: string;
  date: string;
  title: string;
  amount: number;
  account: string;
  status: string;
}

export interface BudgetItem {
  value: string;
  code: string;
  name: string;
  allocated: number;
  used: number;
  remaining: number;
}

export interface PendingExpenseAction {
  token: string;
  type: "create" | "approve" | "cancel";
  itemIds?: number[];
  items?: ExpenseItem[];
  draft?: ExpenseItem & {
    requiresMinutes?: boolean;
    evidencePath?: string | null;
    store?: string;
  };
}

export interface ExpenseOverview {
  items: ExpenseItem[];
  budgets: BudgetItem[];
  pendingCount: number;
  approvedCount: number;
  pendingAction?: PendingExpenseAction | null;
}

export interface ExpensePayload {
  kind: "expense";
  message: string;
  overview: ExpenseOverview;
  created?: ExpenseItem[];
}

export interface ClarificationPayload {
  kind: "clarification";
  message: string;
  options: Array<{ workspace: Exclude<Workspace, "clarification">; label: string }>;
}

export type AgentPayload = KnowledgePayload | DataPayload | ExpensePayload | ClarificationPayload;

type GeneratedAssistantResult = components["schemas"]["AssistantResult"];
export type AssistantResult = Omit<GeneratedAssistantResult, "payload"> & { payload: AgentPayload };

export type StreamEvent =
  | { type: "stage"; data: { node: string; label: string; status: string; sessionId?: string } }
  | { type: "route"; data: { workspace: Workspace; confidence: number; reason: string } }
  | { type: "clarification"; data: { sessionId: string; message: string; payload: ClarificationPayload } }
  | { type: "result"; data: AssistantResult }
  | { type: "error"; data: { code: string; message: string; retryable: boolean; sessionId?: string } };

export interface SessionRecord {
  id: string;
  role: string;
  branchId: number;
  workspace?: Workspace;
  messages: Array<{
    id: string;
    role: "user" | "assistant";
    content: string;
    workspace?: Workspace;
    payload?: AgentPayload;
    createdAt: string;
  }>;
}
