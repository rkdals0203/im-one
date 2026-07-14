const form = document.querySelector("#queryForm");
const input = document.querySelector("#questionInput");
const sampleList = document.querySelector("#sampleList");
const resetButton = document.querySelector("#resetButton");
const copySqlButton = document.querySelector("#copySqlButton");
const exportCsvButton = document.querySelector("#exportCsvButton");
const reportButton = document.querySelector("#reportButton");
const tableWrap = document.querySelector("#tableWrap");
const sqlBlock = document.querySelector("#sqlBlock code");
const validationMetric = document.querySelector("#validationMetric");
const rowMetric = document.querySelector("#rowMetric");
const tableMetric = document.querySelector("#tableMetric");
const latencyBadge = document.querySelector("#latencyBadge");
const traceList = document.querySelector("#traceList");
const runtimeLabel = document.querySelector("#runtimeLabel");
const generationLabel = document.querySelector("#generationLabel");
const runButton = document.querySelector(".run-action");
const themeOptions = document.querySelectorAll("[data-theme-option]");
const chatQuestion = document.querySelector("#chatQuestion");
const chatSummary = document.querySelector("#chatSummary");
const clarificationList = document.querySelector("#clarificationList");
const chatForm = document.querySelector("#chatForm");
const chatInput = document.querySelector("#chatInput");
const miniResult = document.querySelector("#miniResult");
const feedbackForm = document.querySelector("#feedbackForm");
const feedbackCategory = document.querySelector("#feedbackCategory");
const feedbackComment = document.querySelector("#feedbackComment");
const feedbackStatus = document.querySelector("#feedbackStatus");
const feedbackButtons = document.querySelectorAll("[data-feedback-rating]");
const globalSearch = document.querySelector("#globalSearch");
const filterButton = document.querySelector("#filterButton");
const dataPane = document.querySelector(".data-pane");
const tableTopbar = document.querySelector(".table-topbar");
const queryComposer = document.querySelector(".query-composer");
const gridFooter = document.querySelector(".grid-footer");
const homeView = document.querySelector("#homeView");
const workspaceView = document.querySelector("#workspaceView");
const catalogView = document.querySelector("#catalogView");
const monitorView = document.querySelector("#monitorView");
const homeForm = document.querySelector("#homeForm");
const homeInput = document.querySelector("#homeInput");
const homeButton = document.querySelector("#homeButton");
const workspaceButton = document.querySelector("#workspaceButton");
const catalogButton = document.querySelector("#catalogButton");
const monitorButton = document.querySelector("#monitorButton");
const roleSelect = document.querySelector("#roleSelect");
const catalogRoleSelect = document.querySelector("#catalogRoleSelect");
const branchScopeInput = document.querySelector("#branchScopeInput");
const schemaList = document.querySelector("#schemaList");
const metricList = document.querySelector("#metricList");
const homeSampleList = document.querySelector("#homeSampleList");
const refreshCatalogButton = document.querySelector("#refreshCatalogButton");
const catalogStatus = document.querySelector("#catalogStatus");
const catalogManagementSummary = document.querySelector("#catalogManagementSummary");
const catalogMetricManagementList = document.querySelector("#catalogMetricManagementList");
const catalogTableManagementList = document.querySelector("#catalogTableManagementList");
const catalogRoleCoverageList = document.querySelector("#catalogRoleCoverageList");
const catalogIssueManagementList = document.querySelector("#catalogIssueManagementList");
const refreshMonitorButton = document.querySelector("#refreshMonitorButton");
const monitorStatus = document.querySelector("#monitorStatus");
const runtimeMetrics = document.querySelector("#runtimeMetrics");
const auditSummaryList = document.querySelector("#auditSummaryList");
const feedbackSummaryList = document.querySelector("#feedbackSummaryList");
const verifiedSummary = document.querySelector("#verifiedSummary");
const catalogGovernanceSummary = document.querySelector("#catalogGovernanceSummary");
const evaluationSummary = document.querySelector("#evaluationSummary");
const readinessSummary = document.querySelector("#readinessSummary");
const readinessActionList = document.querySelector("#readinessActionList");

const questionMaxLength = 1000;
const initialQuestion = input.value;
const themeStorageKey = "im-one-theme";
const sessionStorageKey = "im-one-session-id";
const apiTokenStorageKey = "im-one-api-token";
const roleStorageKey = "im-one-role";
const branchStorageKey = "im-one-branch-id";
let activeSql = "";
let hasRenderedTable = false;
let conversationContext = {};
let sessionId = localStorage.getItem(sessionStorageKey) || createSessionId();
let selectedFeedbackRating = "";

function createSessionId() {
  const nextId =
    window.crypto && window.crypto.randomUUID
      ? window.crypto.randomUUID()
      : `session-${Date.now()}-${Math.random().toString(16).slice(2)}`;
  localStorage.setItem(sessionStorageKey, nextId);
  return nextId;
}

function applyTheme(theme) {
  const nextTheme = theme === "light" ? "light" : "dark";
  document.documentElement.dataset.theme = nextTheme;
  localStorage.setItem(themeStorageKey, nextTheme);
  themeOptions.forEach((button) => {
    const isActive = button.dataset.themeOption === nextTheme;
    button.classList.toggle("is-active", isActive);
    button.setAttribute("aria-pressed", String(isActive));
  });
}

