import { describe, expect, it } from "vitest";
import { recommendChart } from "../src/charting";

describe("recommendChart", () => {
  it("recommends a line chart for time series", () => {
    expect(recommendChart({
      question: "월별 신규 계좌 추이",
      columns: ["opened_month", "new_account_count"],
      rows: [
        { opened_month: "2026-05", new_account_count: 12 },
        { opened_month: "2026-06", new_account_count: 18 },
      ],
    }).kind).toBe("line");
  });

  it("recommends a bar chart for categorical comparisons", () => {
    expect(recommendChart({
      question: "지점별 ELS 금액 비교",
      columns: ["branch_name", "els_amount"],
      rows: [
        { branch_name: "광주상무지점", els_amount: 120_000_000 },
        { branch_name: "대구중앙지점", els_amount: 95_000_000 },
      ],
    }).kind).toBe("bar");
  });

  it("uses a donut only for small composition data", () => {
    expect(recommendChart({
      question: "VOC 유형별 구성비",
      columns: ["case_type", "share"],
      rows: [
        { case_type: "상품", share: 55 },
        { case_type: "서비스", share: 45 },
      ],
    }).kind).toBe("donut");
  });

  it("falls back to a table when no numeric measure exists", () => {
    expect(recommendChart({
      question: "지점 목록",
      columns: ["branch_name", "region"],
      rows: [{ branch_name: "광주상무지점", region: "광주" }],
    }).kind).toBe("table");
  });
});
