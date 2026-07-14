import { expect, test, type Page, type Route } from "@playwright/test";

const dataRows = Array.from({ length: 180 }, (_, index) => ({
  opened_month: `2026-${String((index % 3) + 4).padStart(2, "0")}`,
  branch_name: ["광주상무지점", "대구중앙지점", "부산해운대지점", "서울중앙WM센터"][index % 4],
  new_account_count: 18 + (index % 17),
}));

const dataPayload = {
  kind: "data",
  question: "지난 3개월간 지점별 신규 계좌 수 추이는?",
  answer: "최근 3개월 신규 계좌는 서울중앙WM센터에서 가장 많이 늘었습니다.",
  explanation: "계좌 개설일을 월 단위로 묶고 현재 지점 권한 범위에서 집계했습니다.",
  columns: ["opened_month", "branch_name", "new_account_count"],
  columnMetadata: [],
  rows: dataRows,
  rowCount: dataRows.length,
  sql: "SELECT opened_month, branch_name, COUNT(*) AS new_account_count FROM accounts GROUP BY opened_month, branch_name LIMIT 200",
  generatedSql: "SELECT opened_month, branch_name, COUNT(*) AS new_account_count FROM accounts GROUP BY opened_month, branch_name LIMIT 200",
  executionMs: 38,
  validation: { allowed: true, issues: [] },
  metrics: [{ name: "new_accounts", definition: "기간 내 신규 계좌 수" }],
  tables: [{ name: "accounts" }, { name: "branches" }],
  executionTrace: [
    { node: "Schema Retrieval", status: "completed", detail: "accounts, branches" },
    { node: "SQL Validation", status: "completed", detail: "read-only query" },
  ],
  conversationContext: {},
};

const baseBudgets = [
  { value: "08WA 본사 업무추진비", code: "08WA", name: "본사 업무추진비", allocated: 2_000_000, used: 450_000, remaining: 1_550_000 },
  { value: "40AA 본사 회의비", code: "40AA", name: "본사 회의비", allocated: 1_200_000, used: 232_000, remaining: 968_000 },
  { value: "45BC 본사 조직활성화비", code: "45BC", name: "본사 조직활성화비", allocated: 800_000, used: 180_000, remaining: 620_000 },
];

function streamResult(workspace: string, payload: object, answer: string) {
  const route = JSON.stringify({ workspace, confidence: 0.94, reason: "업무 용어 기준" });
  const result = JSON.stringify({ sessionId: "e2e-session", userMessageId: "u1", messageId: "m1", workspace, answer, payload });
  return `event: stage\ndata: {"node":"intake","label":"질문을 확인하고 있습니다","status":"completed"}\n\nevent: route\ndata: ${route}\n\nevent: result\ndata: ${result}\n\n`;
}

async function fulfillSse(route: Route, workspace: string, payload: object, answer: string) {
  await route.fulfill({
    status: 200,
    contentType: "text/event-stream; charset=utf-8",
    body: streamResult(workspace, payload, answer),
  });
}

async function assertNoDocumentOverflow(page: Page) {
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)).toBe(true);
}

test("home routes a natural-language question to a chart and virtualized table", async ({ page }, testInfo) => {
  await page.route("**/api/v1/assistant/messages", (route) => fulfillSse(route, "data", dataPayload, dataPayload.answer));
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "무엇을 도와드릴까요?" })).toBeVisible();
  await assertNoDocumentOverflow(page);

  await page.getByRole("textbox", { name: "질문 입력" }).fill(dataPayload.question);
  await page.getByRole("button", { name: "질문 실행" }).click();
  await expect(page).toHaveURL(/\/data$/);
  await expect(page.getByText("최근 3개월 신규 계좌는 서울중앙WM센터에서 가장 많이 늘었습니다.")).toBeVisible();
  await expect(page.locator("canvas")).toBeVisible();
  await page.waitForTimeout(450);
  expect(await page.locator("canvas").evaluate((element) => {
    const canvas = element as HTMLCanvasElement;
    const context = canvas.getContext("2d");
    if (!context || canvas.width === 0 || canvas.height === 0) return false;
    const pixels = context.getImageData(0, 0, canvas.width, canvas.height).data;
    for (let index = 3; index < pixels.length; index += 997) if (pixels[index] > 0) return true;
    return false;
  })).toBe(true);

  await page.getByRole("tab", { name: "표" }).click();
  const tableViewport = page.locator(".result-table-scroll");
  await expect(tableViewport).toBeVisible();
  await tableViewport.evaluate((element) => { element.scrollTop = element.scrollHeight; });
  await expect(page.locator(".result-table-row").last()).toBeVisible();
  await assertNoDocumentOverflow(page);
  await page.screenshot({ path: testInfo.outputPath("data-workspace.png"), fullPage: true });
});