function setupTheme() {
  applyTheme(localStorage.getItem(themeStorageKey) || "dark");
}

function setupRoleControls() {
  roleSelect.value = localStorage.getItem(roleStorageKey) || "branch_manager";
  catalogRoleSelect.value = roleSelect.value;
  branchScopeInput.value = localStorage.getItem(branchStorageKey) || "1";
  syncRoleControls();
}

function syncRoleControls() {
  const isBranchManager = roleSelect.value === "branch_manager";
  catalogRoleSelect.value = roleSelect.value;
  branchScopeInput.disabled = !isBranchManager;
  branchScopeInput.parentElement.classList.toggle("is-disabled", !isBranchManager);
  localStorage.setItem(roleStorageKey, roleSelect.value);
  localStorage.setItem(branchStorageKey, branchScopeInput.value || "1");
}

function currentBranchId() {
  const parsed = Number.parseInt(branchScopeInput.value || "1", 10);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : 1;
}

function showHome() {
  homeView.hidden = false;
  workspaceView.hidden = true;
  catalogView.hidden = true;
  monitorView.hidden = true;
  homeButton.classList.add("is-active");
  workspaceButton.classList.remove("is-active");
  catalogButton.classList.remove("is-active");
  monitorButton.classList.remove("is-active");
  requestAnimationFrame(() => homeInput.focus({ preventScroll: true }));
}

function showWorkspace() {
  homeView.hidden = true;
  workspaceView.hidden = false;
  catalogView.hidden = true;
  monitorView.hidden = true;
  homeButton.classList.remove("is-active");
  workspaceButton.classList.add("is-active");
  catalogButton.classList.remove("is-active");
  monitorButton.classList.remove("is-active");
  requestAnimationFrame(syncResultGridHeight);
}

function showCatalog() {
  homeView.hidden = true;
  workspaceView.hidden = true;
  catalogView.hidden = false;
  monitorView.hidden = true;
  homeButton.classList.remove("is-active");
  workspaceButton.classList.remove("is-active");
  catalogButton.classList.add("is-active");
  monitorButton.classList.remove("is-active");
  loadCatalog();
}

function showMonitor() {
  homeView.hidden = true;
  workspaceView.hidden = true;
  catalogView.hidden = true;
  monitorView.hidden = false;
  homeButton.classList.remove("is-active");
  workspaceButton.classList.remove("is-active");
  catalogButton.classList.remove("is-active");
  monitorButton.classList.add("is-active");
  loadMonitoring();
}

function renderIcons() {
  if (window.lucide) {
    window.lucide.createIcons();
  }
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function formatCompact(value) {
  if (typeof value === "number") {
    return new Intl.NumberFormat("ko-KR").format(value);
  }
  return value ?? "";
}

function formatGenerationStatus(data) {
  const engine = data.generationEngine || "LLM";
  const model = data.llmModel || "";
  return model ? `${engine}/${model}` : engine;
}

function formatGenerationTitle(data) {
  return [data.generationEngine, data.llmModel, data.promptVersion]
    .filter(Boolean)
    .join(" / ");
}

function setLoading(isLoading) {
  runButton.disabled = isLoading;
  latencyBadge.textContent = isLoading ? "running" : latencyBadge.textContent;
}

function setFeedbackRating(rating) {
  selectedFeedbackRating = rating;
  feedbackButtons.forEach((button) => {
    const isActive = button.dataset.feedbackRating === rating;
    button.classList.toggle("is-active", isActive);
    button.setAttribute("aria-pressed", String(isActive));
  });
}

function resetFeedbackState() {
  setFeedbackRating("");
  feedbackComment.value = "";
  feedbackStatus.textContent = "";
  feedbackStatus.className = "feedback-status";
}

function apiHeaders() {
  const headers = { "Content-Type": "application/json" };
  const token = localStorage.getItem(apiTokenStorageKey);
  if (token) {
    headers.Authorization = `Bearer ${token}`;
  }
  return headers;
}

function renderSamples(questions) {
  sampleList.innerHTML = questions
    .map(
      (question) =>
        `<button class="sample-button" type="button" data-search-item="${escapeHtml(question)}" data-question="${escapeHtml(question)}">${escapeHtml(question)}</button>`,
    )
    .join("");

  homeSampleList.innerHTML = questions
    .slice(0, 4)
    .map(
      (question) =>
        `<button class="home-sample-button" type="button" data-question="${escapeHtml(question)}">${escapeHtml(question)}</button>`,
    )
    .join("");
}

function renderSchemaList(tables) {
  if (!tables.length) {
    return;
  }

  schemaList.innerHTML = tables
    .map((table, index) => {
      const columns = table.columns || [];
      const searchText = [table.name, table.description || "", ...columns].join(" ");
      const columnHtml = columns
        .slice(0, 4)
        .map((column) => `<span>${escapeHtml(column)}</span>`)
        .join("");
      return `
        <details ${index === 0 ? "open" : ""} data-search-item="${escapeHtml(searchText)}">
          <summary>${escapeHtml(table.name)}</summary>
          ${columnHtml}
        </details>
      `;
    })
    .join("");
}

function renderMetricList(metrics) {
  if (!metrics.length) {
    metricList.innerHTML = `<div class="context-empty">No metrics for this role</div>`;
    return;
  }

  metricList.innerHTML = metrics
    .map((metric, index) => {
      const filters = metric.filters || [];
      const tables = metric.tables || [];
      const searchText = [
        metric.name,
        metric.description || "",
        metric.definition || "",
        metric.default_grouping || "",
        ...filters,
        ...tables,
      ].join(" ");
      const detailItems = [
        metric.definition,
        filters.length ? `filter: ${filters.join(", ")}` : "",
        metric.default_grouping ? `group: ${metric.default_grouping}` : "",
        tables.length ? `tables: ${tables.join(", ")}` : "",
      ]
        .filter(Boolean)
        .slice(0, 4)
        .map((item) => `<span>${escapeHtml(item)}</span>`)
        .join("");

      return `
        <details ${index === 0 ? "open" : ""} data-search-item="${escapeHtml(searchText)}">
          <summary>${escapeHtml(metric.name)}</summary>
          ${detailItems}
        </details>
      `;
    })
    .join("");
}

async function loadCatalog() {
  try {
    const response = await fetch(`/api/catalog?role=${encodeURIComponent(roleSelect.value)}`, {
      headers: apiHeaders(),
    });
    if (!response.ok) {
      return;
    }
    const data = await response.json();
    renderSchemaList(data.tables || []);
    renderMetricList(data.metrics || []);
  } catch (error) {
    metricList.innerHTML = `<div class="context-empty">Catalog unavailable</div>`;
  }
}

async function fetchMonitorJson(path) {
  const response = await fetch(path, { headers: apiHeaders() });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.error || `${path} ${response.status}`);
  }
  return data;
}

