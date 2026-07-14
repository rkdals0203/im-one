import * as Dialog from "@radix-ui/react-dialog";
import * as Tabs from "@radix-ui/react-tabs";
import { BarChart3, ChevronRight, Download, FileText, Gauge, Table2, X } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { useAppState } from "../app-state";
import { recommendChart, type ChartKind } from "../charting";
import { AssistantComposer } from "../components/AssistantComposer";
import { ChartView } from "../components/ChartView";
import { VirtualTable } from "../components/VirtualTable";
import { EmptyWorkspace, StatusArea, WorkspaceHeading } from "./KnowledgePage";

export function DataPage() {
  const { data, loading, progress, error, downloadData } = useAppState();
  const recommendation = useMemo(() => data ? recommendChart(data) : { kind: "table" as ChartKind, reason: "" }, [data]);
  const [chartKind, setChartKind] = useState<ChartKind>(recommendation.kind);
  useEffect(() => setChartKind(recommendation.kind), [recommendation.kind]);
  const activeRecommendation = { ...recommendation, kind: chartKind };

  return (
    <div className="workspace-page data-workspace">
      <WorkspaceHeading
        eyebrow="Data agent"
        title="데이터 분석"
        description="질문을 안전한 조회로 바꾸고 결과를 이해하기 쉬운 형태로 정리합니다."
        icon={<BarChart3 size={21} />}
        actions={data && <>
          <button className="secondary-button" onClick={() => downloadData("csv")}><Download size={15} /> CSV</button>
          <button className="secondary-button" onClick={() => downloadData("report")}><FileText size={15} /> 보고서</button>
        </>}
      />
      <div className="workspace-composer"><AssistantComposer hint="data" placeholder="예: 지난 3개월간 지점별 신규 계좌 수 추이는?" /></div>
      <StatusArea loading={loading} progress={progress} error={error} />

      {data ? (
        <div className="data-result-layout">
          <section className="data-summary-band">
            <div><span className="result-status">{data.validation?.allowed ? "검증 완료" : "확인 필요"}</span><h2>{data.answer}</h2><p>{data.explanation}</p></div>
            <div className="kpi-row">
              <div><strong>{data.rowCount ?? data.rows.length}</strong><span>조회 행</span></div>
              <div><strong>{formatMs(data.executionMs)}</strong><span>실행 시간</span></div>
              <div><strong>{data.tables?.length ?? 0}</strong><span>참조 테이블</span></div>
            </div>
          </section>

          {data.rows.length > 0 ? (
            <Tabs.Root className="result-tabs" defaultValue={recommendation.kind === "table" ? "table" : "chart"}>
              <div className="result-toolbar">
                <Tabs.List className="tab-list" aria-label="결과 보기 방식">
                  <Tabs.Trigger value="chart"><BarChart3 size={15} /> 차트</Tabs.Trigger>
                  <Tabs.Trigger value="table"><Table2 size={15} /> 표</Tabs.Trigger>
                </Tabs.List>
                <div className="chart-kind-control" aria-label="차트 유형">
                  {(["line", "bar", "donut"] as ChartKind[]).map((kind) => (
                    <button key={kind} className={chartKind === kind ? "active" : ""} onClick={() => setChartKind(kind)} disabled={!recommendation.category || !recommendation.value}>
                      {kind === "line" ? "선" : kind === "bar" ? "막대" : "도넛"}
                    </button>
                  ))}
                </div>
              </div>
              <Tabs.Content value="chart" className="result-content">
                {chartKind === "table" || !recommendation.category ? (
                  <div className="chart-empty"><Gauge size={22} /><p>{recommendation.reason}</p></div>
                ) : <ChartView data={data} recommendation={activeRecommendation} />}
              </Tabs.Content>
              <Tabs.Content value="table" className="result-content"><VirtualTable columns={data.columns} rows={data.rows} /></Tabs.Content>
            </Tabs.Root>
          ) : <div className="no-result-panel">{data.validation?.issues?.join(" ") || "조회된 데이터가 없습니다."}</div>}

          <Dialog.Root>
            <Dialog.Trigger asChild><button className="evidence-disclosure">분석 근거 확인 <ChevronRight size={16} /></button></Dialog.Trigger>
            <Dialog.Portal>
              <Dialog.Overlay className="dialog-overlay" />
              <Dialog.Content className="evidence-drawer">
                <div className="dialog-head"><div><Dialog.Title>분석 근거</Dialog.Title><Dialog.Description>생성 SQL과 검증 과정을 확인합니다.</Dialog.Description></div><Dialog.Close asChild><button className="icon-button"><X size={18} /></button></Dialog.Close></div>
                <section><span className="drawer-label">Generated SQL</span><pre><code>{data.sql || data.generatedSql || "SQL이 생성되지 않았습니다."}</code></pre></section>
                <section><span className="drawer-label">Execution trace</span><div className="trace-list">{data.executionTrace?.map((step) => <div key={step.node}><span className={step.status === "blocked" ? "trace-dot warn" : "trace-dot"} /><div><strong>{step.node}</strong><p>{step.detail}</p></div></div>)}</div></section>
              </Dialog.Content>
            </Dialog.Portal>
          </Dialog.Root>
        </div>
      ) : <EmptyWorkspace icon={<BarChart3 size={24} />} title="분석할 데이터를 질문하세요" description="SQL을 몰라도 질문, 검증, 조회, 시각화가 한 번에 진행됩니다." />}
    </div>
  );
}

function formatMs(value?: number): string {
  if (value === undefined || value === null) return "-";
  return value < 1000 ? `${Math.round(value)}ms` : `${(value / 1000).toFixed(1)}s`;
}
