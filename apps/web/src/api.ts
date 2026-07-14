import type { AssistantInput, AssistantResult, ExpensePayload, SessionRecord, StreamEvent } from "./types";

export async function streamAssistant(
  input: AssistantInput,
  onEvent: (event: StreamEvent) => void,
  signal?: AbortSignal,
): Promise<AssistantResult> {
  const response = await fetch("/api/v1/assistant/messages", {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
    body: JSON.stringify(input),
    signal,
  });
  if (!response.ok || !response.body) {
    throw new Error(await readError(response));
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let finalResult: AssistantResult | undefined;

  while (true) {
    const { done, value } = await reader.read();
    buffer += decoder.decode(value ?? new Uint8Array(), { stream: !done });
    const frames = buffer.split("\n\n");
    buffer = frames.pop() ?? "";
    for (const frame of frames) {
      const parsed = parseSseFrame(frame);
      if (!parsed) continue;
      onEvent(parsed);
      if (parsed.type === "error") throw new Error(parsed.data.message);
      if (parsed.type === "result") finalResult = parsed.data;
    }
    if (done) break;
  }
  if (!finalResult) throw new Error("에이전트가 최종 결과를 반환하지 않았습니다.");
  return finalResult;
}

export function parseSseFrame(frame: string): StreamEvent | null {
  let eventName = "message";
  const dataLines: string[] = [];
  for (const line of frame.split("\n")) {
    if (line.startsWith("event:")) eventName = line.slice(6).trim();
    if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
  }
  if (!dataLines.length || !["stage", "route", "clarification", "result", "error"].includes(eventName)) return null;
  return { type: eventName, data: JSON.parse(dataLines.join("\n")) } as StreamEvent;
}

export async function getSession(sessionId: string): Promise<SessionRecord> {
  const response = await fetch(`/api/v1/sessions/${encodeURIComponent(sessionId)}`);
  if (!response.ok) throw new Error(await readError(response));
  return response.json();
}

export async function getExpenseOverview(
  sessionId: string | undefined,
  role: string,
  branchId: number,
): Promise<{ sessionId: string; kind: "expense"; overview: ExpensePayload["overview"] }> {
  const params = new URLSearchParams({ role, branchId: String(branchId) });
  if (sessionId) params.set("sessionId", sessionId);
  const response = await fetch(`/api/v1/expenses/overview?${params}`);
  if (!response.ok) throw new Error(await readError(response));
  return response.json();
}

export async function performExpenseAction(input: {
  sessionId: string;
  action: "confirm" | "reject";
  confirmationToken?: string;
  idempotencyKey: string;
  role: string;
  branchId: number;
}): Promise<ExpensePayload> {
  const response = await fetch("/api/v1/expenses/actions", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  });
  if (!response.ok) throw new Error(await readError(response));
  return response.json();
}

export async function uploadExpenseEvidence(input: {
  sessionId: string;
  confirmationToken: string;
  role: string;
  branchId: number;
  file: File;
}): Promise<ExpensePayload> {
  const form = new FormData();
  form.set("sessionId", input.sessionId);
  form.set("confirmationToken", input.confirmationToken);
  form.set("role", input.role);
  form.set("branchId", String(input.branchId));
  form.set("file", input.file);
  const response = await fetch("/api/v1/expenses/evidence", { method: "POST", body: form });
  if (!response.ok) throw new Error(await readError(response));
  return response.json();
}

export async function exportData(
  sessionId: string,
  exportType: "csv" | "report",
  role: string,
  branchId: number,
): Promise<void> {
  const response = await fetch("/api/v1/data/export", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ sessionId, exportType, role, branchId }),
  });
  if (!response.ok) throw new Error(await readError(response));
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = exportType === "csv" ? "imax-results.csv" : "imax-report.md";
  anchor.click();
  URL.revokeObjectURL(url);
}

async function readError(response: Response): Promise<string> {
  try {
    const payload = await response.json();
    return payload.detail ?? payload.error ?? "요청을 처리하지 못했습니다.";
  } catch {
    return "요청을 처리하지 못했습니다.";
  }
}