function renderMonitorStats(container, items) {
  container.innerHTML = items
    .map(
      ([label, value]) =>
        `<div><span>${escapeHtml(label)}</span><strong>${escapeHtml(formatCompact(value))}</strong></div>`,
    )
    .join("");
}

function renderCounterSummary(container, counter, recentItems, recentFormatter, emptyText) {
  const counterRows = Object.entries(counter || {})
    .slice(0, 5)
    .map(
      ([label, count]) =>
        `<div class="monitor-row"><span>${escapeHtml(label)}</span><strong>${escapeHtml(formatCompact(count))}</strong></div>`,
    );
  const recentRows = (recentItems || [])
    .slice(0, 3)
    .map((item) => `<div class="monitor-recent">${escapeHtml(recentFormatter(item))}</div>`);
  const rows = [...counterRows, ...recentRows];
  container.innerHTML = rows.length ? rows.join("") : `<div class="monitor-empty">${escapeHtml(emptyText)}</div>`;
}

function renderBacklogSummary(container, backlog, fallbackCounter, fallbackRecent) {
  if (Array.isArray(backlog) && backlog.length) {
    container.innerHTML = backlog
      .slice(0, 5)
      .map((item) => {
        const scope = [...(item.metrics || []), ...(item.tables || [])].slice(0, 3).join(", ");
        const note = (item.recent_comments || [])[0] || (item.sample_questions || [])[0] || item.suggested_action || "";
        return `
          <div class="monitor-recent">
            <strong>${escapeHtml(item.category || "backlog")} · ${escapeHtml(item.priority_score || 0)}</strong>
            <span>${escapeHtml(scope || item.key || "-")}</span>
            <small>${escapeHtml(note)}</small>
          </div>
        `;
      })
      .join("");
    return;
  }

  renderCounterSummary(
    container,
    fallbackCounter,
    fallbackRecent,
    (item) => `${item.category || "uncategorized"} · ${item.comment || item.question || "-"}`,
    "No semantic backlog",
  );
}

function renderCatalogMetricManagement(container, metrics) {
  const rows = (metrics || []).slice(0, 12).map((metric) => {
    const tables = (metric.tables || []).join(", ");
    const columns = (metric.related_columns || []).slice(0, 4).join(", ");
    return `
      <div class="monitor-recent">
        <strong>${escapeHtml(metric.name || "metric")}</strong>
        <span>${escapeHtml(metric.description || metric.definition || "-")}</span>
        <small>${escapeHtml([tables, metric.default_period, metric.default_grouping, columns].filter(Boolean).join(" · "))}</small>
      </div>
    `;
  });
  container.innerHTML = rows.length ? rows.join("") : `<div class="monitor-empty">No metrics loaded</div>`;
}

function renderCatalogTableManagement(container, tables) {
  const rows = (tables || []).map(
    (table) => `
      <div class="monitor-recent">
        <strong>${escapeHtml(table.name || "table")}</strong>
        <span>${escapeHtml(table.description || "-")}</span>
        <small>${escapeHtml((table.columns || []).join(", "))}</small>
      </div>
    `,
  );
  container.innerHTML = rows.length ? rows.join("") : `<div class="monitor-empty">No tables loaded</div>`;
}

function renderCatalogRoleCoverage(container, roleCoverage) {
  const rows = Object.entries(roleCoverage || {}).map(
    ([role, coverage]) => `
      <div class="monitor-row">
        <span>${escapeHtml(role)} · ${(coverage.allowed_tables || []).map(escapeHtml).join(", ")}</span>
        <strong>${escapeHtml(coverage.visible_metric_count ?? 0)}</strong>
      </div>
    `,
  );
  container.innerHTML = rows.length ? rows.join("") : `<div class="monitor-empty">No role coverage loaded</div>`;
}

