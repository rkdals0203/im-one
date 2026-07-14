import { lazy, Suspense } from "react";
import { Navigate, Route, Routes } from "react-router";
import { AppStateProvider } from "./app-state";
import { Shell } from "./components/Shell";
import { HomePage } from "./pages/HomePage";

const KnowledgePage = lazy(() => import("./pages/KnowledgePage").then((module) => ({ default: module.KnowledgePage })));
const DataPage = lazy(() => import("./pages/DataPage").then((module) => ({ default: module.DataPage })));
const ExpensePage = lazy(() => import("./pages/ExpensePage").then((module) => ({ default: module.ExpensePage })));

export function App() {
  return (
    <AppStateProvider>
      <Suspense fallback={<div className="route-loading" role="status">화면을 준비하고 있습니다</div>}>
        <Routes>
          <Route element={<Shell />}>
            <Route index element={<HomePage />} />
            <Route path="knowledge" element={<KnowledgePage />} />
            <Route path="data" element={<DataPage />} />
            <Route path="expenses" element={<ExpensePage />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Route>
        </Routes>
      </Suspense>
    </AppStateProvider>
  );
}
