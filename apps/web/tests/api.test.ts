import { describe, expect, it, vi } from "vitest";
import { parseSseFrame, streamAssistant } from "../src/api";

describe("SSE client", () => {
  it("parses named Korean data events", () => {
    expect(parseSseFrame('event: stage\ndata: {"node":"classify","label":"담당 업무를 선택했습니다","status":"completed"}')).toEqual({
      type: "stage",
      data: { node: "classify", label: "담당 업무를 선택했습니다", status: "completed" },
    });
  });

  it("collects chunked events and returns the final result", async () => {
    const encoded = new TextEncoder();
    const body = new ReadableStream({
      start(controller) {
        controller.enqueue(encoded.encode('event: stage\ndata: {"node":"intake","label":"질문 확인","status":"completed"}\n\n'));
        controller.enqueue(encoded.encode('event: result\ndata: {"sessionId":"s1","messageId":"m1","workspace":"clarification","answer":"선택","payload":{"kind":"clarification","message":"선택","options":[]}}\n\n'));
        controller.close();
      },
    });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(body, { status: 200 })));
    const events: string[] = [];

    const result = await streamAssistant(
      { message: "도와줘", role: "branch_manager", branchId: 1 },
      (event) => events.push(event.type),
    );

    expect(events).toEqual(["stage", "result"]);
    expect(result.payload.kind).toBe("clarification");
    vi.unstubAllGlobals();
  });
});