function renderCatalogIssueManagement(container, issues) {
  const rows = (issues || []).map(
    (issue) => `
      <div class="monitor-recent">
        <strong>${escapeHtml(issue.severity || "info")} · ${escapeHtml(issue.issue || "issue")}</strong>
        <span>${escapeHtml(issue.metric || "catalog")}</span>
        <small>${escapeHtml(issue.detail || "-")}</small>
      </div>
    `,
  );
  container.innerHTML = rows.length ? rows.join("") : `<div class="monitor-empty">No governance issues</div>`;
}

async function loadCatalog() {
  catalogStatus.textContent = "Loading";
  try {
    const role = roleSelect.value;
    const [catalog, governance] = await Promise.all([
      fetchMonitorJson(`/api/catalog?role=${encodeURIComponent(role)}`),
      fetchMonitorJson(`/api/catalog-governance?role=${encodeURIComponent(role)}`),
    ]);

    renderMonitorStats(catalogManagementSummary, [
      ["visible_metrics", catalog.metrics?.length ?? 0],
      ["visible_tables", catalog.tables?.length ?? 0],
      ["governance_status", governance.status || "-"],
      ["issues", governance.issueCount ?? 0],
      ["synthetic_data", catalog.syntheticData ? "yes" : "no"],
    ]);
    renderCatalogMetricManagement(catalogMetricManagementList, catalog.metrics);
    renderCatalogTableManagement(catalogTableManagementList, catalog.tables);
    renderCatalogRoleCoverage(catalogRoleCoverageList, governance.roleCoverage);
    renderCatalogIssueManagement(catalogIssueManagementList, governance.issues);
    catalogStatus.textContent = "Updated";
  } catch (error) {
    catalogStatus.textContent = "Unavailable";
    catalogManagementSummary.innerHTML = `<div class="monitor-empty">${escapeHtml(error.message)}</div>`;
    catalogMetricManagementList.innerHTML = `<div class="monitor-empty">${escapeHtml(error.message)}</div>`;
    catalogTableManagementList.innerHTML = `<div class="monitor-empty">${escapeHtml(error.message)}</div>`;
    catalogRoleCoverageList.innerHTML = `<div class="monitor-empty">${escapeHtml(error.message)}</div>`;
    catalogIssueManagementList.innerHTML = `<div class="monitor-empty">${escapeHtml(error.message)}</div>`;
  }
}

function renderReadinessActions(container, profileEntries) {
  const rows = profileEntries
    .flatMap(([profileLabel, payload]) => {
      const nextActions = (payload.next_actions || [])
        .filter((item) => item.required)
        .map((item) => ({ ...item, profileLabel }));
      const liveGateActions = (payload.readiness_gate?.failures || [])
        .filter((item) => item.name === "live_checks_not_run")
        .map((item) => ({
          name: item.name,
          detail: item.details?.join(" ") || "Live readiness checks were not run.",
          action: `Run /api/readiness?profile=${String(payload.profile || "").toLowerCase()}&live=true or the strict evidence replay command.`,
          profileLabel,
        }));
      const evaluationGateActions = (payload.prd_evaluation_gate?.failures || [])
        .filter((item) => item.name === "prd_evaluation_evidence_not_run")
        .map((item) => ({
          name: item.name,
          detail: item.details?.join(" ") || "PRD evaluation evidence was not run.",
          action: `Run python -m im_one_agent.evidence --profile ${String(payload.profile || "").toLowerCase()} --live-checks --strict.`,
          profileLabel,
        }));
      const evaluationCoverageActions = (payload.prd_evaluation_gate?.coverage_gate?.failures || [])
        .filter((item) => item.name === "prd_evaluation_coverage_failed")
        .map((item) => ({
          name: item.name,
          detail: item.details?.join(" ") || "PRD evaluation coverage is below the required case thresholds.",
          action: "Restore the full PRD evaluation set before treating readiness as complete.",
          profileLabel,
        }));
      return nextActions.concat(liveGateActions, evaluationGateActions, evaluationCoverageActions);
    })
    .slice(0, 6)
    .map(
      (item) => `
        <div class="monitor-recent">
          <strong>${escapeHtml(item.profileLabel)} · ${escapeHtml(item.name || "gate")}</strong>
          <span>${escapeHtml(item.detail || "Required readiness gate failed.")}</span>
          <small>${escapeHtml(item.action || "Resolve this readiness gate before demo or pilot deployment.")}</small>
        </div>
      `,
    );

  container.innerHTML = rows.length
    ? rows.join("")
    : `<div class="monitor-empty">No failed required gates</div>`;
}

