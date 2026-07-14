import { BarChart, LineChart, PieChart } from "echarts/charts";
import {
  DatasetComponent,
  GridComponent,
  LegendComponent,
  TooltipComponent,
} from "echarts/components";
import * as echarts from "echarts/core";
import { CanvasRenderer } from "echarts/renderers";
import { useEffect, useMemo, useRef } from "react";
import { chartOption, type ChartRecommendation } from "../charting";
import type { DataPayload } from "../types";

echarts.use([
  BarChart,
  LineChart,
  PieChart,
  DatasetComponent,
  GridComponent,
  LegendComponent,
  TooltipComponent,
  CanvasRenderer,
]);

export function ChartView({ data, recommendation }: { data: DataPayload; recommendation: ChartRecommendation }) {
  const elementRef = useRef<HTMLDivElement>(null);
  const option = useMemo(() => chartOption(data, recommendation), [data, recommendation]);

  useEffect(() => {
    if (!elementRef.current) return;
    const chart = echarts.init(elementRef.current, undefined, { renderer: "canvas" });
    chart.setOption(option);
    const observer = new ResizeObserver(() => chart.resize());
    observer.observe(elementRef.current);
    return () => {
      observer.disconnect();
      chart.dispose();
    };
  }, [option]);

  return <div ref={elementRef} className="chart-canvas" role="img" aria-label={recommendation.reason} />;
}
