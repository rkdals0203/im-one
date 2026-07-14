import type { DataPayload } from "./types";

export type ChartKind = "line" | "bar" | "donut" | "table";

export interface ChartRecommendation {
  kind: ChartKind;
  category?: string;
  value?: string;
  reason: string;
}

export function recommendChart(data: Pick<DataPayload, "columns" | "rows" | "question">): ChartRecommendation {
  if (!data.rows.length || data.columns.length < 2) {
    return { kind: "table", reason: "표시할 차트 차원이 충분하지 않습니다." };
  }
  const numeric = data.columns.filter((column) => data.rows.some((row) => isNumeric(row[column])));
  const temporal = data.columns.find(
    (column) => /date|month|year|일자|날짜|월|기간/i.test(column) || data.rows.some((row) => isDateValue(row[column])),
  );
  const categorical = data.columns.find((column) => column !== temporal && !numeric.includes(column));
  const value = numeric.find((column) => column !== temporal);
  if (temporal && value) {
    return { kind: "line", category: temporal, value, reason: "기간별 수치 흐름을 선 차트로 표시합니다." };
  }
  if (categorical && value) {
    const categoryCount = new Set(data.rows.map((row) => String(row[categorical]))).size;
    if (categoryCount <= 6 && /비중|구성|점유|분포|ratio|share|percent/i.test(`${data.question} ${value}`)) {
      return { kind: "donut", category: categorical, value, reason: "구성비를 도넛 차트로 표시합니다." };
    }
    return { kind: "bar", category: categorical, value, reason: "항목별 수치를 막대 차트로 비교합니다." };
  }
  return { kind: "table", reason: "현재 결과는 표에서 가장 정확하게 확인할 수 있습니다." };
}

export function chartOption(data: DataPayload, recommendation: ChartRecommendation) {
  const { category, value } = recommendation;
  if (!category || !value) return {};
  const source = data.rows.map((row) => ({ [category]: row[category], [value]: numberValue(row[value]) }));
  const common = {
    animationDuration: 420,
    dataset: { source },
    tooltip: { trigger: "axis" },
    textStyle: { fontFamily: "Inter, Pretendard, system-ui, sans-serif" },
  };
  if (recommendation.kind === "donut") {
    return {
      ...common,
      tooltip: { trigger: "item", valueFormatter: (item: unknown) => formatValue(item) },
      series: [
        {
          type: "pie",
          radius: ["52%", "74%"],
          itemStyle: { borderWidth: 2 },
          encode: { itemName: category, value },
          label: { formatter: "{b}\n{d}%" },
        },
      ],
    };
  }
  return {
    ...common,
    grid: { top: 18, right: 20, bottom: 46, left: 64, containLabel: false },
    xAxis: { type: "category", axisLabel: { hideOverlap: true, color: "#71807d" } },
    yAxis: { type: "value", axisLabel: { formatter: (item: number) => compactNumber(item), color: "#71807d" } },
    series: [
      {
        type: recommendation.kind,
        encode: { x: category, y: value },
        smooth: recommendation.kind === "line",
        symbolSize: 7,
        lineStyle: { width: 3 },
        itemStyle: { color: "#00c4a8", borderRadius: recommendation.kind === "bar" ? [4, 4, 0, 0] : 0 },
        areaStyle: recommendation.kind === "line" ? { color: "rgba(0,196,168,.08)" } : undefined,
      },
    ],
  };
}

function isNumeric(value: unknown): boolean {
  if (typeof value === "number") return Number.isFinite(value);
  if (typeof value !== "string" || value.trim() === "") return false;
  return Number.isFinite(Number(value.replaceAll(",", "")));
}

function numberValue(value: unknown): number {
  return typeof value === "number" ? value : Number(String(value ?? 0).replaceAll(",", ""));
}

function isDateValue(value: unknown): boolean {
  return typeof value === "string" && /^\d{4}[-/.](?:\d{1,2})(?:[-/.]\d{1,2})?$/.test(value.trim());
}

function compactNumber(value: number): string {
  return new Intl.NumberFormat("ko-KR", { notation: "compact", maximumFractionDigits: 1 }).format(value);
}

function formatValue(value: unknown): string {
  return typeof value === "number" ? new Intl.NumberFormat("ko-KR").format(value) : String(value ?? "");
}