async function loadMonitoring() {
  monitorStatus.textContent = "Loading";
  try {
    const role = roleSelect.value;
    const branchId = currentBranchId();
    const [
      metrics,
      feedback,
      audit,
      verified,
      catalogGovernance,
      evaluation,
      readiness,
      pocReadiness,
      pilotReadiness,
    ] = await Promise.all([
      fetchMonitorJson("/api/metrics"),
      fetchMonitorJson("/api/feedback-summary"),
      fetchMonitorJson("/api/audit-summary"),
      fetchMonitorJson(`/api/verified-questions?role=${encodeURIComponent(role)}&branchId=${branchId}`),
      fetchMonitorJson(`/api/catalog-governance?role=${encodeURIComponent(role)}`),
      fetchMonitorJson(`/api/evaluation-summary?role=${encodeURIComponent(role)}&branchId=${branchId}`),
      fetchMonitorJson("/api/readiness"),
      fetchMonitorJson("/api/readiness?profile=poc"),
      fetchMonitorJson("/api/readiness?profile=pilot"),
    ]);

    renderMonitorStats(runtimeMetrics, [
      ["queries_total", metrics.metrics?.queries_total ?? 0],
      ["queries_blocked_total", metrics.metrics?.queries_blocked_total ?? 0],
      ["feedback_total", metrics.metrics?.feedback_total ?? 0],
      ["sessions", metrics.sessions?.results ?? 0],
    ]);
    renderCounterSummary(
      auditSummaryList,
      audit.by_execution_status,
      audit.recent,
      (item) => `${item.execution_status || "-"} · ${item.question || "-"}`,
      "No audit events",
    );
    renderBacklogSummary(feedbackSummaryList, feedback.semantic_backlog, feedback.by_category, feedback.recent);
    renderMonitorStats(verifiedSummary, [
      ["verified_total", verified.verified_total ?? 0],
      ["safety_total", verified.safety_total ?? 0],
      ["role", verified.role || role],
      ["branch_id", verified.branch_id ?? branchId],
    ]);
    renderMonitorStats(catalogGovernanceSummary, [
      ["visible_metrics", catalogGovernance.visibleMetricCount ?? 0],
      ["visible_tables", catalogGovernance.tableCount ?? 0],
      ["issues", catalogGovernance.issueCount ?? 0],
      ["status", catalogGovernance.status || "-"],
    ]);
    renderMonitorStats(evaluationSummary, [
      ["total_cases", evaluation.total_cases ?? 0],
      ["blocked_cases", evaluation.blocked_cases ?? 0],
      ["follow_up_cases", evaluation.follow_up_cases ?? 0],
      ["gold_coverage", evaluation.gold_coverage_ratio ?? 0],
    ]);
    renderMonitorStats(readinessSummary, [
      ["local_required_failed", readiness.summary?.required_failed ?? 0],
      ["poc_required_failed", pocReadiness.summary?.required_failed ?? 0],
      ["pilot_required_failed", pilotReadiness.summary?.required_failed ?? 0],
      ["poc_gate", pocReadiness.readiness_gate?.status || "-"],
      ["pilot_gate", pilotReadiness.readiness_gate?.status || "-"],
      ["poc_eval_gate", pocReadiness.prd_evaluation_gate?.status || "-"],
      ["pilot_eval_gate", pilotReadiness.prd_evaluation_gate?.status || "-"],
      ["pilot_next_actions", pilotReadiness.next_actions?.length ?? 0],
      ["live_checks", pilotReadiness.live_checks_requested ? "on" : "off"],
    ]);
    renderReadinessActions(readinessActionList, [
      ["POC", pocReadiness],
      ["Pilot", pilotReadiness],
    ]);
    monitorStatus.textContent = "Updated";
  } catch (error) {
    monitorStatus.textContent = "Unavailable";
    runtimeMetrics.innerHTML = `<div class="monitor-empty">${escapeHtml(error.message)}</div>`;
    auditSummaryList.innerHTML = `<div class="monitor-empty">${escapeHtml(error.message)}</div>`;
    feedbackSummaryList.innerHTML = `<div class="monitor-empty">${escapeHtml(error.message)}</div>`;
    verifiedSummary.innerHTML = `<div class="monitor-empty">${escapeHtml(error.message)}</div>`;
    catalogGovernanceSummary.innerHTML = `<div class="monitor-empty">${escapeHtml(error.message)}</div>`;
    evaluationSummary.innerHTML = `<div class="monitor-empty">${escapeHtml(error.message)}</div>`;
    readinessSummary.innerHTML = `<div class="monitor-empty">${escapeHtml(error.message)}</div>`;
    readinessActionList.innerHTML = `<div class="monitor-empty">${escapeHtml(error.message)}</div>`;
  }
}

function syncResultGridHeight() {
  const table = tableWrap.querySelector("table");
  if (!table || !hasRenderedTable) {
    dataPane.style.removeProperty("--result-grid-height");
    return;
  }

  const targetHeight = window.calculateResultGridHeight({
    dataPaneHeight: dataPane.clientHeight,
    tableTopbarHeight: tableTopbar.offsetHeight,
    queryComposerHeight: queryComposer.offsetHeight,
    gridFooterHeight: gridFooter.offsetHeight,
    tableScrollHeight: table.scrollHeight,
  });

  dataPane.style.setProperty("--result-grid-height", `${targetHeight}px`);
}