test("knowledge answers keep citations and dark mode remains legible", async ({ page }, testInfo) => {
  const payload = {
    kind: "knowledge",
    question: "회의실 예약 절차를 알려줘",
    answer: "회의실과 날짜를 선택해 예약 정보를 입력한 뒤, 안내 팝업까지 확인해야 예약이 완료됩니다. [1]",
    citations: [{ source: "meeting_room_manual.md", section: "1.1. 회의실 예약 절차", excerpt: "날짜를 더블클릭하고 시간과 회의내용을 입력한 뒤 안내 팝업을 확인합니다.", score: 12.4 }],
    generationEngine: "llm",
    llmModel: "demo-model",
  };
  await page.route("**/api/v1/assistant/messages", (route) => fulfillSse(route, "knowledge", payload, payload.answer));
  await page.goto("/knowledge");
  await page.getByRole("textbox", { name: "질문 입력" }).fill(payload.question);
  await page.getByRole("button", { name: "질문 실행" }).click();
  await expect(page.getByText("확인한 근거")).toBeVisible();
  await page.getByRole("button", { name: "다크 모드" }).click();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await assertNoDocumentOverflow(page);
  await page.screenshot({ path: testInfo.outputPath("knowledge-dark.png"), fullPage: true });
});

test("expense draft changes state only after explicit confirmation", async ({ page }, testInfo) => {
  const items = [{ id: 1003, dept: "IT정보팀", date: "2026-07-14", title: "회의비", amount: 133_000, account: "40AA 본사 회의비", status: "미승인" }];
  const initialOverview = { items, budgets: baseBudgets, pendingCount: 1, approvedCount: 0, pendingAction: null };
  const pendingOverview = {
    ...initialOverview,
    pendingAction: {
      token: "confirm-token",
      type: "create",
      draft: { id: 0, dept: "IT정보팀", date: "2026-07-14", title: "스타벅스 법인카드 사용", amount: 88_000, account: "08WA 본사 업무추진비", status: "미승인" },
    },
  };
  await page.route("**/api/v1/expenses/overview**", (route) => route.fulfill({ json: { sessionId: "e2e-session", kind: "expense", overview: initialOverview } }));
  await page.route("**/api/v1/assistant/messages", (route) => fulfillSse(
    route,
    "expense",
    { kind: "expense", message: "88,000원 품의 초안을 만들었습니다.", overview: pendingOverview },
    "88,000원 품의 초안을 만들었습니다.",
  ));
  await page.route("**/api/v1/expenses/actions", (route) => route.fulfill({
    json: {
      kind: "expense",
      message: "지출품의 1건을 등록했습니다.",
      overview: { ...initialOverview, items: [{ ...items[0], id: 1004, amount: 88_000, title: "스타벅스 법인카드 사용" }, ...items] },
      created: [{ ...items[0], id: 1004, amount: 88_000, title: "스타벅스 법인카드 사용" }],
    },
  }));

  await page.goto("/expenses");
  await expect(page.getByText("예산 현황")).toBeVisible();
  await page.getByRole("textbox", { name: "질문 입력" }).fill("스타벅스 88,000원 법인카드 품의해줘");
  await page.getByRole("button", { name: "질문 실행" }).click();
  await expect(page.getByText("확인이 필요합니다")).toBeVisible();
  await page.getByRole("button", { name: "확인하고 실행" }).click();
  await expect(page.getByText("지출품의 1건을 등록했습니다.")).toBeVisible();
  await assertNoDocumentOverflow(page);
  await page.screenshot({ path: testInfo.outputPath("expense-confirmed.png"), fullPage: true });
});
