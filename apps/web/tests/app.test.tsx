import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";
import { App } from "../src/App";

describe("iMAX unified shell", () => {
  it("shows the home question entry and only the three implemented workflows", () => {
    render(<MemoryRouter initialEntries={["/"]}><App /></MemoryRouter>);

    expect(screen.getByRole("heading", { name: "무엇을 도와드릴까요?" })).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "질문 입력" })).toBeInTheDocument();
    expect(screen.getAllByText("업무지식").length).toBeGreaterThan(0);
    expect(screen.getAllByText("데이터 분석").length).toBeGreaterThan(0);
    expect(screen.getAllByText("지출품의").length).toBeGreaterThan(0);
    expect(screen.queryByText("CRM")).not.toBeInTheDocument();
  });

  it("renders the accessible data workspace route", async () => {
    render(<MemoryRouter initialEntries={["/data"]}><App /></MemoryRouter>);

    expect(await screen.findByRole("heading", { name: "데이터 분석" })).toBeInTheDocument();
    expect(
      await screen.findByPlaceholderText("예: 지난 3개월간 지점별 신규 계좌 수 추이는?"),
    ).toBeInTheDocument();
  });
});