function renderTable(columns, rows) {
  if (!rows.length) {
    hasRenderedTable = false;
    dataPane.style.removeProperty("--result-grid-height");
    tableWrap.innerHTML = `<div class="empty-state">조건에 맞는 데이터가 없습니다.</div>`;
    return;
  }

  const header = [
    `<th class="row-check"><span class="fake-checkbox"></span></th>`,
    ...columns.map((column) => `<th>${escapeHtml(column)}</th>`),
  ].join("");
  const body = rows
    .map(
      (row) =>
        `<tr><td class="row-check"><span class="fake-checkbox"></span></td>${columns
          .map((column) => `<td>${escapeHtml(formatCompact(row[column]))}</td>`)
          .join("")}</tr>`,
    )
    .join("");

  hasRenderedTable = true;
  tableWrap.innerHTML = `<table><thead><tr>${header}</tr></thead><tbody>${body}</tbody></table>`;
  requestAnimationFrame(syncResultGridHeight);
}

function renderMiniResult(columns, rows) {
  if (!rows.length) {
    miniResult.innerHTML = `<div class="mini-result-title">Result preview</div><div class="mini-empty">조건에 맞는 데이터가 없습니다.</div>`;
    return;
  }

  const visibleColumns = columns.slice(0, 2);
  const visibleRows = rows.slice(0, 6);
  const header = visibleColumns.map((column) => `<th>${escapeHtml(column)}</th>`).join("");
  const body = visibleRows
    .map(
      (row) =>
        `<tr>${visibleColumns
          .map((column) => `<td>${escapeHtml(formatCompact(row[column]))}</td>`)
          .join("")}</tr>`,
    )
    .join("");

  miniResult.innerHTML = `
    <div class="mini-result-title">Result preview</div>
    <table><thead><tr>${header}</tr></thead><tbody>${body}</tbody></table>
  `;
}

function renderTrace(data) {
  if (Array.isArray(data.executionTrace) && data.executionTrace.length) {
    traceList.innerHTML = data.executionTrace
      .map((item) => {
        const status = String(item.status || "").toLowerCase();
        const stateClass = status.includes("blocked") || status.includes("failed") ? "warn" : "";
        const queryPlan = Array.isArray(item.metadata?.queryPlan) ? item.metadata.queryPlan : [];
        const planDetail = item.node === "Query Execution" && queryPlan.length ? ` · plan: ${queryPlan[0]}` : "";
        const precheckRows = item.metadata?.preExecutionRowCount;
        const precheckDetail = item.node === "Query Execution" && precheckRows !== undefined && precheckRows !== null
          ? ` · precheck: ${precheckRows} rows`
          : "";
        return `<div class="trace-item ${stateClass}">${escapeHtml(item.node)}: ${escapeHtml((item.detail || item.status || "-") + precheckDetail + planDetail)}</div>`;
      })
      .join("");
    return;
  }

  const validation = data.validation || {};
  const metrics = (data.metrics || []).map((metric) => metric.name).join(", ");
  const tables = (data.tables || []).map((table) => table.name).join(", ");
  const issues = validation.issues || [];
  const generationEngine = data.generationEngine ? data.generationEngine.toUpperCase() : "LLM";
  const validationText = validation.allowed ? "SQL Validation passed" : `SQL Validation blocked: ${issues.join(", ")}`;
  const validationClass = validation.allowed ? "" : "warn";

  traceList.innerHTML = [
    `<div class="trace-item">Semantic Layer: ${escapeHtml(metrics || "-")}</div>`,
    `<div class="trace-item">Schema Retrieval: ${escapeHtml(tables || "-")}</div>`,
    `<div class="trace-item">SQL Generation: ${escapeHtml(generationEngine)}</div>`,
    `<div class="trace-item ${validationClass}">${escapeHtml(validationText)}</div>`,
  ].join("");
}

function renderClarificationOptions(options) {
  const visibleOptions = Array.isArray(options) ? options.filter(Boolean).slice(0, 3) : [];
  if (!visibleOptions.length) {
    clarificationList.innerHTML = "";
    clarificationList.hidden = true;
    return;
  }

  clarificationList.hidden = false;
  clarificationList.innerHTML = visibleOptions
    .map(
      (option) =>
        `<button class="clarification-chip" type="button" data-question="${escapeHtml(option)}">${escapeHtml(option)}</button>`,
    )
    .join("");
}

function renderResult(data, elapsedMs) {
  const validation = data.validation || {};
  const tableNames = (data.tables || []).map((table) => table.name);
  activeSql = data.sql || "";
  conversationContext = data.conversationContext || {};
  sessionId = data.sessionId || sessionId;
  localStorage.setItem(sessionStorageKey, sessionId);
  roleSelect.value = data.role || roleSelect.value;
  branchScopeInput.value = String(data.branchId || currentBranchId());
  syncRoleControls();

  runtimeLabel.textContent = data.graphRuntime || "LangGraph";
  generationLabel.textContent = formatGenerationStatus(data);
  generationLabel.title = formatGenerationTitle(data);
  validationMetric.textContent = validation.allowed ? "Passed" : "Blocked";
  validationMetric.className = validation.allowed ? "success" : "blocked";
  rowMetric.textContent = String(data.rowCount ?? 0);
  tableMetric.textContent = String(tableNames.length || "-");
  latencyBadge.textContent = `${elapsedMs} ms`;
  sqlBlock.textContent = activeSql || "SELECT ...";
  chatQuestion.textContent = data.question || input.value.trim();
  chatSummary.textContent = validation.allowed
    ? `${data.generationReason} 참조 테이블: ${tableNames.join(", ")}.`
    : `검증에서 차단되었습니다. ${validation.issues?.join(" ") || ""} ${data.retryGuidance || ""}`.trim();

  renderTable(data.columns || [], data.rows || []);
  renderMiniResult(data.columns || [], data.rows || []);
  renderSchemaList(data.tables || []);
  renderTrace(data);
  renderClarificationOptions(data.clarificationOptions || []);
  resetFeedbackState();
}

function renderError(message) {
  hasRenderedTable = false;
  dataPane.style.removeProperty("--result-grid-height");
  validationMetric.textContent = "Error";
  validationMetric.className = "blocked";
  generationLabel.textContent = "generation error";
  generationLabel.title = "";
  rowMetric.textContent = "0";
  tableMetric.textContent = "-";
  latencyBadge.textContent = "failed";
  chatSummary.textContent = message;
  renderClarificationOptions([]);
  tableWrap.innerHTML = `<div class="empty-state error-text">${escapeHtml(message)}</div>`;
  miniResult.innerHTML = `<div class="mini-result-title">Result preview</div><div class="mini-empty error-text">${escapeHtml(message)}</div>`;
  traceList.innerHTML = `<div class="trace-item danger">${escapeHtml(message)}</div>`;
}

function showInvalidInput(inputElement, message) {
  inputElement.setCustomValidity(message);
  inputElement.reportValidity();
  inputElement.addEventListener("input", () => inputElement.setCustomValidity(""), { once: true });
}

function validateQuestionInput(inputElement) {
  const question = inputElement.value.trim();
  if (!question) {
    showInvalidInput(inputElement, "질문을 입력해주세요.");
    return false;
  }
  if (question.length > questionMaxLength) {
    showInvalidInput(inputElement, `질문은 ${questionMaxLength}자 이하로 입력해주세요.`);
    return false;
  }
  inputElement.setCustomValidity("");
  return true;
}

async function runQuestion(question) {
  if (!question) {
    showInvalidInput(input, "질문을 입력해주세요.");
    renderError("질문을 입력해주세요.");
    return;
  }
  if (question.length > questionMaxLength) {
    const message = `질문은 ${questionMaxLength}자 이하로 입력해주세요.`;
    showInvalidInput(input, message);
    renderError(message);
    return;
  }

  setLoading(true);
  chatQuestion.textContent = question;
  chatSummary.textContent = "질문을 관련 스키마와 지표 정의에 매핑하고 있습니다.";
  renderClarificationOptions([]);
  const startedAt = performance.now();

  try {
    const response = await fetch("/api/query", {
      method: "POST",
      headers: apiHeaders(),
      body: JSON.stringify({
        question,
        role: roleSelect.value,
        branchId: currentBranchId(),
        sessionId,
        conversationContext,
      }),
    });
    const data = await response.json();
    if (!response.ok) {
      throw new Error(data.error || "요청이 실패했습니다.");
    }
    renderResult(data, Math.round(performance.now() - startedAt));
  } catch (error) {
    renderError(error.message);
  } finally {
    setLoading(false);
  }
}

async function submitFeedback() {
  const comment = feedbackComment.value.trim();
  if (!selectedFeedbackRating && !comment) {
    feedbackStatus.textContent = "rating or note required";
    feedbackStatus.className = "feedback-status is-error";
    return;
  }

  feedbackStatus.textContent = "saving";
  feedbackStatus.className = "feedback-status";

  try {
    const response = await fetch("/api/feedback", {
      method: "POST",
      headers: apiHeaders(),
      body: JSON.stringify({
        sessionId,
        role: roleSelect.value,
        branchId: currentBranchId(),
        rating: selectedFeedbackRating,
        category: feedbackCategory.value,
        comment,
      }),
    });
    const data = await response.json();
    if (!response.ok) {
      throw new Error(data.error || "feedback failed");
    }
    feedbackStatus.textContent = "saved";
    feedbackStatus.className = "feedback-status is-success";
    feedbackComment.value = "";
  } catch (error) {
    feedbackStatus.textContent = error.message;
    feedbackStatus.className = "feedback-status is-error";
  }
}

async function loadSamples() {
  try {
    const response = await fetch("/api/demo-questions");
    const data = await response.json();
    renderSamples(data.questions || []);
  } catch {
    renderSamples([
      "지난 3개월간 지점별 신규 계좌 수 추이는?",
      "이번 달 고위험 상품 가입 건수가 많은 지점은?",
      "최근 30일 VOC 유형별 처리 현황 알려줘.",
      "영업점별 ELS 가입 금액과 민원 건수를 비교해줘.",
    ]);
  }
}

function filterContext(term) {
  const normalized = term.trim().toLowerCase();
  document.querySelectorAll("[data-search-item]").forEach((item) => {
    const text = item.dataset.searchItem?.toLowerCase() || item.textContent.toLowerCase();
    item.classList.toggle("is-hidden", Boolean(normalized) && !text.includes(normalized));
  });
}

form.addEventListener("submit", (event) => {
  event.preventDefault();
  if (!validateQuestionInput(input)) {
    return;
  }
  showWorkspace();
  runQuestion(input.value.trim());
});

homeForm.addEventListener("submit", (event) => {
  event.preventDefault();
  if (!validateQuestionInput(homeInput)) {
    return;
  }
  const question = homeInput.value.trim();
  input.value = question;
  homeInput.value = "";
  chatInput.value = "";
  showWorkspace();
  runQuestion(question);
});

chatForm.addEventListener("submit", (event) => {
  event.preventDefault();
  if (!validateQuestionInput(chatInput)) {
    return;
  }
  const question = chatInput.value.trim();
  input.value = question;
  chatInput.value = "";
  showWorkspace();
  runQuestion(question);
});

clarificationList.addEventListener("click", (event) => {
  const button = event.target.closest(".clarification-chip");
  if (!button) {
    return;
  }
  const question = button.dataset.question;
  input.value = question;
  chatInput.value = "";
  showWorkspace();
  runQuestion(question);
});

sampleList.addEventListener("click", (event) => {
  const button = event.target.closest(".sample-button");
  if (!button) {
    return;
  }
  input.value = button.dataset.question;
  document.querySelectorAll(".sample-button").forEach((item) => item.classList.remove("active"));
  button.classList.add("active");
  showWorkspace();
  runQuestion(input.value.trim());
});

homeSampleList.addEventListener("click", (event) => {
  const button = event.target.closest(".home-sample-button");
  if (!button) {
    return;
  }
  const question = button.dataset.question;
  homeInput.value = question;
  input.value = question;
  chatInput.value = "";
  showWorkspace();
  runQuestion(question);
});

globalSearch.addEventListener("input", (event) => {
  filterContext(event.target.value);
});

filterButton.addEventListener("click", () => {
  globalSearch.focus();
});

resetButton.addEventListener("click", () => {
  input.value = initialQuestion;
  activeSql = "";
  conversationContext = {};
  sessionId = createSessionId();
  roleSelect.value = "branch_manager";
  branchScopeInput.value = "1";
  syncRoleControls();
  loadCatalog();
  hasRenderedTable = false;
  dataPane.style.removeProperty("--result-grid-height");
  chatQuestion.textContent = initialQuestion;
  chatSummary.textContent = "질문을 실행하면 생성 기준, 검증 결과, 참조 스키마가 표시됩니다.";
  renderClarificationOptions([]);
  tableWrap.innerHTML = `<div class="empty-state">Run을 눌러 결과를 확인하세요.</div>`;
  miniResult.innerHTML = `<div class="mini-result-title">Result preview</div><div class="mini-empty">No rows yet</div>`;
  sqlBlock.textContent = "SELECT ...";
  validationMetric.textContent = "Ready";
  validationMetric.className = "";
  runtimeLabel.textContent = "LangGraph";
  generationLabel.textContent = "LLM pending";
  generationLabel.title = "";
  rowMetric.textContent = "0";
  tableMetric.textContent = "-";
  latencyBadge.textContent = "idle";
  resetFeedbackState();
});

roleSelect.addEventListener("change", () => {
  conversationContext = {};
  syncRoleControls();
  loadCatalog();
  if (!monitorView.hidden) {
    loadMonitoring();
  }
});

catalogRoleSelect.addEventListener("change", () => {
  roleSelect.value = catalogRoleSelect.value;
  conversationContext = {};
  syncRoleControls();
  loadCatalog();
  if (!monitorView.hidden) {
    loadMonitoring();
  }
});

branchScopeInput.addEventListener("input", () => {
  conversationContext = {};
  syncRoleControls();
  if (!monitorView.hidden) {
    loadMonitoring();
  }
});

copySqlButton.addEventListener("click", async () => {
  if (!activeSql) {
    return;
  }
  await navigator.clipboard.writeText(activeSql);
  copySqlButton.querySelector("span").textContent = "Copied";
  setTimeout(() => {
    copySqlButton.querySelector("span").textContent = "Copy SQL";
  }, 1200);
});

async function downloadExport(exportType) {
  const response = await fetch("/api/export", {
    method: "POST",
    headers: apiHeaders(),
    body: JSON.stringify({ sessionId, exportType }),
  });
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    renderError(data.error || "내보내기에 실패했습니다.");
    return;
  }
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = exportType === "report" ? "im-one-report.md" : "im-one-results.csv";
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

exportCsvButton.addEventListener("click", () => {
  downloadExport("csv");
});

reportButton.addEventListener("click", () => {
  downloadExport("report");
});

feedbackButtons.forEach((button) => {
  button.addEventListener("click", () => {
    setFeedbackRating(button.dataset.feedbackRating);
  });
});

feedbackForm.addEventListener("submit", (event) => {
  event.preventDefault();
  submitFeedback();
});

themeOptions.forEach((button) => {
  button.addEventListener("click", () => {
    applyTheme(button.dataset.themeOption);
  });
});

homeButton.addEventListener("click", showHome);
workspaceButton.addEventListener("click", showWorkspace);
catalogButton.addEventListener("click", showCatalog);
monitorButton.addEventListener("click", showMonitor);
refreshCatalogButton.addEventListener("click", loadCatalog);
refreshMonitorButton.addEventListener("click", loadMonitoring);
window.addEventListener("resize", syncResultGridHeight);

setupTheme();
setupRoleControls();
loadCatalog();
loadSamples();
renderIcons();
showHome();
