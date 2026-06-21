const state = {
  activeView: "overview",
  activeSettingsTab: "resources",
  companies: [],
  selectedCompanyId: "",
  defaultCompanyId: "",
  defaultAppConfigId: "",
  defaultMailboxId: "",
  botUsers: [],
  selectedResourceId: "",
  selectedResourceLabel: "",
  selectedToolName: "",
  selectedToolSupportsWrite: false,
  selectedToolCompatibleProviders: [],
  selectedToolAuditAction: "",
  selectedToolParamTemplateName: "",
  lastToolExecutionLogFilter: null,
  discoverIncludeLocal: true,
  databaseUnavailable: false,
  replyModes: null,
  capabilityRegistry: null,
  capabilityRegistryCompanyId: "",
  capabilityRegistryDiffs: [],
  legacyToolConfigsByName: {},
};

const viewMeta = {
  overview: ["经营智能中心", "多公司状态、经营风险、关键任务、数据覆盖和员工智能体"],
  "company-space": ["公司经营空间", "公司级飞书 App、数据覆盖、WorkEvent、员工智能体和大飞哥状态"],
  entrypoints: ["交互入口", "大飞哥机器人、管理后台和 iOS 入口状态"],
  "today-focus": ["今日重点", "当日经营重点、风险、待办和待决策事项"],
  risks: ["风险中心", "开放风险、异常信号和优先处理建议"],
  tasks: ["待办事项", "开放任务、责任人和处理建议"],
  approvals: ["审批动态", "审批事件、付款报销和待处理动作"],
  projects: ["项目动态", "项目、交付、研发和客户现场动态"],
  decisions: ["决策事项", "开放决策、决策依据和后续动作"],
  communications: ["消息邮件", "飞书消息、邮件和沟通线索"],
  meetings: ["会议日程", "会议、日程和待跟进纪要"],
  reports: ["报告中心", "日报、周报、月报、风险和决策报告"],
  settings: ["设置", "公司与资源、知识库、权限、AI 和审计配置"],
};

const businessToolFamilies = [
  ["ApprovalTool", "审批", "审批查询、审批动作和审批卡片"],
  ["KnowledgeTool", "知识", "Docs、Wiki、制度、会议纪要和 RAG"],
  ["BitableTool", "多维表格", "业务表、客户、项目和结构化记录"],
  ["ChatTool", "消息邮件", "群聊、消息、邮件和沟通线索"],
  ["CalendarTool", "日历", "日程、忙闲和时间安排"],
  ["MeetingTool", "会议", "历史会议、妙记、纪要和参会信息"],
  ["ReportTool", "报告", "日报、周报、经营报告和风险报告"],
  ["AutomationTool", "自动化", "任务、提醒、流程和条件触发"],
  ["PeopleTool", "人员", "通讯录、考勤、绩效和薪酬"],
];

document.addEventListener("DOMContentLoaded", () => {
  bindNavigation();
  bindActions();
  setToday();
  bootstrap();
});

function headers() {
  const token = document.getElementById("adminToken").value.trim();
  const base = { "Content-Type": "application/json" };
  return token ? { ...base, "X-Admin-Token": token } : base;
}

async function api(path, options = {}) {
  const res = await fetch(path, { ...options, headers: { ...headers(), ...(options.headers || {}) } });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(JSON.stringify(data, null, 2) || res.statusText);
  }
  return data;
}

function bindNavigation() {
  document.querySelectorAll("[data-view]").forEach((button) => {
    button.addEventListener("click", () => showView(button.dataset.view));
  });
}

function bindActions() {
  document.getElementById("refreshBtn").addEventListener("click", () => loadCurrentView());
  document.getElementById("companySelect").addEventListener("change", (event) => {
    state.selectedCompanyId = event.target.value;
    loadCurrentView();
  });
  document.querySelectorAll("[data-action]").forEach((button) => {
    button.addEventListener("click", () => runAction(button.dataset.action));
  });
  document.querySelectorAll("[data-settings-tab]").forEach((button) => {
    button.addEventListener("click", () => showSettingsTab(button.dataset.settingsTab));
  });
  const providerSelect = document.getElementById("selectedToolProvider");
  if (providerSelect) providerSelect.addEventListener("change", renderToolRuntimeHint);
}

function showView(name) {
  if (!viewMeta[name]) return;
  state.activeView = name;
  document.querySelectorAll("[data-view]").forEach((item) => item.classList.toggle("active", item.dataset.view === name));
  document.querySelectorAll(".view").forEach((view) => view.classList.toggle("active", view.id === name));
  document.getElementById("pageTitle").textContent = viewMeta[name][0];
  document.getElementById("pageSubtitle").textContent = viewMeta[name][1];
  window.scrollTo({ top: 0, behavior: "smooth" });
  loadCurrentView();
}

async function bootstrap() {
  await loadOverview();
  applyConsoleUrlParams();
  if (state.databaseUnavailable) return;
  await loadDashboardDefaults();
  await loadCompanies({ silent: true });
  applyConsoleUrlParams();
  await loadCurrentView();
}

async function loadCurrentView() {
  if (state.databaseUnavailable && state.activeView !== "overview") {
    const operatingState = await loadOperatingCenter();
    if (operatingState?.databaseUnavailable) return;
  }
  const loaders = {
    overview: loadOverview,
    "company-space": loadCompanySpace,
    entrypoints: loadEntrypoints,
    "today-focus": loadTodayFocus,
    risks: loadRisks,
    tasks: loadTasks,
    approvals: loadApprovals,
    projects: loadProjects,
    decisions: loadDecisions,
    communications: loadCommunications,
    meetings: loadMeetings,
    reports: loadReportsView,
    settings: loadSettings,
  };
  return loaders[state.activeView]?.();
}

async function loadDashboardDefaults() {
  const data = await safeLoad("/api/dashboard/overview");
  if (!data) return;
  state.defaultCompanyId = data.default_company_id || state.defaultCompanyId;
  state.defaultAppConfigId = data.default_app_config_id || state.defaultAppConfigId;
  state.defaultMailboxId = data.default_mailbox_id || state.defaultMailboxId;
  fillDiscoveryDefaults();
}

function runAction(action) {
  const actions = {
    "load-sync-runs": loadSyncRuns,
    "load-companies": loadCompanies,
    "load-company-space": loadCompanySpace,
    "load-entrypoints": loadEntrypoints,
    "quick-setup": quickSetup,
    "validate-feishu-app": validateFeishuAppConfig,
    "load-v5": loadAdministrationFoundation,
    "bootstrap-v5": bootstrapFoundation,
    "preset-discover-core": () => setDiscoverPreset("contacts,chats,approvals,calendar,meetings,tasks", true),
    "preset-discover-mail": () => setDiscoverPreset("mail", false),
    "preset-discover-docs": () => setDiscoverPreset("drive,wiki,bitable", true),
    "preset-discover-local": () => setDiscoverPreset("local,bitable,approvals", true),
    "discover-resources": discoverResources,
    "open-feishu-user-oauth": openFeishuUserOAuth,
    "complete-feishu-cli-user-auth": completeFeishuCliUserAuth,
    "probe-user-identity-tools": probeUserIdentityTools,
    "load-feishu-user-accounts": loadFeishuUserAccounts,
    "load-resources": loadResources,
    "open-ai": openAiModal,
    "close-ai": closeAiModal,
    "advisor-chat": advisorChat,
    "load-risks": loadRisks,
    "load-today-focus": loadTodayFocus,
    "load-tasks": loadTasks,
    "load-approvals": loadApprovals,
    "load-projects": loadProjects,
    "load-decisions": loadDecisions,
    "load-communications": loadCommunications,
    "load-meetings": loadMeetings,
    "sync-selected-resource": syncSelectedResource,
    "retry-selected-resource-block": retrySelectedResourceBlock,
    "clear-selected-resource-block": clearSelectedResourceBlock,
    "mark-selected-business-group": () => setSelectedResourceAccessDecision("business_group"),
    "mark-selected-owner-confirmed": () => setSelectedResourceAccessDecision("owner_confirmed"),
    "mark-selected-do-not-connect": () => setSelectedResourceAccessDecision("do_not_connect"),
    "ignore-selected-access-suggestion": () => setSelectedResourceAccessDecision("ignore"),
    "reset-selected-access-decision": () => setSelectedResourceAccessDecision("reset"),
    "load-items": loadItems,
    "generate-report": generateReport,
    "load-reports": loadReports,
    "load-knowledge": loadKnowledge,
    "discover-knowledge-resources": discoverKnowledgeResources,
    "preview-knowledge-sync": previewKnowledgeSync,
    "vector-search": vectorSearch,
    "load-sync-strategy": loadSyncStrategy,
    "load-sync-policy": loadSyncPolicy,
    "save-sync-policy": saveSyncPolicy,
    "load-resource-sync-status": loadResourceSyncStatus,
    "load-resource-launch-plan": loadResourceLaunchPlan,
    "discover-company-space-resources": discoverCompanySpaceResources,
    "discover-launch-local-resources": discoverLaunchLocalResources,
    "preview-launch-sync": previewLaunchSync,
    "sync-launch-pending": syncLaunchPending,
    "load-resource-monitoring": loadResourceMonitoring,
    "preview-stale-resources": previewStaleResources,
    "sync-stale-resources": syncStaleResources,
    "load-bot-users": loadBotUsers,
    "load-permission-rules": loadPermissionRules,
    "preview-permissions": () => recalculatePermissions(true),
    "apply-permissions": () => recalculatePermissions(false),
    "load-audit": loadAudit,
    "load-system-logs": loadSystemLogs,
    "view-gateway-card-issues": viewGatewayCardIssues,
    "view-agent-runtime-gateway": viewAgentRuntimeGatewayLogs,
    "load-tool-executions": loadToolExecutions,
    "load-tools": loadToolConfigurations,
    "execute-tool-dry-run": () => executeSelectedTool("dry_run"),
    "execute-tool-confirmed": () => executeSelectedTool("confirmed"),
    "view-tool-system-logs": viewToolSystemLogs,
    "save-tool-config": saveToolConfig,
    "preview-tool-batch": () => updateToolBatch(true),
    "apply-tool-batch": () => updateToolBatch(false),
    "load-agent-settings": loadAgentSettings,
    "load-reply-modes": loadAgentReplyModes,
    "save-agent-settings": saveAgentSettings,
    "preview-agent-fast": () => previewAgentReplyModePreset("fast"),
    "preview-agent-normal": () => previewAgentReplyModePreset("normal"),
    "preview-agent-thinking": () => previewAgentReplyModePreset("thinking"),
    "preview-agent-reply-mode": () => previewAgentReplyMode(),
    "load-agent-traces": loadAgentTraces,
  };
  return actions[action]?.();
}

async function loadOverview() {
  fillDiscoveryDefaults();
  const operatingState = await loadOperatingCenter();
  await loadAgentReplyModes({ silent: true });
  await loadCapabilityRegistry({ silent: true });
  if (operatingState?.databaseUnavailable) {
    renderOwnerCommandCenter({ modules: [] }, operatingState);
    renderV5ArchitectureBoard(operatingState);
    renderCockpitCards({ modules: [] });
    renderOverviewGovernanceActions({ modules: [] });
    renderMetrics({ modules: [] });
    return;
  }
  const data = await safeLoad(`/api/cockpit/overview${companyQuery("limit=8")}`);
  if (!data) {
    renderOwnerCommandCenter({ modules: [] }, operatingState);
    renderV5ArchitectureBoard(operatingState);
    renderCockpitCards({ modules: [] });
    renderOverviewGovernanceActions({ modules: [] });
    renderMetrics({ modules: [] });
    return;
  }
  renderOwnerCommandCenter(data, operatingState);
  renderV5ArchitectureBoard(operatingState);
  renderCockpitCards(data);
  renderOverviewGovernanceActions(data);
  renderMetrics(data);
}

async function loadOperatingCenter() {
  const company = companyQuery();
  const toolPath = state.selectedCompanyId
    ? `/api/v5/tools?company_id=${encodeURIComponent(state.selectedCompanyId)}`
    : null;
  const os = await safeLoad(`/api/v5/os/overview${company}`);
  const databaseUnavailable = !os || os?.status?.database === "unavailable";
  state.databaseUnavailable = databaseUnavailable;
  prefillQuickSetupFromOverview(os);
  if (databaseUnavailable) renderCompanyUnavailable();
  let syncStatus = null;
  let tools = null;
  if (!databaseUnavailable) {
    [syncStatus, tools] = await Promise.all([
      safeLoad(`/api/v5/resources/sync-status${company}`),
      toolPath ? safeLoad(toolPath) : Promise.resolve(null),
    ]);
  }
  renderOperatingCenter(os, syncStatus, tools);
  return { databaseUnavailable, os, syncStatus, tools };
}

async function loadCompanySpace() {
  const os = await safeLoad("/api/v5/os/overview");
  if (!os || os?.status?.database === "unavailable") {
    renderCompanySpace(os, null, null, null, null, "");
    return;
  }
  const companies = os.companies || [];
  const effectiveCompanyId = state.selectedCompanyId || (companies.length === 1 ? companies[0].id : "");
  if (!state.selectedCompanyId && effectiveCompanyId) {
    state.selectedCompanyId = effectiveCompanyId;
    const select = document.getElementById("companySelect");
    if (select) select.value = effectiveCompanyId;
  }
  const companyParam = effectiveCompanyId ? `company_id=${encodeURIComponent(effectiveCompanyId)}` : "";
  const query = companyParam ? `?${companyParam}` : "";
  const limitQuery = companyParam ? `?${companyParam}&limit=50` : "?limit=50";
  const [companyOverview, syncStatus, events, cockpit] = await Promise.all([
    safeLoad(`/api/v5/resources/company-overview${query}`),
    safeLoad(`/api/v5/resources/sync-status${query}`),
    safeLoad(`/api/v5/workspace/events${limitQuery}`),
    safeLoad(`/api/cockpit/overview${limitQuery}`),
  ]);
  renderCompanySpace(os, companyOverview, syncStatus, events, cockpit, effectiveCompanyId);
}

async function discoverCompanySpaceResources() {
  const appConfigId = await ensureDefaultAppConfigId("companySpaceActionResult");
  if (!appConfigId) return;
  const result = await safeAction("companySpaceActionResult", async () => api(`/api/feishu/apps/${appConfigId}/resources/discover`, {
    method: "POST",
    body: JSON.stringify({
      kinds: ["local"],
      async_run: false,
      limit: 50,
      include_local_mining: true,
    }),
  }));
  if (result) await loadCompanySpace();
}

async function loadEntrypoints() {
  await loadAgentReplyModes({ silent: true });
  const data = await safeLoad(`/api/v5/entrypoints/status${companyQuery()}`);
  renderEntrypoints(data);
}

function renderEntrypoints(data) {
  const entrypoints = data?.entrypoints || [];
  const bot = entrypoints.find((item) => item.key === "feishu_bot") || {};
  const admin = entrypoints.find((item) => item.key === "admin_console") || {};
  const ios = entrypoints.find((item) => item.key === "ios_app") || {};
  const apps = bot.apps || [];
  const recentEvents = bot.recent_events || [];
  const recentGatewayMessages = bot.recent_gateway_messages || [];
  const gatewayRuntime = bot.gateway_runtime || {};
  const identityStatus = bot.cli_readiness?.identity_status || {};
  setHtml("entrypointStatusPills", [
    ["大飞哥", bot.status || "unknown"],
    ["飞书 App", bot.app_count ?? apps.length],
    ["已校验 App", bot.validated_app_count ?? 0],
    ["最近事件", recentEvents.length],
    ["最近消息", recentGatewayMessages.length],
    ["Agent链路", gatewayRuntime.agent_runtime_messages ?? 0],
    ["Tool异常", gatewayRuntime.tool_error_count ?? 0],
    ["流程异常", gatewayRuntime.workflow_error_count ?? 0],
    ["Bot身份", identityStatus.bot_identity?.message || "-"],
    ["User身份", identityStatus.user_identity?.message || "-"],
    ["管理后台", admin.status || "unknown"],
    ["iOS", ios.status || "planned"],
  ].map(([label, value]) => `
    <div class="command-pill">
      <span>${escapeHtml(label)}</span>
      <strong>${escapeHtml(value)}</strong>
    </div>
  `).join(""));
  const userIdentityMessage = identityStatus.user_identity?.message || "";
  const identityNotice = (userIdentityMessage.includes("needs refresh") || userIdentityMessage.includes("missing"))
    ? [{
        label: "用户授权",
        key: "feishu_user_identity",
        status: "attention",
        detail: "User token 缺失或需要刷新；不影响大飞哥 bot 接收和回复，影响需要用户身份的 CLI 操作",
      }]
    : [];
  setHtml("entrypointCards", entrypoints.concat(identityNotice).map((item) => `
    <div class="architecture-row">
      <span>${escapeHtml(item.label || item.key)}</span>
      <strong>${escapeHtml(localizeCellValue(item.status || "unknown"))}</strong>
      <small>${escapeHtml(entrypointDetail(item))}</small>
    </div>
  `).join("") || `<div class="empty">暂无入口状态</div>`);
  const boundary = data?.boundary || {};
  const identityBoundary = boundary.identity_boundary || bot.identity_boundary || {};
  setHtml("entrypointBoundaryBoard", [
    ["AI 实时操作", boundary.realtime_operation || "Message Gateway -> Agent Runtime -> Tool Router -> Tool -> 数据源/执行源 -> Agent Runtime -> Message Gateway"],
    ["数据同步入库", boundary.sync_ingestion || "Sync Engine -> API Client -> Feishu -> PostgreSQL"],
    ["Agent 模型", identityBoundary.agent_model || "每个飞书用户一个专属 Agent"],
    ["Tool 模型", identityBoundary.tool_model || "9 个业务 Tool 全局共享，Agent 可以访问并调用所有 Tool"],
    ["数据边界", identityBoundary.tool_data_boundary_rule || "工具是全局共享的；数据是有边界的"],
    ["企业资源边界", identityBoundary.enterprise_resource_boundary || "App Identity + Company Scope + Role Scope"],
    ["用户资源边界", identityBoundary.user_resource_boundary || "User Identity + Resource Owner Authorization"],
    ["企业资源策略", identityBoundary.enterprise_resource_policy || "企业级资源继承大飞哥自建应用权限，并按公司范围和角色权限继续收紧"],
    ["用户资源策略", identityBoundary.user_resource_policy || "用户级资源只基于资源所有者本人授权；首次使用时按需引导授权"],
    ["权限原则", identityBoundary.digital_advisor_permission_policy || "只能收紧权限，不能突破飞书 App 或用户原始授权范围"],
    ["最近异常", gatewayRuntime.latest_error ? entrypointGatewayErrorSummary(gatewayRuntime.latest_error) : "无"],
    ["禁止链路", (boundary.forbidden || []).join("；") || "Agent Runtime 不直接调用 CLI；Agent Runtime 不直接调用 API；Sync Engine 不调用 MCP"],
    ["Tool 契约", (boundary.tool_contract || []).join("；") || "Tool 决定数据源/执行源；Tool 返回结构化结果；Agent Runtime 生成最终回复"],
  ].map(([label, detail]) => `
    <div class="architecture-row">
      <span>${escapeHtml(label)}</span>
      <strong>${escapeHtml(detail)}</strong>
    </div>
  `).join(""));
  setHtml("entrypointSourceBoard", (boundary.data_or_execution_sources || [
    "MCP -> CLI -> Feishu",
    "API -> Feishu",
    "PostgreSQL",
    "Vector DB",
    "External Search",
  ]).map((source) => `
    <div class="architecture-row">
      <span>数据源/执行源</span>
      <strong>${escapeHtml(source)}</strong>
    </div>
  `).join(""));
  renderReplyModeBoard("entrypointReplyModeBoard", state.replyModes?.items || defaultReplyModes(), { compact: true });
  renderTable("entrypointAppsTable", apps, [
    ["name", "应用"],
    ["app_id", "App ID"],
    ["cli_profile", "CLI Profile"],
    ["credential_status", "凭证状态"],
    ["credential_validated_at", "校验时间"],
    ["is_active", "启用"],
  ], { compactId: true, emptyMessage: "暂无 active 飞书 App" });
  renderTable("entrypointGatewayMessagesTable", recentGatewayMessages.map((item) => ({
    ...item,
    source_summary: gatewaySourceSummary(item),
    authorization_summary: gatewayAuthorizationSummary(item),
    chain_summary: Array.isArray(item.gateway_chain) ? item.gateway_chain.join(" -> ") : "",
  })), [
    ["created_at", "时间"],
    ["status", "状态"],
    ["actor", "用户"],
    ["chat_id", "会话"],
    ["message_id", "消息"],
    ["route_label", "路由"],
    ["reply_mode_label", "回复模式"],
    ["tool_summary", "Tool / 状态 / 来源"],
    ["source_summary", "数据源/执行源"],
    ["final_answer_owner", "最终回复"],
    ["authorization_summary", "授权"],
    ["agent_runtime_step_count", "步骤"],
  ], { compactId: true, emptyMessage: "暂无机器人消息链路；请在飞书向大飞哥发送测试消息" });
  renderTable("entrypointEventsTable", recentEvents, [
    ["occurred_at", "时间"],
    ["event_type", "事件"],
    ["thread_id", "会话"],
    ["title", "标题"],
    ["resource_id", "资源"],
    ["business_domain", "业务域"],
    ["data_layer", "数据层"],
    ["sync_action", "同步动作"],
    ["vector_status", "向量"],
  ], { compactId: true, emptyMessage: "暂无大飞哥事件，请先在飞书群里发送测试消息" });
}

function gatewaySourceSummary(item) {
  const steps = Array.isArray(item.agent_runtime_tool_steps) ? item.agent_runtime_tool_steps : [];
  const values = steps.map((step) => step.data_source || step.execution_source).filter(Boolean);
  return values.length ? Array.from(new Set(values)).join(" / ") : "-";
}

function gatewayAuthorizationSummary(item) {
  if (!item.user_identity_required) return "不需要用户授权";
  const resources = Array.isArray(item.required_user_identity_resources) ? item.required_user_identity_resources.join(", ") : "";
  const owner = item.authorization_owner_open_id ? `owner=${item.authorization_owner_open_id}` : "";
  const count = item.authorization_action_count != null ? `${item.authorization_action_count} 个入口` : "";
  return [resources, owner, count].filter(Boolean).join(" · ") || "需要用户授权";
}

function entrypointGatewayErrorSummary(error) {
  return [
    error.route_label || error.route_path,
    error.reply_mode_label,
    error.tool_summary || error.workflow_summary,
    error.error ? `错误=${error.error}` : "",
    error.final_answer_owner ? `最终回复=${error.final_answer_owner}` : "",
  ].filter(Boolean).join(" · ") || "有异常，暂无摘要";
}

function entrypointDetail(item) {
  const hint = item.feishu_app_bootstrap_hint || item.bootstrap_hint || {};
  const parts = [item.detail, item.required_action || item.next_action].filter(Boolean);
  if (hint.inferred_from_cli && hint.app_id) {
    parts.push(`CLI 已识别 App ID：${hint.app_id}`);
  }
  if (hint.secret_required) {
    parts.push("仍需 App Secret 入库并校验 tenant token");
  }
  return parts.join(" · ");
}

function renderCompanySpace(os, companyOverview, syncStatus, events, cockpit, companyId) {
  const companies = os?.companies || [];
  const company = companies.find((item) => item.id === companyId) || companies[0] || null;
  const counts = company?.counts || {};
  const overviewItem = (companyOverview?.items || []).find((item) => item.company_id === companyId)
    || (companyOverview?.items || [])[0]
    || {};
  const syncItems = syncStatus?.items || [];
  const eventItems = events?.items || [];
  const readiness = os?.release_readiness || {};
  const readinessChecks = Object.fromEntries((readiness.checks || []).map((item) => [item.key, item]));
  const moduleByKey = {};
  for (const item of cockpit?.modules || []) moduleByKey[item.key] = item;

  setText("companySpaceTitle", company ? `${company.name}经营空间` : "公司经营空间待初始化");
  setText(
    "companySpaceSubtitle",
    company
      ? `${company.code} · 飞书 App、资源覆盖、WorkEvent 和员工智能体统一查看`
      : "请先在系统配置中创建公司并绑定飞书 App",
  );
  setHtml("companySpaceMetrics", [
    ["公司状态", company?.status || "待配置"],
    ["飞书资源", overviewItem.resources ?? counts.resources ?? 0],
    ["WorkEvent", counts.work_events ?? eventItems.length],
    ["员工智能体", counts.users ?? 0],
    ["同步异常", overviewItem.failed_resource_sync_runs ?? syncStatus?.counts?.failed ?? 0],
    ["上线状态", readiness.status === "ready" ? "Ready" : "Degraded"],
  ].map(([label, value]) => `
    <div class="command-pill">
      <span>${escapeHtml(label)}</span>
      <strong>${escapeHtml(value)}</strong>
    </div>
  `).join(""));

  const layerCounts = companySpaceLayerCounts(syncItems);
  const dataLayers = [
    ["Operational Data", "审批、项目、客户、日历、群消息", `${layerCounts.operational} 个资源 · ${counts.work_events ?? eventItems.length} 条 WorkEvent`],
    ["Knowledge Data", "Doc、Wiki、制度、会议纪要", `${layerCounts.knowledge} 个资源 · L1/L2/L3 策略已接入`],
    ["Memory", "用户偏好、长期上下文、Agent 记忆", `${layerCounts.memory} 个资源 · ${counts.users ?? 0} 个员工智能体入口`],
    ["WorkEvent", "统一经营事件流", `${eventItems.length} 条最近事件 · 向量状态可追踪`],
  ];
  setHtml("companySpaceDataLayers", dataLayers.map(([label, scope, status]) => `
    <div class="architecture-row">
      <span>${escapeHtml(label)}</span>
      <strong>${escapeHtml(scope)}</strong>
      <small>${escapeHtml(status)}</small>
    </div>
  `).join(""));

  const botCheck = readinessChecks.feishu_bot || {};
  const appCheck = readinessChecks.feishu_app_config || {};
  const cliCheck = readinessChecks.feishu_cli || {};
  setHtml("companySpaceAgentState", [
    ["大飞哥", botCheck.detail || "等待入口检查", os?.entrypoints?.feishu_bot?.execution_boundary || "Tool Router 边界待确认"],
    [
      "飞书 App",
      appCheck.detail || "等待 App 配置",
      [company ? `${company.name} 独立 App / 独立 CLI Profile` : "待配置", appCheck.required_action].filter(Boolean).join(" · "),
    ],
    ["飞书 CLI", cliCheck.status === "ready" ? "CLI 可用" : "CLI 待处理", cliCheck.detail || "等待 CLI 自检"],
    ["Agent Runtime", os?.entrypoints?.feishu_bot?.agent_runtime_fallback ? "自然语言兜底已开" : "自然语言兜底未开", "Agent Runtime 只通过 Tool Router 调用工具"],
  ].map(([label, status, detail]) => `
    <div class="architecture-row">
      <span>${escapeHtml(label)}</span>
      <strong>${escapeHtml(status)}</strong>
      <small>${escapeHtml(detail)}</small>
    </div>
  `).join(""));

  const actions = syncStatus?.governance_actions?.length
    ? syncStatus.governance_actions.slice(0, 6).map((item) => [
        item.title || item.resource_name || "治理动作",
        item.owner_next_step_detail || item.next_action || item.reason || "请检查资源覆盖",
      ])
    : [
        ["资源发现", overviewItem.resources ? "已登记资源，继续检查同步状态" : "还没有登记资源，下一步需要跑飞书资源发现"],
        ["经营数据", eventItems.length ? "已有 WorkEvent，可进入经营模块查看" : "WorkEvent 较少，需同步审批、群消息、日历、会议等数据"],
        ["今日判断", moduleByKey["today-focus"]?.summary || "真实数据增加后会生成今日重点"],
      ];
  setHtml("companySpaceSyncActions", actions.map(([label, detail]) => `
    <div class="brief-row">
      <span>${escapeHtml(label)}</span>
      <strong>${escapeHtml(detail)}</strong>
    </div>
  `).join(""));

  renderTable("companySpaceEventsTable", eventItems, [
    ["title", "事件"],
    ["event_type", "类型"],
    ["source", "来源"],
    ["sensitivity", "敏感度"],
    ["vector_status", "向量"],
    ["occurred_at", "时间"],
  ], { compactId: true, emptyMessage: "当前公司暂无 WorkEvent，请先同步飞书资源" });
  renderTable("companySpaceResourcesTable", syncItems, [
    ["resource_name", "资源"],
    ["resource_type", "类型"],
    ["data_layer", "数据层"],
    ["status", "状态"],
    ["sync_action", "同步动作"],
    ["query_path", "查询路径"],
    ["work_event_count", "事件"],
    ["next_action", "建议"],
  ], { rowAction: selectResource, compactId: true, emptyMessage: "当前公司暂无资源，请先执行资源发现" });
}

function companySpaceLayerCounts(items) {
  const counts = { operational: 0, knowledge: 0, memory: 0 };
  for (const item of items || []) {
    const value = String(item.data_layer || item.data_type || item.resource_type || "").toLowerCase();
    if (value.includes("knowledge") || ["doc", "docs", "document", "wiki", "drive"].some((term) => value.includes(term))) {
      counts.knowledge += 1;
    } else if (value.includes("memory")) {
      counts.memory += 1;
    } else {
      counts.operational += 1;
    }
  }
  return counts;
}

async function loadTodayFocus() {
  const data = await loadCockpitModule("today-focus");
  renderChips("todayFocusStats", data?.metrics || {});
  renderTable("todayFocusTable", data?.items || [], [
    ["attention_label", "分层"],
    ["title", "事项"],
    ["subtitle", "影响"],
    ["status", "状态"],
    ["priority", "优先级"],
    ["occurred_at", "时间"],
    ["description", "处理建议"],
  ], { emptyMessage: "暂无必须老板立即处理的事项" });
  showResult("todayFocusResult", {
    summary: data?.summary,
    next_actions: data?.next_actions || [],
  });
}

function openAiModal() {
  const modal = document.getElementById("aiModal");
  modal.classList.add("open");
  modal.setAttribute("aria-hidden", "false");
  if (!value("advisorQuestion")) {
    setValue("advisorQuestion", "今天有什么需要我关注的经营风险？");
  }
  document.getElementById("advisorQuestion").focus();
}

function closeAiModal() {
  const modal = document.getElementById("aiModal");
  modal.classList.remove("open");
  modal.setAttribute("aria-hidden", "true");
}

function showSettingsTab(name) {
  state.activeSettingsTab = name || "resources";
  document.querySelectorAll("[data-settings-tab]").forEach((button) => {
    button.classList.toggle("active", button.dataset.settingsTab === state.activeSettingsTab);
  });
  document.querySelectorAll("[data-settings-section]").forEach((section) => {
    section.classList.toggle("settings-section-active", section.dataset.settingsSection === state.activeSettingsTab);
  });
  if (state.activeSettingsTab === "tools") loadToolConfigurations();
  if (state.activeSettingsTab === "knowledge") loadKnowledge();
}

function applyConsoleUrlParams() {
  const params = new URLSearchParams(window.location.search);
  if (params.get("auth") !== "feishu_cli") return;
  const companyId = params.get("company_id") || "";
  const openId = params.get("open_id") || "";
  if (companyId) {
    state.selectedCompanyId = companyId;
    setValue("companySelect", companyId);
  }
  if (openId) {
    setValue("agentPreviewOpenId", openId);
    setValue("adminOpenId", openId);
  }
  showView("settings");
  showSettingsTab("resources");
  renderFeishuCliAuthBoard({
    status: "ready_to_start",
    cli_profile: "按当前公司飞书 AppConfig",
    instruction: "这是本地 CLI 调试入口。员工正式授权请从大飞哥机器人卡片进入飞书 OAuth。",
  });
}

async function loadCompanies(options = {}) {
  const items = await safeLoad("/api/companies");
  if (!items) return;
  state.companies = items;
  renderCompanySelect(items);
  renderTable("companiesTable", items, [
    ["name", "公司"],
    ["code", "代码"],
    ["id", "公司 ID"],
    ["created_at", "创建时间"],
  ]);
  if (!options.silent && state.activeView === "overview") await loadOverview();
}

async function loadKnowledge() {
  const [strategy, syncStatus, events] = await Promise.all([
    safeLoad("/api/v5/resources/sync-strategy"),
    safeLoad(`/api/v5/resources/sync-status${companyQuery()}`),
    safeLoad(`/api/v5/workspace/events${companyQuery("limit=80")}`),
  ]);
  const knowledgePolicies = (strategy?.query_policies || []).filter((item) => item.data_type === "knowledge_data");
  const knowledgeLayers = (strategy?.layers || []).filter((item) => String(item.key || "").startsWith("knowledge_"));
  const knowledgeResources = (syncStatus?.items || []).filter(isKnowledgeResource);
  const knowledgeEvents = (events?.items || []).filter(isKnowledgeEvent).slice(0, 20);
  const vectorCounts = countBy(knowledgeEvents, "vector_status");
  const layerCounts = countBy(knowledgeResources, "data_layer");

  renderChips("knowledgeStats", {
    resources: knowledgeResources.length,
    l1_hot: knowledgeResources.filter((item) => isKnowledgeLevel(item, "L1")).length,
    l2_cold: knowledgeResources.filter((item) => isKnowledgeLevel(item, "L2")).length,
    l3_external: knowledgeResources.filter((item) => isKnowledgeLevel(item, "L3")).length,
    events: knowledgeEvents.length,
    vector_pending: vectorCounts.pending || 0,
    vector_indexed: vectorCounts.indexed || 0,
  });
  renderKnowledgePolicyBoard(knowledgePolicies, knowledgeLayers);
  renderTable("knowledgeResourcesTable", knowledgeResources.map((item) => ({
    ...item,
    knowledge_level: knowledgeResourceLevel(item),
    latest_rag: item.latest_run?.rag_indexing_summary,
    latest_document_store: item.latest_run?.document_store_summary,
  })), [
    ["resource_name", "资源"],
    ["resource_type", "类型"],
    ["knowledge_level", "分层"],
    ["data_layer", "数据层"],
    ["sync_action", "同步动作"],
    ["query_path", "查询路径"],
    ["status", "状态"],
    ["latest_document_store", "文档库"],
    ["latest_rag", "RAG"],
    ["next_action", "建议"],
  ], { rowAction: selectResource, compactId: true, emptyMessage: "暂无知识资源，请先发现 Doc/Wiki/云文档资源" });
  renderTable("knowledgeEventsTable", knowledgeEvents, [
    ["title", "知识事件"],
    ["event_type", "类型"],
    ["source", "来源"],
    ["resource_id", "资源"],
    ["vector_status", "向量"],
    ["occurred_at", "时间"],
  ], { compactId: true, emptyMessage: "暂无知识 WorkEvent" });
  showResult("knowledgeResult", {
    principle: "L1 热知识同步到 Document Store + Vector DB；L2 冷知识只登记 token 并通过飞书 CLI 实时查询；L3 外部知识默认不入库。",
    storage_boundary: "Knowledge Data 与 Operational Data、Memory 分开；Memory 由 Agent Runtime 提炼，不保存原始知识全文。",
    resource_layers: layerCounts,
    vector_status: vectorCounts,
    query_policies: knowledgePolicies.map((item) => ({
      level: item.level,
      query_path: item.primary_query_path,
      persistence_rule: item.persistence_rule,
    })),
  });
}

async function discoverKnowledgeResources() {
  const appConfigId = await ensureDefaultAppConfigId("knowledgeResult");
  if (!appConfigId) return;
  const result = await safeAction("knowledgeResult", async () => api(`/api/feishu/apps/${appConfigId}/resources/discover`, {
    method: "POST",
    body: JSON.stringify({
      kinds: ["drive", "wiki"],
      async_run: false,
      limit: 50,
      include_local_mining: true,
    }),
  }));
  if (result?.items) renderDiscoveredItems(result.items);
  await loadResources();
  await loadKnowledge();
}

async function previewKnowledgeSync() {
  if (!state.selectedCompanyId) {
    showResult("knowledgeResult", "请先选择公司，再预览知识同步。");
    return;
  }
  const result = await safeAction("knowledgeResult", async () => api("/api/v5/resources/sync-preview", {
    method: "POST",
    body: JSON.stringify({
      ...batchSyncPayload(),
      resource_type: null,
      statuses: ["never_synced", "stale", "manual_review_required"],
      limit_resources: 50,
      large_document_mode: "index_only",
      vector_mode: "summaries_and_hot_knowledge",
    }),
  }));
  const knowledgeItems = (result?.items || []).filter(isKnowledgeResource);
  renderTable("knowledgeSyncPreviewTable", knowledgeItems, [
    ["resource_name", "资源"],
    ["resource_type", "类型"],
    ["data_layer", "数据层"],
    ["sync_action", "同步动作"],
    ["query_path", "查询路径"],
    ["decision_status", "决策"],
    ["executable", "可执行"],
    ["next_action", "下一步"],
  ], { rowAction: selectResource, compactId: true, emptyMessage: "当前没有待预览的知识同步资源" });
  showResult("knowledgeResult", {
    previewed: knowledgeItems.length,
    principle: "预览只展示知识资源；L2 冷知识默认只登记目录、摘要和 token，不拉取全文。",
  });
}

function renderKnowledgePolicyBoard(policies, layers) {
  const rows = policies.map((policy) => {
    const layer = layers.find((item) => item.key === (
      policy.level === "L1" ? "knowledge_hot" : policy.level === "L2" ? "knowledge_cold" : "knowledge_external_web"
    )) || {};
    return `
      <div class="architecture-row">
        <span>${escapeHtml(`${policy.level || "-"} ${policy.key || ""}`)}</span>
        <strong>${escapeHtml(localizeCellValue(policy.primary_query_path || layer.query_path || "-"))}</strong>
        <small>${escapeHtml(policy.xmind_rule || layer.retention || "")}</small>
      </div>
    `;
  });
  setHtml("knowledgePolicyBoard", rows.join("") || `<div class="empty">暂无知识分层策略</div>`);
}

function isKnowledgeResource(item) {
  const text = [
    item.resource_type,
    item.data_type,
    item.data_layer,
    item.business_domain,
    item.storage_layer,
    item.sync_action,
    item.query_path,
  ].map((value) => String(value || "").toLowerCase()).join(" ");
  return text.includes("knowledge")
    || ["doc", "wiki", "drive_file", "web", "external_web"].some((value) => text.includes(value));
}

function isKnowledgeEvent(item) {
  const text = [
    item.event_type,
    item.source,
    item.title,
    item.external_id,
  ].map((value) => String(value || "").toLowerCase()).join(" ");
  return text.includes("knowledge")
    || text.includes("doc")
    || text.includes("wiki")
    || text.includes("document")
    || item.vector_status === "pending"
    || item.vector_status === "indexed";
}

function knowledgeResourceLevel(item) {
  if (isKnowledgeLevel(item, "L1")) return "L1 热知识";
  if (isKnowledgeLevel(item, "L2")) return "L2 冷知识";
  if (isKnowledgeLevel(item, "L3")) return "L3 外部知识";
  return "待分层";
}

function isKnowledgeLevel(item, level) {
  const dataLayer = String(item.data_layer || "").toLowerCase();
  const syncAction = String(item.sync_action || "").toLowerCase();
  const queryPath = String(item.query_path || "").toLowerCase();
  const resourceType = String(item.resource_type || "").toLowerCase();
  if (level === "L1") return dataLayer === "knowledge_hot" || syncAction === "knowledge_vectorize" || queryPath === "local_rag";
  if (level === "L2") return dataLayer === "knowledge_cold" || syncAction === "document_index_only" || queryPath === "lark_cli_realtime";
  if (level === "L3") return dataLayer === "knowledge_external_web" || resourceType === "web" || queryPath === "external_search_realtime";
  return false;
}

async function loadAdministration() {
  await loadCompanies({ silent: true });
  await loadAdministrationFoundation();
  await loadBotUsers();
  await loadSystemLogs();
  await loadAudit();
  await loadToolExecutions();
}

async function loadReportsView() {
  await loadItems();
}

async function loadSettings() {
  showSettingsTab(state.activeSettingsTab);
  await loadAgentReplyModes({ silent: true });
  await loadResources();
  await loadKnowledge();
  await loadAdministration();
  await loadToolConfigurations();
  await loadAgentSettings();
  await loadAgentTraces();
  showSettingsTab(state.activeSettingsTab);
}

async function loadRisks() {
  const data = await loadCockpitModule("risks");
  const items = data?.items || [];
  renderRiskStats(items);
  renderTable("risksTable", items, [
    ["title", "风险"],
    ["subtitle", "影响"],
    ["owner", "建议责任人"],
    ["description", "处理建议"],
    ["priority", "优先级"],
    ["status", "状态"],
    ["created_at", "发现时间"],
  ]);
  showResult("riskCenterResult", moduleSummary(data));
}

async function loadTasks() {
  const data = await loadCockpitModule("tasks");
  const items = data?.items || [];
  renderChips("taskStats", data?.metrics || {});
  renderTable("tasksTable", items, [
    ["attention_label", "分层"],
    ["title", "待办"],
    ["subtitle", "影响"],
    ["owner", "建议责任人"],
    ["status", "状态"],
    ["priority", "优先级"],
    ["due_at", "截止时间"],
    ["description", "处理建议"],
  ]);
  showResult("taskCenterResult", moduleSummary(data));
}

async function loadApprovals() {
  const data = await loadCockpitModule("approvals");
  const items = data?.items || [];
  renderChips("approvalStats", data?.metrics || {});
  renderTable("approvalsTable", items, [
    ["attention_label", "分层"],
    ["title", "审批"],
    ["subtitle", "单据"],
    ["status", "状态"],
    ["priority", "优先级"],
    ["occurred_at", "时间"],
    ["description", "说明"],
  ]);
  showResult("approvalCenterResult", moduleSummary(data));
}

async function loadProjects() {
  const data = await loadCockpitModule("projects");
  const items = data?.items || [];
  renderChips("projectStats", data?.metrics || {});
  renderTable("projectsTable", items, [
    ["attention_label", "分层"],
    ["title", "项目动态"],
    ["subtitle", "影响"],
    ["status", "状态"],
    ["priority", "优先级"],
    ["occurred_at", "时间"],
    ["description", "处理建议"],
  ]);
  showResult("projectCenterResult", moduleSummary(data));
}

async function loadDecisions() {
  const data = await loadCockpitModule("decisions");
  const items = data?.items || [];
  renderChips("decisionStats", data?.metrics || {});
  renderTable("decisionsTable", items, [
    ["attention_label", "分层"],
    ["title", "决策"],
    ["subtitle", "影响"],
    ["owner", "建议责任人"],
    ["status", "状态"],
    ["priority", "优先级"],
    ["due_at", "截止时间"],
    ["description", "处理建议"],
  ]);
  showResult("decisionCenterResult", moduleSummary(data));
}

async function loadCommunications() {
  const data = await loadCockpitModule("communications");
  const items = data?.items || [];
  renderChips("communicationStats", data?.metrics || {});
  renderTable("communicationsTable", items, [
    ["attention_label", "分层"],
    ["title", "沟通线索"],
    ["subtitle", "影响"],
    ["source", "来源"],
    ["status", "状态"],
    ["occurred_at", "时间"],
    ["description", "处理建议"],
  ]);
  showResult("communicationCenterResult", moduleSummary(data));
}

async function loadMeetings() {
  const data = await loadCockpitModule("meetings");
  const items = data?.items || [];
  renderChips("meetingStats", data?.metrics || {});
  renderTable("meetingsTable", items, [
    ["attention_label", "分层"],
    ["title", "会议日程"],
    ["subtitle", "影响"],
    ["source", "来源"],
    ["status", "状态"],
    ["occurred_at", "时间"],
    ["description", "处理建议"],
  ]);
  showResult("meetingCenterResult", moduleSummary(data));
}

async function loadCockpitModule(key) {
  return safeLoad(`/api/cockpit/modules/${key}${companyQuery("limit=100")}`);
}

async function loadExtractedItems(itemType) {
  const params = new URLSearchParams();
  if (state.selectedCompanyId) params.set("company_id", state.selectedCompanyId);
  if (itemType) params.set("item_type", itemType);
  params.set("limit", "100");
  const data = await safeLoad(`/api/extracted-items?${params.toString()}`);
  return data?.items || [];
}

async function loadFilteredWorkEvents(keywords) {
  const params = new URLSearchParams();
  if (state.selectedCompanyId) params.set("company_id", state.selectedCompanyId);
  params.set("limit", "100");
  const data = await safeLoad(`/api/work-events?${params.toString()}`);
  const events = data || [];
  return events.filter((event) => containsAny([
    event.event_type,
    event.title,
    event.content_text,
    ...(event.labels || []),
  ].join(" "), keywords)).slice(0, 50);
}

async function loadResources() {
  const company = companyQuery();
  const [resources, companyOverview] = await Promise.all([
    safeLoad(`/api/v5/resources${company}`),
    safeLoad(`/api/v5/resources/company-overview${company}`),
    loadSyncPolicy(),
    loadResourceSyncStatus(),
    loadResourceMonitoring(),
    loadSyncStrategy(),
  ]);
  fillDiscoveryDefaults();
  const items = resources?.items || [];
  const rows = items.map((item) => ({
    ...item,
    governance_status: item.config_json?.governance?.status,
    governance_reason: item.config_json?.governance?.reason,
  }));
  renderCompanyResourceOverview(companyOverview?.items || []);
  renderResourceStats(items);
  renderTable("resourcesTable", rows, [
    ["id", "OS ID"],
    ["platform", "平台"],
    ["resource_type", "类型"],
    ["data_layer", "数据层"],
    ["resource_name", "名称"],
    ["resource_id", "主 ID"],
    ["resource_sub_id", "子 ID"],
    ["sync_mode", "同步"],
    ["permission_level", "权限级别"],
    ["governance_status", "治理状态"],
    ["governance_reason", "治理原因"],
    ["enabled", "启用"],
  ], { rowAction: selectResource, compactId: true });
  await loadResourceLaunchPlan();
}

function renderCompanyResourceOverview(items) {
  renderTable("companyResourceOverviewTable", items, [
    ["company_name", "公司"],
    ["company_code", "代码"],
    ["resources", "资源数"],
    ["enabled_resources", "启用"],
    ["failed_resource_sync_runs", "异常同步"],
    ["latest_sync_action", "最近动作"],
    ["latest_sync_status", "最近状态"],
    ["latest_sync_at", "最近同步"],
    ["resource_type_counts", "资源类型"],
    ["sync_action_counts", "动作分布"],
  ], { compactId: true });
}

async function ensureLaunchCompany(target = "resourceLaunchResult") {
  if (state.selectedCompanyId) return state.selectedCompanyId;
  if (!state.companies.length) {
    await loadCompanies({ silent: true });
  }
  if (!state.selectedCompanyId && state.companies.length === 1) {
    state.selectedCompanyId = state.companies[0].id;
    const select = document.getElementById("companySelect");
    if (select) select.value = state.selectedCompanyId;
  }
  if (!state.selectedCompanyId) {
    showResult(target, "请先选择公司，再执行上线编排。");
    return "";
  }
  return state.selectedCompanyId;
}

async function loadResourceLaunchPlan() {
  const companyId = await ensureLaunchCompany("resourceLaunchResult");
  if (!companyId) {
    renderChips("resourceLaunchStats", {});
    renderTable("resourceLaunchPreviewTable", [], [], { emptyMessage: "请选择公司后查看待同步资源" });
    setHtml("resourceLaunchSteps", `<div class="empty">请选择公司后生成上线编排</div>`);
    return;
  }
  const query = companyQuery();
  const limitQuery = companyQuery("limit=50");
  const [os, syncStatus, monitoring, companyOverview, events] = await Promise.all([
    safeLoad("/api/v5/os/overview"),
    safeLoad(`/api/v5/resources/sync-status${query}`),
    safeLoad(`/api/v5/resources/monitoring${query}`),
    safeLoad(`/api/v5/resources/company-overview${query}`),
    safeLoad(`/api/v5/workspace/events${limitQuery}`),
  ]);
  const company = (os?.companies || []).find((item) => item.id === companyId) || null;
  const overview = (companyOverview?.items || []).find((item) => item.company_id === companyId) || {};
  const resources = syncStatus?.items || [];
  const pending = resources.filter((item) => {
    const status = String(item.status || "");
    return item.sync_supported !== false && ["never_synced", "stale"].includes(status);
  });
  const failed = resources.filter((item) => ["failed", "blocked", "access_blocked"].includes(String(item.status || "")));
  const governanceActions = syncStatus?.governance_actions || monitoring?.governance_actions || [];
  const eventsCount = events?.items?.length || company?.counts?.work_events || 0;
  renderChips("resourceLaunchStats", {
    公司: company ? `${company.name} (${company.code})` : companyId,
    资源: overview.resources ?? resources.length,
    待同步: pending.length,
    同步异常: failed.length,
    治理动作: governanceActions.length,
    WorkEvent: eventsCount,
  });
  renderResourceLaunchSteps({ company, resources, pending, failed, governanceActions, eventsCount });
  renderTable("resourceLaunchPreviewTable", pending.slice(0, 12), [
    ["resource_name", "待处理资源"],
    ["resource_type", "类型"],
    ["data_layer", "数据层"],
    ["status", "状态"],
    ["sync_action", "同步动作"],
    ["query_path", "查询路径"],
    ["next_action", "下一步"],
  ], { rowAction: selectResource, compactId: true, emptyMessage: "当前没有待同步资源" });
  showResult("resourceLaunchResult", {
    status: failed.length ? "needs_attention" : "ready",
    next_step: pending.length ? "预览同步后执行同步待处理资源" : "继续扩大飞书资源发现范围",
    governance_actions: governanceActions.slice(0, 5),
  });
}

function renderResourceLaunchSteps({ company, resources, pending, failed, governanceActions, eventsCount }) {
  const rows = [
    [
      "1. 公司与 App",
      company ? "已选择公司" : "待选择公司",
      company ? `${company.name} 使用独立飞书 App / CLI Profile` : "先选择要上线的公司",
    ],
    [
      "2. 本地资源发现",
      resources.length ? `已登记 ${resources.length} 个资源` : "待发现",
      "从已入库 WorkEvent 挖掘群聊、文档、多维表等资源，不直接触发外部全量扫描",
    ],
    [
      "3. 同步预览",
      pending.length ? `${pending.length} 个资源待同步` : "暂无待同步资源",
      "预览 Sync Engine 将执行的 API 同步范围和数据层写入路径",
    ],
    [
      "4. 同步入库",
      failed.length ? `${failed.length} 个异常需处理` : "同步状态正常",
      "API Client -> Feishu -> PostgreSQL，实时问答不走同步链路",
    ],
    [
      "5. 经营事件",
      eventsCount ? `${eventsCount} 条 WorkEvent` : "等待 WorkEvent",
      "Operational Data 抽象为 WorkEvent，供经营中心、报告和 Agent 记忆使用",
    ],
    [
      "6. 治理动作",
      governanceActions.length ? `${governanceActions.length} 项待处理` : "暂无关键治理动作",
      "处理机器人未入群、授权不足、资源标识缺失和数据覆盖策略",
    ],
  ];
  setHtml("resourceLaunchSteps", rows.map(([label, status, detail]) => `
    <div class="architecture-row">
      <span>${escapeHtml(label)}</span>
      <strong>${escapeHtml(status)}</strong>
      <small>${escapeHtml(detail)}</small>
    </div>
  `).join(""));
}

async function discoverLaunchLocalResources() {
  if (!(await ensureLaunchCompany("resourceLaunchResult"))) return;
  const appConfigId = await ensureDefaultAppConfigId("resourceLaunchResult");
  if (!appConfigId) return;
  const result = await safeAction("resourceLaunchResult", async () => api(`/api/feishu/apps/${appConfigId}/resources/discover`, {
    method: "POST",
    body: JSON.stringify({
      kinds: ["local"],
      async_run: false,
      limit: 50,
      include_local_mining: true,
    }),
  }));
  if (result?.items) renderDiscoveredItems(result.items);
  await loadResources();
  if (result) showResult("resourceLaunchResult", result);
}

async function previewLaunchSync() {
  if (!(await ensureLaunchCompany("resourceLaunchResult"))) return;
  const result = await safeAction("resourceLaunchResult", async () => api("/api/v5/resources/sync-preview", {
    method: "POST",
    body: JSON.stringify(batchSyncPayload()),
  }));
  renderTable("resourceLaunchPreviewTable", result?.items || [], [
    ["resource_name", "资源"],
    ["resource_type", "类型"],
    ["data_layer", "数据层"],
    ["sync_action", "同步动作"],
    ["query_path", "查询路径"],
    ["decision_status", "决策"],
    ["executable", "可执行"],
    ["next_action", "下一步"],
  ], { rowAction: selectResource, compactId: true, emptyMessage: "当前没有可预览的同步资源" });
}

async function syncLaunchPending() {
  if (!(await ensureLaunchCompany("resourceLaunchResult"))) return;
  const result = await safeAction("resourceLaunchResult", async () => api("/api/v5/resources/sync", {
    method: "POST",
    body: JSON.stringify(batchSyncPayload()),
  }));
  await loadResources();
  if (result) showResult("resourceLaunchResult", result);
}

async function loadResourceSyncStatus() {
  const data = await safeLoad(`/api/v5/resources/sync-status${companyQuery()}`);
  const items = (data?.items || []).map((item) => ({
    ...item,
    latest_run_status: item.latest_run?.status,
    latest_run_error: item.latest_run?.error_message,
    latest_items_seen: item.latest_run?.items_seen,
    latest_items_indexed: item.latest_run?.items_indexed,
    latest_items_skipped: item.latest_run?.items_skipped,
    latest_document_store_summary: item.latest_run?.document_store_summary,
    latest_rag_indexing_summary: item.latest_run?.rag_indexing_summary,
    authorization_access_mode: item.authorization?.access_mode,
    authorization_required_identifiers: item.authorization?.required_identifiers,
  }));
  renderChips("syncStatusStats", data?.counts || {});
  renderGovernanceActions(data?.governance_actions || []);
  renderTable("resourceSyncStatusTable", items, [
    ["resource_name", "资源"],
    ["resource_type", "类型"],
    ["sync_action", "同步动作"],
    ["query_path", "查询路径"],
    ["authorization_access_mode", "授权方式"],
    ["authorization_required_identifiers", "必需标识"],
    ["status", "状态"],
    ["latest_run_status", "最近运行"],
    ["latest_run_error", "最近错误"],
    ["decision_status", "决策"],
    ["sync_supported", "可同步"],
    ["last_sync_at", "上次同步"],
    ["last_sync_age_hours", "距今小时"],
    ["latest_items_seen", "看到"],
    ["latest_items_indexed", "写入"],
    ["latest_items_skipped", "跳过"],
    ["latest_document_store_summary", "最近文档库"],
    ["latest_rag_indexing_summary", "最近 RAG"],
    ["work_event_count", "事件数"],
    ["decision_reason", "策略原因"],
    ["next_action", "建议"],
  ], { rowAction: selectResource, compactId: true });
  showResult("resourceSyncResult", {
    governance_actions: data?.governance_actions || [],
    latest_runs: data?.latest_runs || [],
  });
}

function renderGovernanceActions(actions) {
  const rows = (actions || []).map((item) => ({
    ...item,
    affected_resources: item.affected_resources || (item.resources || [])
      .map((resource) => resource.resource_name || resource.resource_id)
      .filter(Boolean)
      .slice(0, 5)
      .join("、"),
  }));
  const el = document.getElementById("resourceGovernanceActionsTable");
  if (!rows.length) {
    el.innerHTML = `<div class="empty">暂无需要处理的数据盲区动作</div>`;
    return;
  }
  el.innerHTML = `
    <table>
      <thead>
        <tr>
          <th>处理</th>
          <th>处理动作</th>
          <th>数量</th>
          <th>接入建议</th>
          <th>通知对象</th>
          <th>影响范围</th>
          <th>建议责任人</th>
          <th>业务影响</th>
          <th>下一步</th>
          <th>系统行为</th>
          <th>关联资源</th>
        </tr>
      </thead>
      <tbody>
        ${rows.map((item, index) => `
          <tr>
            <td><button class="ghost compact-button" data-governance-index="${index}">处理</button></td>
            <td>${formatCell(item.title)}</td>
            <td>${formatCell(item.count)}</td>
            <td>${formatCell(item.access_recommendation_summary)}</td>
            <td>${formatCell((item.recommended_notify_targets || []).join(" / "))}</td>
            <td>${formatCell(item.business_domain)}</td>
            <td>${formatCell(item.responsible_role)}</td>
            <td>${formatCell(item.business_impact)}</td>
            <td>${formatCell(item.owner_next_step_detail)}</td>
            <td>${formatCell(item.system_behavior)}</td>
            <td>${formatCell(item.affected_resources)}</td>
          </tr>
        `).join("")}
      </tbody>
    </table>
  `;
  el.querySelectorAll("[data-governance-index]").forEach((button) => {
    button.addEventListener("click", () => selectGovernanceAction(rows[Number(button.dataset.governanceIndex)]));
  });
}

function selectGovernanceAction(action) {
  const resource = (action.resources || [])[0];
  const resourceId = resource?.resource_id;
  if (resourceId) {
    state.selectedResourceId = resourceId;
    state.selectedResourceLabel = `${resource.resource_type || "resource"} / ${resource.resource_name || resource.resource_id}`;
    const label = document.getElementById("selectedResourceLabel");
    if (label) label.textContent = `已选择：${state.selectedResourceLabel}`;
  }
  applyGovernanceActionPreset(action);
  showResult("governanceActionGuide", governanceActionGuide(action));
  showSettingsTab("resources");
}

function applyGovernanceActionPreset(action) {
  const code = action.action_code || "";
  if (code === "invite_bot_to_chats") {
    setValue("batchSyncResourceType", "chat");
    setValue("batchSyncStatuses", "");
  } else if (code === "complete_user_authorization") {
    setValue("batchSyncResourceType", "calendar");
    setValue("batchSyncStatuses", "");
  } else if (code === "complete_resource_identifiers") {
    setDiscoverPreset("local,bitable,approvals", true);
  } else if (code === "review_data_coverage_policy") {
    setValue("batchSyncStatuses", "");
  } else if (code === "wait_or_review_data_refresh") {
    setValue("batchSyncStatuses", "stale");
  }
}

function governanceActionGuide(action) {
  const code = action.action_code || "";
  const resources = (action.resources || [])
    .map((resource) => resource.resource_name || resource.resource_id)
    .filter(Boolean)
    .slice(0, 8);
  const names = resources.length ? resources.join("、") : "暂无关联资源";
  const common = [
    `处理动作：${action.title || "-"}`,
    `影响范围：${action.business_domain || "-"}`,
    `建议责任人：${action.responsible_role || "-"}`,
    `接入建议：${action.access_recommendation_summary || "需要人工确认"}`,
    `建议通知对象：${(action.recommended_notify_targets || []).join(" / ") || "业务负责人先确认"}`,
    `关联资源：${names}`,
  ];
  const stepsByCode = {
    invite_bot_to_chats: [
      "操作步骤：",
      "1. 先查看接入建议，只有“建议接入”的业务群才通知负责人。",
      "2. 由业务负责人确认后，再请群主把数字助理机器人加入群。",
      "3. 不建议接入或暂不处理的群，不通知群主。",
      "4. 机器人加入后回到这里点击“重试并恢复”。",
      "5. 同步成功后系统会自动清除访问阻断，后续进入自动同步。",
    ],
    complete_user_authorization: [
      "操作步骤：",
      "1. 完成飞书日历/会议相关授权；系统会自动发现可同步资源。",
      "2. 授权完成后点击“重试并恢复”。",
      "3. 同步成功后系统会自动清除访问阻断；失败时继续保留阻断原因。",
    ],
    complete_resource_identifiers: [
      "操作步骤：",
      "1. 在“发现”区域执行自动发现，系统会登记可同步资源并标出缺权限项。",
      "2. 点击“开始发现并登记”。",
      "3. 回到“同步”区域预览自动同步队列。",
    ],
    review_data_coverage_policy: [
      "操作步骤：",
      "1. 判断这些资源是否对经营分析有价值。",
      "2. 有价值则把对应资源类型加入自动同步策略。",
      "3. 无价值则保持排除，避免无效数据进入系统。",
    ],
    wait_or_review_data_refresh: [
      "操作步骤：",
      "1. 先等待下一轮自动同步重试。",
      "2. 如果连续失败，再查看下方同步状态表的最近错误。",
      "3. 必要时选中资源后手动同步一次。",
    ],
  };
  return [...common, "", ...(stepsByCode[code] || [action.owner_next_step_detail || action.owner_next_step || "-"])].join("\n");
}

async function loadSyncPolicy() {
  if (!state.selectedCompanyId) {
    showResult("syncPolicyResult", "请先选择公司。");
    return;
  }
  const data = await safeLoad(`/api/v5/resources/sync-policy?company_id=${encodeURIComponent(state.selectedCompanyId)}`);
  const policy = data?.policy;
  if (!policy) return;
  setChecked("autoSyncEnabled", policy.enabled);
  setValue("autoSyncInterval", policy.interval_seconds);
  setValue("autoSyncLimitResources", policy.limit_resources);
  setValue("autoSyncEventLimit", policy.event_limit);
  setValue("autoSyncMaxPages", policy.max_pages);
  setValue("autoSyncResourceTypes", (policy.resource_types || []).join(","));
  setValue("autoSyncStatuses", (policy.statuses || []).join(","));
  setValue("largeDocumentMode", policy.large_document_mode);
  setValue("bitableMode", policy.bitable_mode);
  setValue("memoryMode", policy.memory_mode);
  setValue("vectorMode", policy.vector_mode);
  showResult("syncPolicyResult", policy);
}

async function saveSyncPolicy() {
  if (!state.selectedCompanyId) {
    showResult("syncPolicyResult", "请先选择公司。");
    return;
  }
  const payload = {
    company_id: state.selectedCompanyId,
    enabled: checked("autoSyncEnabled"),
    interval_seconds: Number(value("autoSyncInterval") || 900),
    limit_resources: Number(value("autoSyncLimitResources") || 10),
    event_limit: Number(value("autoSyncEventLimit") || 20),
    max_pages: Number(value("autoSyncMaxPages") || 2),
    resource_types: csv(value("autoSyncResourceTypes")),
    statuses: csv(value("autoSyncStatuses")),
    extract_items: true,
    large_document_mode: value("largeDocumentMode") || "index_only",
    bitable_mode: value("bitableMode") || "master_data_index",
    memory_mode: value("memoryMode") || "stable_facts_only",
    vector_mode: value("vectorMode") || "summaries_and_hot_knowledge",
  };
  await safeAction("syncPolicyResult", async () => api("/api/v5/resources/sync-policy", {
    method: "PUT",
    body: JSON.stringify(payload),
  }));
  await loadResourceMonitoring();
}

async function loadResourceMonitoring() {
  const data = await safeLoad(`/api/v5/resources/monitoring${companyQuery()}`);
  renderChips("monitoringStats", data?.counts || {});
  renderChips("monitoringTypeStats", {
    ...(data?.resource_type_counts || {}),
    ...(data?.sync_action_counts || {}),
  });
  showResult("monitoringResult", {
    recommendations: data?.recommendations || [],
    governance_actions: data?.governance_actions || [],
    platform_counts: data?.platform_counts || {},
  });
}

async function loadSyncStrategy() {
  const data = await safeLoad("/api/v5/resources/sync-strategy");
  renderTable("syncStrategyTable", data?.layers || [], [
    ["name", "数据层"],
    ["sync_mode", "同步方式"],
    ["query_path", "查询路径"],
    ["resource_types", "资源类型"],
    ["vectorize", "向量策略"],
    ["retention", "本地保存原则"],
  ]);
  renderTable("syncAuthorizationTable", data?.authorization_model || [], [
    ["name", "飞书能力"],
    ["resource_type", "资源类型"],
    ["access_mode", "授权方式"],
    ["required_identifiers", "必需标识"],
    ["preferred_sync_path", "优先接口"],
    ["default_sync_action", "默认动作"],
    ["note", "说明"],
  ]);
  showResult("syncStrategyResult", {
    data_source: data?.data_source,
    query_policies: data?.query_policies,
    defaults: data?.defaults,
    authorization_model: data?.authorization_model,
    rules: data?.rules,
  });
}

async function syncStaleResources() {
  if (!state.selectedCompanyId) {
    showResult("resourceSyncResult", "请先选择公司，再执行批量同步。");
    return;
  }
  const payload = batchSyncPayload();
  await safeAction("resourceSyncResult", async () => api("/api/v5/resources/sync", {
    method: "POST",
    body: JSON.stringify(payload),
  }));
  await loadResources();
}

async function previewStaleResources() {
  if (!state.selectedCompanyId) {
    showResult("resourceSyncResult", "请先选择公司，再预览同步。");
    return;
  }
  const result = await safeAction("resourceSyncResult", async () => api("/api/v5/resources/sync-preview", {
    method: "POST",
    body: JSON.stringify(batchSyncPayload()),
  }));
  renderTable("resourceSyncPreviewTable", result?.items || [], [
    ["resource_name", "资源"],
    ["resource_type", "类型"],
    ["data_layer", "数据层"],
    ["sync_action", "同步动作"],
    ["query_path", "查询路径"],
    ["decision_status", "决策"],
    ["executable", "可执行"],
    ["next_action", "下一步"],
    ["reason", "原因"],
  ], { compactId: true });
}

function batchSyncPayload() {
  const resourceType = value("batchSyncResourceType");
  const statuses = csv(value("batchSyncStatuses"));
  const limitResources = Number(value("batchSyncLimit") || 5);
  return {
    company_id: state.selectedCompanyId,
    resource_type: resourceType || null,
    statuses,
    limit_resources: Math.min(Math.max(limitResources, 1), 50),
    limit: 20,
    max_pages: 2,
    extract_items: true,
    ...syncPolicyModes(),
  };
}

async function loadAdministrationFoundation() {
  const company = companyQuery();
  const [
    overview,
    companySettings,
    departments,
    teams,
    users,
    roles,
    permissions,
    resourcePermissions,
  ] = await Promise.all([
    safeLoad(`/api/v5/os/overview${company}`),
    safeLoad(`/api/v5/administration/company-settings${company}`),
    safeLoad(`/api/v5/administration/departments${company}`),
    safeLoad(`/api/v5/administration/teams${company}`),
    safeLoad(`/api/v5/administration/users${company}`),
    safeLoad(`/api/v5/administration/roles${company}`),
    safeLoad(`/api/v5/administration/permissions${company}`),
    safeLoad(`/api/v5/administration/resource-permissions${company}`),
  ]);
  renderFoundationMetrics(overview);
  renderTable("companySettingsTable", companySettings?.items || [], [
    ["company_name", "公司"],
    ["company_code", "代码"],
    ["status", "状态"],
    ["settings", "设置"],
    ["updated_at", "更新时间"],
  ]);
  renderTable("departmentsTable", departments?.items || [], [
    ["name", "部门"],
    ["description", "描述"],
    ["parent_department_id", "上级部门"],
    ["external_id", "飞书 ID"],
  ]);
  renderTable("teamsTable", teams?.items || [], [
    ["name", "团队"],
    ["department_name", "所属部门"],
    ["description", "描述"],
  ]);
  renderTable("adminUsersTable", users?.items || [], [
    ["display_name", "姓名"],
    ["company_name", "公司"],
    ["role", "角色"],
    ["scope_type", "范围"],
    ["email", "邮箱"],
    ["is_active", "启用"],
  ]);
  renderTable("rolesTable", roles?.items || [], [
    ["name", "角色"],
    ["scope_type", "范围"],
    ["description", "描述"],
    ["permissions", "权限域"],
  ]);
  renderTable("permissionsTable", permissions?.items || [], [
    ["code", "权限代码"],
    ["name", "权限名称"],
    ["permission_type", "类型"],
    ["description", "说明"],
  ]);
  renderTable("adminResourcePermissionsTable", resourcePermissions?.items || [], [
    ["resource_type", "资源类型"],
    ["resource_name", "资源"],
    ["role", "角色"],
    ["user", "用户"],
    ["permission_level", "权限"],
    ["enabled", "启用"],
  ]);
  showResult("foundationResult", overview || "暂无系统底座数据，请先初始化");
}

async function bootstrapFoundation() {
  const query = state.selectedCompanyId ? `?company_id=${encodeURIComponent(state.selectedCompanyId)}` : "";
  await safeAction("foundationResult", async () => api(`/api/v5/bootstrap/foundation${query}`, {
    method: "POST",
  }));
  await loadAdministrationFoundation();
}

async function loadItems() {
  const params = new URLSearchParams();
  if (state.selectedCompanyId) params.set("company_id", state.selectedCompanyId);
  const type = document.getElementById("itemType").value;
  if (type) params.set("item_type", type);
  params.set("limit", "100");
  const data = await safeLoad(`/api/extracted-items?${params.toString()}`);
  renderTable("itemsTable", data?.items || [], [
    ["item_type", "类型"],
    ["title", "标题"],
    ["owner", "负责人"],
    ["priority", "优先级"],
    ["status", "状态"],
    ["created_at", "创建时间"],
  ]);
}

async function loadReports() {
  const params = new URLSearchParams();
  if (state.selectedCompanyId) params.set("company_id", state.selectedCompanyId);
  params.set("limit", "20");
  const data = await safeLoad(`/api/reports?${params.toString()}`);
  showResult("reportsResult", data);
}

async function loadBotUsers() {
  const data = await safeLoad(`/api/bot-users${companyQuery()}`);
  const items = data?.items || [];
  state.botUsers = items;
  renderChips("employeeAgentStats", employeeAgentStats(items));
  renderEmployeeAgentRuntimeBoard(items);
  renderEmployeeAgentToolBoard(items);
  renderEmployeeAgentAuthorizationBoard(items);
  renderEmployeeAgentEvidenceBoard(items);
  renderReplyModeBoard("employeeAgentReplyModeBoard", items[0]?.reply_modes || state.replyModes?.items || defaultReplyModes(), { compact: true });
  renderTable("botUsersTable", data?.items || [], [
    ["agent_title", "员工 Agent"],
    ["display_name", "姓名"],
    ["agent_status", "状态"],
    ["agent_entrypoint", "入口"],
    ["agent_launch_summary", "入口就绪"],
    ["agent_activated_at", "激活时间"],
    ["agent_first_chat_id", "首个会话"],
    ["global_shared_tool_summary", "共享 Tool"],
    ["reply_mode_summary", "回复模式"],
    ["enterprise_identity_boundary", "企业边界"],
    ["user_identity_boundary", "用户边界"],
    ["user_identity_summary", "授权状态"],
    ["latest_gateway_message_summary", "最近消息入口"],
    ["latest_gateway_used_agent_runtime", "Agent Runtime"],
    ["latest_gateway_final_answer_owner", "消息最终回复"],
    ["latest_gateway_tool_summary", "消息 Tool"],
    ["latest_gateway_authorization_summary", "消息授权"],
    ["latest_tool_execution_summary", "最近 Tool"],
    ["latest_tool_enterprise_identity_boundary", "最近企业边界"],
    ["latest_tool_user_identity_boundary", "最近用户边界"],
    ["latest_tool_cli_profile", "CLI Profile"],
    ["latest_tool_final_answer_owner", "最终回复"],
    ["latest_tool_cannot_escalate_original_permissions", "不提权"],
    ["pending_user_resource_count", "待授权"],
    ["agent_next_action", "下一步"],
    ["role", "角色"],
    ["access_scope", "范围"],
    ["domains", "业务域"],
    ["open_id", "飞书身份"],
  ]);
}

function employeeAgentStats(items) {
  const agents = items || [];
  const active = agents.filter((item) => item.agent_status === "active").length;
  const authorized = agents.filter((item) => Number(item.authorized_user_resource_count || 0) > 0).length;
  const toolCount = agents[0]?.global_shared_tool_count || businessToolFamilies.length;
  return {
    "员工 Agent": agents.length,
    "启用": active,
    "已授权个人资源": authorized,
    "共享 Tool": toolCount,
  };
}

function renderEmployeeAgentRuntimeBoard(items) {
  const first = (items || [])[0] || {};
  const contract = first.runtime_contract || {};
  const rows = [
    ["入口链路", first.entrypoint_chain || "飞书消息入口 -> Message Gateway -> Agent Runtime -> Tool Router -> Tool -> 数据源/执行源 -> Agent Runtime -> 回复用户", "只有 Agent Runtime 可以生成最终回复"],
    ["入口状态", first.agent_launch_summary || "等待员工 Agent", first.agent_activation_source ? `来源：${first.agent_activation_source}` : "真实消息入口依赖公司飞书 AppConfig"],
    ["Tool 职责", contract.tool_decides_data_or_execution_source ? "Tool 决定数据源/执行源" : "待确认", "Tool Router 只做业务能力路由，Tool 返回结构化结果"],
    ["权限边界", first.data_permission_policy || "只能收紧，不能突破 App Identity 或 User Identity", "企业资源受 App Identity、Company Scope、Role Scope；用户级资源受 User Identity 和资源所有者授权"],
    ["回复模式", first.reply_mode_summary || "Fast（无企业数据） / Normal（实时单 Tool） / Thinking（WorkEvent、Knowledge、Memory、多 Tool 分析）", "Fast 不读企业数据；Normal 调单 Tool；Thinking 先显示思考路径"],
  ];
  setHtml("employeeAgentRuntimeBoard", rows.map(([label, status, detail]) => `
    <div class="architecture-row">
      <span>${escapeHtml(label)}</span>
      <strong>${escapeHtml(status)}</strong>
      <small>${escapeHtml(detail)}</small>
    </div>
  `).join(""));
}

function renderEmployeeAgentToolBoard(items) {
  const first = (items || [])[0] || {};
  const statuses = first.global_shared_tool_statuses || businessToolFamilies.map(([family, label, scope]) => ({
    business_tool: family,
    label,
    scope,
    availability: "shared",
    agent_can_call: true,
    data_boundary: "identity_scoped_tighten_only",
  }));
  setHtml("employeeAgentToolBoard", statuses.map((item) => {
    const family = item.business_tool || "-";
    const meta = businessToolFamilies.find(([name]) => name === family) || [family, family, ""];
    const availability = item.agent_can_call ? "所有员工 Agent 可调用" : "未开放";
    return `
      <div class="tool-family">
        <span>${escapeHtml(family)}</span>
        <strong>${escapeHtml(meta[1] || family)}</strong>
        <small>${escapeHtml(meta[2] || "")}</small>
        <em>${escapeHtml(`${availability} · 数据边界 ${item.data_boundary || "identity_scoped_tighten_only"}`)}</em>
      </div>
    `;
  }).join(""));
}

function renderEmployeeAgentAuthorizationBoard(items) {
  const agents = items || [];
  const first = agents[0] || {};
  const pending = agents.reduce((sum, item) => sum + Number(item.pending_user_resource_count || 0), 0);
  const authorized = agents.reduce((sum, item) => sum + Number(item.authorized_user_resource_count || 0), 0);
  const authorizations = first.user_identity_authorizations || [];
  const authorizationActionSummary = authorizations
    .flatMap((item) => item.authorization_actions || [])
    .map((action) => action.label)
    .join(" / ");
  const rows = [
    ["企业级资源", first.enterprise_identity_boundary || "App Identity + Company Scope + Role Scope", "Digital Advisor 只能在飞书 App 权限内进一步收紧"],
    ["用户级资源", `${authorized} 项已授权 · ${pending} 项待授权`, first.personal_authorization_next_action || "首次使用用户级能力时引导本人授权"],
    ["授权清单", authorizations.map((item) => `${item.label}:${item.status}@${item.owner_open_id || "本人"}`).join(" / ") || "暂无员工 Agent", "未授权前不读取个人飞书、其他邮箱、个人钉钉或个人微信数据"],
    ["授权入口", authorizationActionSummary || "首次使用时按需生成", "Owner 与员工均由资源所有者本人完成统一授权；后台只展示授权状态"],
  ];
  setHtml("employeeAgentAuthorizationBoard", rows.map(([label, status, detail]) => `
    <div class="architecture-row">
      <span>${escapeHtml(label)}</span>
      <strong>${escapeHtml(status)}</strong>
      <small>${escapeHtml(detail)}</small>
    </div>
  `).join(""));
}

function renderEmployeeAgentEvidenceBoard(items) {
  const agents = items || [];
  const latestGateway = agents.find((item) => item.latest_gateway_message)?.latest_gateway_message || null;
  const latest = agents.find((item) => item.latest_tool_execution)?.latest_tool_execution || null;
  const gatewayEvidenceCount = agents.filter((item) => item.latest_gateway_message).length;
  const evidenceCount = agents.filter((item) => item.latest_tool_execution).length;
  const rows = [
    ["消息入口证据", `${gatewayEvidenceCount} 个员工有近期消息`, latestGateway ? `${latestGateway.status || "unknown"} · ${latestGateway.route_label || latestGateway.route_path || "未路由"}` : "暂无飞书消息入口记录"],
    ["Gateway链路", latestGateway?.gateway_chain?.join(" -> ") || "-", "飞书消息入口 -> Message Gateway -> Agent Runtime -> Tool -> 回复用户"],
    ["消息回复模式", latestGateway?.reply_mode_label || "-", latestGateway?.reply_mode_data_requirement || "Fast / Normal / Thinking"],
    ["消息 Tool步骤", latestGateway?.agent_runtime_tool_steps?.map((step) => step.business_tool || step.name).join(" / ") || "无 Tool 调用", "Tool 返回结构化结果，Agent Runtime 生成最终回复"],
    ["消息授权", latestGateway?.user_identity_authorization_required ? `${(latestGateway.required_user_identity_resources || []).join(" / ")} · ${latestGateway.authorization_action_count || 0} 个入口` : "无需授权", latestGateway?.authorization_owner_open_id ? `资源所有者：${latestGateway.authorization_owner_open_id}` : "首次使用个人能力时由本人授权"],
    ["Tool 调用证据", `${evidenceCount} 个员工有近期记录`, latest ? `${latest.business_tool || latest.tool_name || "Tool"} · ${latest.status || "unknown"}` : "暂无员工 Tool 调用记录"],
    ["数据源/执行源", latest ? (latest.execution_source || latest.data_source || "无企业数据") : "-", "Tool 决定使用数据源或执行源"],
    ["企业边界", latest?.enterprise_identity_boundary || "-", "App Identity + Company Scope + Role Scope"],
    ["用户边界", latest?.user_identity_boundary || "-", "User Identity + 资源所有者本人授权"],
    ["CLI Profile", latest?.cli_profile || "默认 profile", "多家公司按 Feishu App 配置选择独立 CLI Profile"],
    ["结构化结果", latest?.tool_returns_structured_result ? "已返回结构化结果" : "待产生证据", "Tool 返回结构化结果，Agent Runtime 生成最终回复"],
    ["最终回复归属", latest?.final_answer_owner || "agent_runtime", "只有 Agent Runtime 可以生成最终回复"],
    ["不提权", latest?.cannot_escalate_original_permissions === false ? "异常：需检查" : "只能收紧", "Digital Advisor 不突破飞书 App 或用户原始授权范围"],
  ];
  setHtml("employeeAgentEvidenceBoard", rows.map(([label, status, detail]) => `
    <div class="architecture-row">
      <span>${escapeHtml(label)}</span>
      <strong>${escapeHtml(status)}</strong>
      <small>${escapeHtml(detail)}</small>
    </div>
  `).join(""));
}

async function loadPermissionRules() {
  await safeAction("permissionResult", async () => api("/api/bot-permission-rules"));
}

async function recalculatePermissions(dryRun) {
  if (!state.selectedCompanyId) {
    showResult("permissionResult", "请先选择公司");
    return;
  }
  const payload = { company_id: state.selectedCompanyId, dry_run: dryRun, update_manual_admins: false };
  await safeAction("permissionResult", async () => api("/api/bot-users/recalculate-permissions", {
    method: "POST",
    body: JSON.stringify(payload),
  }));
  if (!dryRun) await loadBotUsers();
}

async function loadAudit() {
  const params = new URLSearchParams();
  if (state.selectedCompanyId) params.set("company_id", state.selectedCompanyId);
  params.set("limit", "100");
  const data = await safeLoad(`/api/audit-logs?${params.toString()}`);
  renderTable("auditTable", data?.items || [], [
    ["action", "动作"],
    ["target_type", "对象"],
    ["target_id", "对象 ID"],
    ["created_at", "时间"],
  ]);
}

async function loadSystemLogs() {
  const params = new URLSearchParams();
  if (state.selectedCompanyId) params.set("company_id", state.selectedCompanyId);
  if (value("systemLogCategory")) params.set("category", value("systemLogCategory"));
  if (value("systemLogSeverity")) params.set("severity", value("systemLogSeverity"));
  if (value("systemLogStatus")) params.set("status", value("systemLogStatus"));
  if (value("systemLogReason")) params.set("reason", value("systemLogReason"));
  if (value("systemLogAction")) params.set("action", value("systemLogAction"));
  if (value("systemLogUsedAgentRuntime")) params.set("used_agent_runtime", value("systemLogUsedAgentRuntime"));
  if (value("systemLogFinalAnswerOwner")) params.set("final_answer_owner", value("systemLogFinalAnswerOwner"));
  if (value("systemLogRoutePath")) params.set("route_path", value("systemLogRoutePath"));
  if (value("systemLogAgentId")) params.set("agent_id", value("systemLogAgentId"));
  if (value("systemLogAgentOwnerOpenId")) params.set("agent_owner_open_id", value("systemLogAgentOwnerOpenId"));
  if (value("systemLogConfirmed")) params.set("confirmed", value("systemLogConfirmed"));
  if (value("systemLogConfirmationChecked")) {
    params.set("confirmation_token_checked", value("systemLogConfirmationChecked"));
  }
  params.set("limit", "100");
  const data = await safeLoad(`/api/v5/system/logs?${params.toString()}`);
  renderChips("systemLogStats", data?.counts || {});
  renderChips("systemLogCategoryStats", data?.category_counts || {});
  renderTable("systemLatestErrorsTable", data?.latest_errors || [], [
    ["severity", "级别"],
    ["category", "分类"],
    ["action", "动作"],
    ["target_id", "对象 ID"],
    ["actor", "调用人"],
    ["error", "错误"],
    ["created_at", "时间"],
  ], { compactId: true, emptyMessage: "暂无错误日志" });
  renderTable("systemLogsTable", data?.items || [], [
    ["severity", "级别"],
    ["category", "分类"],
    ["status", "状态"],
    ["reason", "原因"],
    ["action", "动作"],
    ["target_type", "对象"],
    ["target_id", "对象 ID"],
    ["actor", "调用人"],
    ["agent_owner_display_name", "员工 Agent"],
    ["agent_owner_open_id", "员工飞书身份"],
    ["shared_business_tool_count", "共享 Tool"],
    ["data_permission_model", "权限模型"],
    ["provider", "执行通道"],
    ["used_agent_runtime", "Agent Runtime"],
    ["final_answer_owner", "最终回复"],
    ["route_path", "路由"],
    ["route_label", "能力路径"],
    ["reply_mode_label", "回复模式"],
    ["reply_mode_data_requirement", "数据需求"],
    ["reply_mode_enterprise_data_required", "企业数据"],
    ["reply_mode_pre_reply_required", "预回复"],
    ["agent_runtime_step_count", "Agent步数"],
    ["confirmation_token_checked", "确认校验"],
    ["expected_action", "预期动作"],
    ["write_target_summary", "写目标"],
    ["error", "错误"],
    ["created_at", "时间"],
  ], {
    compactId: true,
    compactKeys: ["agent_id", "agent_owner_open_id", "target_id"],
    emptyMessage: "暂无运行日志",
    rowAction: showSystemLogDetail,
  });
  showResult("systemLogResult", {
    severity_counts: data?.severity_counts || {},
    latest: (data?.items || []).slice(0, 10),
  });
}

function showSystemLogDetail(item) {
  if (!item) return;
  showResult("systemLogResult", {
    summary: item.summary,
    action: item.action,
    target: {
      type: item.target_type,
      id: item.target_id,
    },
    gateway: {
      used_agent_runtime: item.used_agent_runtime,
      final_answer_owner: item.final_answer_owner,
      route_path: item.route_path,
      route_label: item.route_label,
      reply_mode: item.reply_mode,
      reply_mode_data_requirement: item.reply_mode_data_requirement,
      reply_mode_enterprise_data_required: item.reply_mode_enterprise_data_required,
      reply_mode_pre_reply_required: item.reply_mode_pre_reply_required,
      reply_mode_tool_strategy: item.reply_mode_tool_strategy,
      gateway_chain: item.gateway_chain,
    },
    agent_identity: {
      agent_id: item.agent_id,
      agent_type: item.agent_type,
      owner_open_id: item.agent_owner_open_id,
      owner_display_name: item.agent_owner_display_name,
      shared_business_tool_count: item.shared_business_tool_count,
      data_permission_model: item.data_permission_model,
      contract: item.agent_identity,
    },
    agent_runtime: {
      step_count: item.agent_runtime_step_count,
      trace: item.agent_runtime_trace,
      tool_steps: item.agent_runtime_tool_steps || [],
      workflow_steps: item.agent_runtime_workflow_steps || [],
    },
    write: {
      confirmed: item.confirmed,
      confirmation_token_checked: item.confirmation_token_checked,
      expected_action: item.expected_action,
      write_target: item.write_target,
    },
    error: item.error,
    created_at: item.created_at,
  });
}

async function viewGatewayCardIssues() {
  setValue("systemLogCategory", "gateway");
  setValue("systemLogSeverity", "warning");
  setValue("systemLogStatus", "ignored");
  setValue("systemLogReason", "unhandled_card_action");
  setValue("systemLogAction", "gateway.feishu.card_action");
  setValue("systemLogUsedAgentRuntime", "");
  setValue("systemLogFinalAnswerOwner", "");
  setValue("systemLogRoutePath", "");
  setValue("systemLogAgentId", "");
  setValue("systemLogAgentOwnerOpenId", "");
  setValue("systemLogConfirmed", "");
  setValue("systemLogConfirmationChecked", "");
  showSettingsTab("audit");
  await loadSystemLogs();
}

async function viewAgentRuntimeGatewayLogs() {
  setValue("systemLogCategory", "gateway");
  setValue("systemLogSeverity", "");
  setValue("systemLogStatus", "handled");
  setValue("systemLogReason", "");
  setValue("systemLogAction", "gateway.feishu.message");
  setValue("systemLogUsedAgentRuntime", "true");
  setValue("systemLogFinalAnswerOwner", "agent_runtime");
  setValue("systemLogRoutePath", "");
  setValue("systemLogAgentId", "");
  setValue("systemLogAgentOwnerOpenId", "");
  setValue("systemLogConfirmed", "");
  setValue("systemLogConfirmationChecked", "");
  showSettingsTab("audit");
  await loadSystemLogs();
}

async function loadToolExecutions() {
  if (!state.selectedCompanyId) {
    renderChips("toolExecutionStats", {});
    renderTable("toolExecutionsTable", [], [], { emptyMessage: "请先选择公司" });
    return;
  }
  const params = new URLSearchParams();
  params.set("company_id", state.selectedCompanyId);
  params.set("limit", "100");
  const data = await safeLoad(`/api/v5/tools/executions?${params.toString()}`);
  renderChips("toolExecutionStats", data?.counts || {});
  renderTable("toolExecutionsTable", data?.items || [], [
    ["tool_name", "工具"],
    ["business_tool", "业务 Tool"],
    ["provider", "执行通道"],
    ["execution_source", "执行源"],
    ["data_source", "数据源"],
    ["cli_profile", "CLI Profile"],
    ["cli_profile_source", "Profile 来源"],
    ["source_chain", "来源链路"],
    ["execution_chain", "执行链路"],
    ["status", "状态"],
    ["tool_returns_structured_result", "结构化结果"],
    ["final_answer_owner", "最终回复"],
    ["data_permission_model", "数据权限"],
    ["enterprise_identity_boundary", "企业边界"],
    ["company_scope", "公司范围"],
    ["role_scope", "角色范围"],
    ["user_identity_boundary", "用户边界"],
    ["cannot_escalate_original_permissions", "不提权"],
    ["write_mode", "写模式"],
    ["has_confirmation_token", "确认令牌"],
    ["write_target_summary", "写目标"],
    ["actor", "调用人"],
    ["duration_ms", "耗时 ms"],
    ["error", "错误"],
    ["chat_id", "会话"],
    ["created_at", "时间"],
  ], { compactId: true });
}

async function loadToolConfigurations() {
  if (!state.selectedCompanyId) {
    renderChips("toolConfigStats", {});
    renderTable("toolConfigsTable", [], [], { emptyMessage: "请先选择公司" });
    showResult("toolConfigResult", "请先选择公司后管理工具策略。");
    return;
  }
  const data = await safeLoad(`/api/v5/tools?company_id=${encodeURIComponent(state.selectedCompanyId)}`);
  const legacyItems = data?.items || [];
  state.legacyToolConfigsByName = Object.fromEntries(legacyItems.map((item) => [item.tool_name, item]));
  const registry = await loadCapabilityRegistry({ silent: true });
  const registryRows = skillRegistryRows(registry?.skill_registry_payload);
  const items = registryRows.length ? registryRows : legacyItems;
  if (registryRows.length) {
    const summary = registry.skill_registry_payload?.summary || {};
    logCapabilityRegistryDiff(
      "skill_registry_payload",
      {
        tools: legacyItems.length,
        business_tools: new Set(legacyItems.map((item) => item.business_tool).filter(Boolean)).size,
      },
      {
        skills: summary.skill_count || registryRows.length,
        capabilities: registry.skill_registry_payload?.capabilities?.length || 0,
        missing_provider: summary.missing_provider_count || 0,
      },
      {
        skill_registry_coverage: registryCoverage(summary.skill_count || registryRows.length, registryRows.length),
        provider_binding_coverage: registryCoverage(
          summary.skill_count || registryRows.length,
          registryRows.filter((item) => item.provider !== "unbound").length,
        ),
      },
    );
  }
  renderChips("toolConfigStats", {
    ...(registryRows.length ? (registry?.skill_registry_payload?.summary || {}) : (data?.counts || {})),
    business_tools: new Set(items.map((item) => item.business_tool).filter(Boolean)).size,
    write_tools: items.filter((item) => item.supports_write).length,
    disabled: items.filter((item) => item.enabled === false).length,
  });
  renderTable("toolConfigsTable", items, [
    ["tool_name", "工具"],
    ["business_tool", "业务工具"],
    ["provider", "执行通道"],
    ["compatible_providers", "可用通道"],
    ["enabled", "启用"],
    ["supports_write", "写操作"],
    ["required_permissions", "权限"],
    ["audit_action", "审计动作"],
  ], { rowAction: selectToolConfig });
  const selected = items.find((item) => item.tool_name === state.selectedToolName);
  if (selected) selectToolConfig(selected, { silent: true });
}

function selectToolConfig(item, options = {}) {
  state.selectedToolName = item.tool_name || "";
  state.selectedToolSupportsWrite = Boolean(item.supports_write);
  state.selectedToolCompatibleProviders = item.compatible_providers || [];
  state.selectedToolProviderBoundaries = item.provider_boundaries || {};
  state.selectedToolAuditAction = item.audit_action || "";
  setValue("selectedToolName", state.selectedToolName);
  setValue("selectedToolProvider", item.provider || "local");
  setChecked("selectedToolEnabled", item.enabled !== false);
  setValue("selectedToolConfigJson", JSON.stringify(item.config_json || {}, null, 2));
  if (state.selectedToolParamTemplateName !== state.selectedToolName) {
    setValue("toolExecutionParamsJson", JSON.stringify(toolExecutionTemplate(state.selectedToolName), null, 2));
    setValue("toolExecutionConfirmationToken", "");
    state.selectedToolParamTemplateName = state.selectedToolName;
  }
  renderToolRuntimeHint();
  if (!options.silent) {
    showResult("toolConfigResult", {
      tool_name: item.tool_name,
      business_tool: item.business_tool,
      business_tool_capabilities: item.business_tool_capabilities || [],
      provider: item.provider,
      enabled: item.enabled,
      required_permissions: item.required_permissions || [],
      supports_write: item.supports_write,
      audit_action: item.audit_action,
    });
  }
}

function renderToolRuntimeHint() {
  const el = document.getElementById("toolRuntimeHint");
  if (!el) return;
  const provider = value("selectedToolProvider") || "local";
  const toolName = value("selectedToolName") || "未选择工具";
  const boundaries = state.selectedToolProviderBoundaries || {};
  const compatible = state.selectedToolCompatibleProviders.length
    ? state.selectedToolCompatibleProviders.includes(provider)
    : true;
  const hints = {
    local: "Local 使用已同步数据和本地领域逻辑，不直接调用飞书实时 API；适合默认问答和经营驾驶舱读取。",
    feishu_api: "Feishu API 只保留给 Sync Engine、资源发现、管理端同步预览和受控验证；实时 Agent 操作必须走 Tool Router -> Tool -> MCP -> CLI。",
    feishu_mcp: "Feishu MCP 只负责工具调度，不直接执行动作；已绑定工具由 CLI 执行动作，未绑定时执行会被拒绝。",
    report: "Report provider 面向经营报告和驾驶舱聚合能力，不适合直接替代单个飞书原子 API。",
    devops: "DevOps provider 只应开放给后台运维和 CLI 验证，不应作为普通员工 Agent 的默认工具。",
  };
  let tone = "neutral";
  if (!compatible || provider === "feishu_api" || provider === "devops") tone = "danger";
  else if (provider === "feishu_mcp") tone = "warn";
  const mcp = boundaries.feishu_mcp || {};
  const api = boundaries.feishu_api || {};
  const responsibility = mcp.responsibility_boundary || api.responsibility_boundary || {};
  const responsibilityText = responsibility.tool_router
    ? ` 职责：Tool Router=${responsibility.tool_router}；MCP=${responsibility.mcp}；CLI=${responsibility.cli}；API=${responsibility.api}。`
    : "";
  const writeText = state.selectedToolSupportsWrite ? "该工具声明为写操作能力。" : "该工具声明为读操作能力。";
  const boundaryText = provider === "feishu_mcp"
    ? ` MCP 绑定：${mcp.binding_status || "unknown"}；执行通道：${mcp.mcp_provider || "feishu_mcp"}；角色：${mcp.role || "tool_scheduling"}；动作执行器：${mcp.action_executor || "lark_cli"}；MCP是否直接执行动作：${mcp.performs_action_execution ? "是" : "否"}；${mcp.requires_explicit_tool_binding ? "需要显式工具绑定" : "已允许"}；默认写入：${mcp.default_write_enabled ? "开启" : "关闭"}；阻断原因：${mcp.blocked_reason || "无"}。`
    : provider === "feishu_api"
      ? ` API能力：${api.available ? "已登记" : "未登记"}；API写能力登记：${api.registered_api_write_capability ? "有" : "无"}；角色：${api.role || api.api_role || "sync_engine_data_sync"}；实时读：${api.realtime_read_allowed ? "允许" : "禁止"}；confirmed实时写：${api.supports_confirmed_realtime_write ? "允许" : "禁止"}；MCP访问：${api.mcp_access_allowed ? "允许" : "禁止"}；受控验证：${api.controlled_validation_allowed ? "允许" : "禁止"}；写策略：${api.confirmed_write_policy || "不适用"}；Agent直连：${api.agent_runtime_direct_access === false ? "禁止" : "未锁定"}；dry-run：${api.requires_dry_run_for_write ? "仅预演" : "不适用"}。`
      : "";
  const compatibleText = compatible ? "" : " 当前执行通道不在该工具兼容列表内，保存会被后端拒绝。";
  el.className = `tool-hint ${tone === "neutral" ? "" : tone}`.trim();
  el.textContent = `${toolName} · ${writeText} ${hints[provider] || hints.local}${boundaryText}${responsibilityText}${compatibleText}`;
}

async function saveToolConfig() {
  if (!state.selectedCompanyId) {
    showResult("toolConfigResult", "请先选择公司。");
    return;
  }
  const toolName = value("selectedToolName");
  if (!toolName) {
    showResult("toolConfigResult", "请先在左侧选择一个工具。");
    return;
  }
  let configJson = {};
  const rawConfig = value("selectedToolConfigJson");
  if (rawConfig) {
    try {
      configJson = JSON.parse(rawConfig);
    } catch (err) {
      showResult("toolConfigResult", `策略配置无法解析：${err.message}`);
      return;
    }
  }
  const result = await safeAction("toolConfigResult", async () => api(
    `/api/v5/tools/${encodeURIComponent(toolName)}?company_id=${encodeURIComponent(state.selectedCompanyId)}`,
    {
      method: "PUT",
      body: JSON.stringify({
        enabled: checked("selectedToolEnabled"),
        provider: value("selectedToolProvider") || "local",
        config_json: configJson,
      }),
    },
  ));
  if (result) await loadToolConfigurations();
}

async function updateToolBatch(dryRun) {
  if (!state.selectedCompanyId) {
    showResult("toolConfigResult", "请先选择公司。");
    return;
  }
  const scope = value("toolBatchScope") || "write";
  const enabledValue = value("toolBatchEnabled");
  const providerValue = value("toolBatchProvider");
  if (!enabledValue && !providerValue) {
    showResult("toolConfigResult", "请选择要批量修改的启用状态或执行通道。");
    return;
  }
  const payload = {
    all_tools: scope === "all",
    include_prefixes: scope === "feishu" ? ["feishu_"] : [],
    supports_write: scope === "write" ? true : null,
    enabled: enabledValue === "" ? null : enabledValue === "true",
    provider: providerValue || null,
    dry_run: dryRun,
  };
  const result = await safeAction("toolConfigResult", async () => api(
    `/api/v5/tools/batch?company_id=${encodeURIComponent(state.selectedCompanyId)}`,
    {
      method: "POST",
      body: JSON.stringify(payload),
    },
  ));
  if (!result) return;
  showResult("toolConfigResult", result);
  if (!dryRun) await loadToolConfigurations();
}

function toolExecutionTemplate(toolName) {
  const templates = {
    feishu_bitable_record_create: {
      dry_run: true,
      validate_fields: true,
      app_token: "app_token",
      table_id: "table_id",
      fields: { "字段名": "字段值" },
    },
    feishu_bitable_record_batch_create: {
      dry_run: true,
      validate_fields: true,
      app_token: "app_token",
      table_id: "table_id",
      fields: ["任务名称", "状态"],
      rows: [["拜访客户 A", "待处理"], ["拜访客户 B", "待处理"]],
    },
    feishu_bitable_record_batch_update: {
      dry_run: true,
      validate_fields: true,
      app_token: "app_token",
      table_id: "table_id",
      record_id_list: ["rec_xxx", "rec_yyy"],
      patch: { "状态": "已完成" },
    },
    feishu_bitable_record_batch_delete: {
      dry_run: true,
      app_token: "app_token",
      table_id: "table_id",
      record_id_list: ["rec_xxx", "rec_yyy"],
    },
    feishu_bitable_record_upsert: {
      dry_run: true,
      validate_fields: true,
      app_token: "app_token",
      table_id: "table_id",
      record_id: "rec_xxx",
      fields: { "字段名": "字段值" },
    },
    feishu_bitable_record_upload_attachment: {
      dry_run: true,
      app_token: "app_token",
      table_id: "table_id",
      record_id: "record_id",
      field_id: "attachment_field_id",
      files: ["attachments/example.pdf"],
    },
    feishu_bitable_record_remove_attachment: {
      dry_run: true,
      app_token: "app_token",
      table_id: "table_id",
      record_id: "record_id",
      field_id: "attachment_field_id",
      file_tokens: ["file_token"],
    },
    feishu_bitable_record_update: {
      dry_run: true,
      validate_fields: true,
      app_token: "app_token",
      table_id: "table_id",
      record_id: "record_id",
      fields: { "状态": "跟进中" },
    },
    feishu_bitable_record_delete: {
      dry_run: true,
      app_token: "app_token",
      table_id: "table_id",
      record_id: "record_id",
    },
    feishu_bitable_field_list: {
      app_token: "app_token",
      table_id: "table_id",
      limit: 100,
      offset: 0,
    },
    feishu_bitable_table_create: {
      dry_run: true,
      app_token: "app_token",
      name: "客户档案",
      fields: [
        { name: "客户名称", type: "text" },
        { name: "状态", type: "select", options: [{ name: "跟进中" }, { name: "已成交" }] },
      ],
    },
    feishu_bitable_table_update: {
      dry_run: true,
      app_token: "app_token",
      table_id: "table_id",
      name: "客户档案2026",
    },
    feishu_bitable_table_delete: {
      dry_run: true,
      app_token: "app_token",
      table_id: "table_id",
    },
    feishu_bitable_field_create: {
      dry_run: true,
      app_token: "app_token",
      table_id: "table_id",
      field: { name: "状态", type: "select", multiple: false, options: [{ name: "跟进中" }, { name: "已成交" }] },
    },
    feishu_bitable_field_delete: {
      dry_run: true,
      app_token: "app_token",
      table_id: "table_id",
      field_id: "field_id",
    },
    feishu_bitable_field_update: {
      dry_run: true,
      app_token: "app_token",
      table_id: "table_id",
      field_id: "field_id",
      field: { name: "客户状态", type: "select", multiple: false, options: [{ name: "跟进中" }, { name: "已成交" }] },
    },
    feishu_bitable_view_create: {
      dry_run: true,
      app_token: "app_token",
      table_id: "table_id",
      view: { name: "客户跟进视图", type: "grid" },
    },
    feishu_bitable_view_delete: {
      dry_run: true,
      app_token: "app_token",
      table_id: "table_id",
      view_id: "view_id",
    },
    feishu_bitable_view_rename: {
      dry_run: true,
      app_token: "app_token",
      table_id: "table_id",
      view_id: "view_id",
      name: "客户跟进视图2026",
    },
    feishu_bitable_view_set_filter: {
      dry_run: true,
      app_token: "app_token",
      table_id: "table_id",
      view_id: "view_id",
      filter: { logic: "and", conditions: [["状态", "==", "跟进中"]] },
    },
    feishu_bitable_view_set_sort: {
      dry_run: true,
      app_token: "app_token",
      table_id: "table_id",
      view_id: "view_id",
      sort: { sort_config: [{ field: "优先级", desc: true }] },
    },
    feishu_bitable_view_set_group: {
      dry_run: true,
      app_token: "app_token",
      table_id: "table_id",
      view_id: "view_id",
      group: { group_config: [{ field: "状态", desc: false }] },
    },
    feishu_bitable_view_get_visible_fields: {
      app_token: "app_token",
      table_id: "table_id",
      view_id: "view_id",
    },
    feishu_bitable_view_set_visible_fields: {
      dry_run: true,
      app_token: "app_token",
      table_id: "table_id",
      view_id: "view_id",
      visible_fields: ["客户名称", "状态", "负责人"],
    },
    feishu_bitable_view_get_card: {
      app_token: "app_token",
      table_id: "table_id",
      view_id: "view_id",
    },
    feishu_bitable_view_set_card: {
      dry_run: true,
      app_token: "app_token",
      table_id: "table_id",
      view_id: "view_id",
      card: { cover_field: "附件字段" },
    },
    feishu_bitable_view_get_timebar: {
      app_token: "app_token",
      table_id: "table_id",
      view_id: "view_id",
    },
    feishu_bitable_view_set_timebar: {
      dry_run: true,
      app_token: "app_token",
      table_id: "table_id",
      view_id: "view_id",
      timebar: { start_time: "开始时间", end_time: "结束时间", title: "任务名称" },
    },
    feishu_task_create: {
      dry_run: true,
      summary: "跟进客户",
      description: "由数字参谋 dry-run 生成",
    },
    feishu_task_subtask_create: {
      dry_run: true,
      parent_task_guid: "parent_task_guid",
      summary: "拆解子任务",
      description: "由数字参谋 dry-run 生成",
      assignees: ["ou_xxx"],
    },
    feishu_task_update: {
      dry_run: true,
      task_guid: "task_guid",
      summary: "更新后的任务标题",
      update_fields: ["summary"],
    },
    feishu_task_update_reminders: {
      dry_run: true,
      task_guid: "task_guid",
      relative_fire_minutes: [15],
    },
    feishu_calendar_create_event: {
      dry_run: true,
      calendar_id: "primary",
      summary: "经营例会",
      description: "由数字参谋 dry-run 预检",
      start_time: "2026-06-15T09:00:00+08:00",
      end_time: "2026-06-15T10:00:00+08:00",
      attendee_ids: ["ou_xxx"],
    },
    calendar_qa: {
      calendar_id: "primary",
      start_time: "2026-06-15T00:00:00+08:00",
      end_time: "2026-06-15T23:59:59+08:00",
      page_size: 50,
    },
    approval_qa: {
      limit: 20,
    },
    feishu_approval_task_query: {
      open_id: "ou_xxx",
      page_size: 20,
      page_token: "",
    },
    feishu_approval_instance_get: {
      instance_code: "instance_code",
      locale: "zh-CN",
    },
    feishu_approval_instance_initiated: {
      open_id: "ou_xxx",
      page_size: 20,
      page_token: "",
    },
    feishu_approval_attachment_download: {
      file_token: "file_token",
    },
    company_qa: {
      limit: 20,
    },
    owner_cockpit: {
      limit: 20,
    },
    domain_qa: {
      limit: 20,
    },
    general_chat: {},
    chat_qa: {
      limit: 20,
    },
    chat_summary: {
      limit: 20,
    },
    chat_tasks: {
      limit: 20,
    },
    personal_tasks: {
      limit: 20,
    },
    public_knowledge_qa: {
      limit: 20,
    },
    feishu_okr_cycle_list: {
      user_id: "ou_xxx",
      user_id_type: "open_id",
      page_size: 100,
    },
    feishu_okr_objective_list: {
      cycle_id: "cycle_id",
      user_id_type: "open_id",
      page_size: 100,
    },
    feishu_task_assign_members: {
      dry_run: true,
      task_guid: "task_guid",
      add_assignees: ["ou_xxx"],
      remove_assignees: [],
    },
    feishu_task_update_followers: {
      dry_run: true,
      task_guid: "task_guid",
      add_followers: ["ou_xxx"],
      remove_followers: [],
    },
    feishu_task_complete: {
      dry_run: true,
      task_guid: "task_guid",
    },
    feishu_task_reopen: {
      dry_run: true,
      task_guid: "task_guid",
    },
    feishu_task_delete: {
      dry_run: true,
      task_guid: "task_guid",
    },
    feishu_task_comment: {
      dry_run: true,
      task_guid: "task_guid",
      content: "处理进展",
    },
    feishu_task_upload_attachment: {
      dry_run: true,
      resource_id: "task_guid",
      resource_type: "task",
      file_path: "attachments/example.pdf",
    },
    task_qa: {
      page_size: 50,
      page_token: "",
    },
    bitable_qa: {
      app_token: "app_token",
      table_id: "table_id",
      page_size: 100,
      page_token: "",
      view_id: "",
      field_names: [],
    },
    feishu_doc_fetch: {
      doc: "docx_or_wiki_url_or_token",
      doc_format: "markdown",
      detail: "simple",
      scope: "keyword",
      keyword: "制度",
    },
    feishu_drive_search: {
      query: "制度",
      doc_types: ["docx", "wiki"],
      page_size: 10,
      sort: "edit_time",
    },
    feishu_drive_file_list: {
      page_size: 50,
      page_token: "",
      folder_token: "",
    },
    feishu_wiki_space_list: {
      page_size: 20,
      page_token: "",
    },
    feishu_wiki_node_list: {
      space_id: "space_id",
      parent_node_token: "",
      page_size: 50,
      page_token: "",
    },
    feishu_im_chat_search: {
      query: "经营",
      page_size: 20,
    },
    feishu_im_message_list: {
      chat_id: "oc_xxx",
      page_size: 20,
      page_token: "",
    },
    mail_qa: {
      user_mailbox_id: "me",
      folder_id: "INBOX",
      page_size: 20,
      page_token: "",
    },
    feishu_mail_folder_list: {
      user_mailbox_id: "me",
    },
    feishu_mail_message_get: {
      user_mailbox_id: "me",
      message_id: "message_id",
      format: "plain_text_full",
    },
    feishu_vc_meeting_search: {
      query: "复盘",
      meeting_status: 2,
      page_size: 20,
      page_token: "",
    },
    feishu_tasklist_create: {
      dry_run: true,
      name: "销售跟进清单",
      archive_tasklist: false,
      editors: ["ou_xxx"],
    },
    feishu_tasklist_update: {
      dry_run: true,
      tasklist_guid: "tasklist_guid",
      name: "销售跟进清单2026",
    },
    feishu_tasklist_delete: {
      dry_run: true,
      tasklist_guid: "tasklist_guid",
    },
    feishu_tasklist_update_members: {
      dry_run: true,
      tasklist_guid: "tasklist_guid",
      add_members: ["ou_xxx"],
      remove_members: [],
    },
    feishu_tasklist_set_members: {
      dry_run: true,
      tasklist_guid: "tasklist_guid",
      set_members: ["ou_xxx", "ou_yyy"],
    },
    feishu_task_section_create: {
      dry_run: true,
      name: "销售跟进",
      resource_type: "tasklist",
      resource_id: "tasklist_guid",
    },
    feishu_task_section_update: {
      dry_run: true,
      section_guid: "section_guid",
      name: "已完成跟进",
    },
    feishu_task_section_delete: {
      dry_run: true,
      section_guid: "section_guid",
    },
    feishu_task_add_to_tasklist: {
      dry_run: true,
      task_guid: "task_guid",
      tasklist_guid: "tasklist_guid",
      section_guid: "section_guid",
    },
    feishu_task_set_ancestor: {
      dry_run: true,
      task_guid: "task_guid",
      ancestor_guid: "parent_task_guid",
    },
    feishu_task_clear_ancestor: {
      dry_run: true,
      task_guid: "task_guid",
    },
    feishu_contact_department_children: {
      department_id: "0",
      department_id_type: "department_id",
      user_id_type: "open_id",
      page_size: 50,
    },
    feishu_contact_department_users: {
      department_id: "0",
      department_id_type: "department_id",
      user_id_type: "open_id",
      page_size: 50,
    },
    feishu_contact_scope_list: {
      department_id_type: "open_department_id",
      user_id_type: "open_id",
      page_size: 100,
    },
    feishu_contact_organization_snapshot: {
      root_department_id: "0",
      max_departments: 100,
      max_users: 500,
    },
    feishu_im_send_message: {
      dry_run: true,
      receive_id_type: "chat_id",
      receive_id: "oc_xxx",
      text: "经营事项提醒",
    },
    feishu_im_create_chat: {
      dry_run: true,
      name: "经营例会群",
      description: "由数字参谋 dry-run 预检",
      user_id_list: ["ou_xxx"],
      bot_id_list: ["cli_xxx"],
      chat_mode: "group",
      chat_type: "private",
    },
    feishu_im_auto_join_public_chats: {
      dry_run: true,
      query: "销售",
      chat_ids: [],
      limit: 20,
      max_pages: 2,
    },
    feishu_approval_task_approve: {
      dry_run: true,
      open_id: "ou_xxx",
      approval_code: "approval_code",
      instance_code: "instance_code",
      task_id: "task_id",
      comment: "由数字参谋 dry-run 预检。",
    },
    feishu_approval_task_reject: {
      dry_run: true,
      open_id: "ou_xxx",
      approval_code: "approval_code",
      instance_code: "instance_code",
      task_id: "task_id",
      comment: "由数字参谋 dry-run 预检。",
    },
    feishu_approval_instance_remind: {
      dry_run: true,
      open_id: "ou_xxx",
      instance_code: "instance_code",
      task_ids: ["task_id"],
      comment: "请尽快处理。",
    },
    feishu_approval_instance_cancel: {
      dry_run: true,
      open_id: "ou_xxx",
      instance_code: "instance_code",
    },
    feishu_approval_instance_cc: {
      dry_run: true,
      open_id: "ou_xxx",
      instance_code: "instance_code",
      cc_user_ids: ["ou_target"],
      user_id_type: "open_id",
      comment: "请关注该审批。",
    },
    feishu_approval_task_add_sign: {
      dry_run: true,
      open_id: "ou_xxx",
      approval_code: "approval_code",
      instance_code: "instance_code",
      task_id: "task_id",
      add_sign_user_ids: ["ou_target"],
      add_sign_type: 3,
      user_id_type: "open_id",
      comment: "请协同审批。",
    },
    feishu_approval_task_rollback: {
      dry_run: true,
      open_id: "ou_xxx",
      approval_code: "approval_code",
      instance_code: "instance_code",
      task_id: "task_id",
      node_ids: ["node_id"],
      comment: "退回修改。",
    },
    feishu_approval_task_transfer: {
      dry_run: true,
      open_id: "ou_xxx",
      approval_code: "approval_code",
      instance_code: "instance_code",
      task_id: "task_id",
      transfer_user_id: "ou_target",
      user_id_type: "open_id",
      comment: "由数字参谋 dry-run 预检转交。",
    },
    feishu_cli_status: {},
    feishu_cli_doctor: {},
  };
  return templates[toolName] || { dry_run: true };
}

function parseJsonTextarea(id, resultId) {
  const raw = value(id).trim();
  if (!raw) return {};
  try {
    return JSON.parse(raw);
  } catch (err) {
    showResult(resultId, `JSON 无法解析：${err.message}`);
    return null;
  }
}

async function executeSelectedTool(mode) {
  if (!state.selectedCompanyId) {
    showResult("toolExecutionResult", "请先选择公司。");
    return;
  }
  const toolName = value("selectedToolName");
  if (!toolName) {
    showResult("toolExecutionResult", "请先选择工具。");
    return;
  }
  const params = parseJsonTextarea("toolExecutionParamsJson", "toolExecutionResult");
  if (params === null) return;
  const isWriteTool = Boolean(state.selectedToolSupportsWrite);
  if (!isWriteTool) {
    delete params.dry_run;
    delete params.confirmed;
    delete params.confirmation_token;
  } else if (mode === "dry_run") {
    params.dry_run = true;
    delete params.confirmed;
    delete params.confirmation_token;
  } else {
    const token = value("toolExecutionConfirmationToken").trim();
    if (!token) {
      showResult("toolExecutionResult", "真实执行必须填写 dry-run 返回的 confirmation_token。");
      return;
    }
    params.dry_run = false;
    params.confirmed = true;
    params.confirmation_token = token;
  }
  setValue("toolExecutionParamsJson", JSON.stringify(params, null, 2));
  const result = await safeAction("toolExecutionResult", async () => api(
    `/api/v5/tools/${encodeURIComponent(toolName)}/execute?company_id=${encodeURIComponent(state.selectedCompanyId)}`,
    {
      method: "POST",
      body: JSON.stringify({
        question: `${isWriteTool ? (mode === "dry_run" ? "预演" : "确认执行") : "执行"} ${toolName}`,
        params,
        actor_open_id: value("toolExecutionActorOpenId") || null,
      }),
    },
  ));
  if (!result) return;
  const token = String(result.answer || "").match(/confirmation_token:\s*([A-Za-z0-9_-]+)/)?.[1];
  if (token) setValue("toolExecutionConfirmationToken", token);
  state.lastToolExecutionLogFilter = {
    action: result.metadata?.audit_action || state.selectedToolAuditAction || "",
    confirmed: result.metadata?.confirmed,
    status: result.status || "",
  };
  await loadToolExecutions();
}

async function viewToolSystemLogs() {
  if (!state.selectedCompanyId) {
    showResult("toolExecutionResult", "请先选择公司。");
    return;
  }
  const filter = state.lastToolExecutionLogFilter || {
    action: state.selectedToolAuditAction || "",
    confirmed: "",
    status: "",
  };
  setValue("systemLogCategory", "tool");
  setValue("systemLogAction", filter.action || "");
  setValue("systemLogStatus", filter.status || "");
  setValue("systemLogReason", "");
  setValue("systemLogUsedAgentRuntime", "");
  setValue("systemLogFinalAnswerOwner", "");
  setValue("systemLogRoutePath", "");
  setValue("systemLogConfirmed", filter.confirmed === true ? "true" : filter.confirmed === false ? "false" : "");
  setValue("systemLogConfirmationChecked", "");
  showSettingsTab("audit");
  await loadSystemLogs();
}

async function loadSyncRuns() {
  const data = await safeLoad(`/api/v5/resources/sync-runs${companyQuery("limit=50")}`);
  renderSyncRuns(data?.items || []);
}

async function quickSetup() {
  const appId = value("appId");
  const appSecret = value("appSecret");
  const payload = {
    company_name: value("companyName"),
    company_code: value("companyCode"),
    feishu_mailbox_id: value("mailboxId") || null,
    bot_admin_open_id: value("adminOpenId") || null,
  };
  if (appId && appSecret) {
    payload.feishu_app = {
      name: value("appName") || "飞书应用",
      app_id: appId,
      app_secret: appSecret,
      verification_token: value("verificationToken") || null,
      encrypt_key: value("encryptKey") || null,
      settings: { cli_profile: value("appCliProfile") || null },
    };
  }
  const result = await safeAction("quickSetupResult", async () => api("/api/onboarding/company-setup", {
    method: "POST",
    body: JSON.stringify(payload),
  }));
  if (result) {
    if (result.feishu_app?.id) {
      state.defaultAppConfigId = result.feishu_app.id;
      setValue("discoverAppConfigId", result.feishu_app.id);
      if (appId && appSecret) {
        await validateFeishuAppConfig({ target: "quickSetupResult" });
      }
    }
    await loadDashboardDefaults();
    await loadCompanies({ silent: true });
    await loadCurrentView();
  }
}

async function validateFeishuAppConfig(options = {}) {
  const target = options.target || "quickSetupResult";
  const appConfigId = await ensureDefaultAppConfigId(target);
  if (!appConfigId) {
    return null;
  }
  const result = await safeAction(target, async () => api(`/api/feishu/apps/${appConfigId}/tenant-access-token`, {
    method: "POST",
  }));
  if (result?.credential_status === "valid") {
    await loadDashboardDefaults();
    await loadEntrypoints();
  }
  return result;
}

async function discoverResources() {
  const appConfigId = await ensureDefaultAppConfigId();
  if (!appConfigId) {
    return;
  }
  const payload = {
    kinds: csv(value("discoverKinds")),
    mailbox_ids: lines(value("discoverMailboxes")),
    app_tokens: lines(value("discoverAppTokens")),
    bitable_tables: lines(value("discoverBitableTables")),
    document_ids: lines(value("discoverDocuments")),
    wiki_space_ids: lines(value("discoverWikiSpaces")),
    limit: Number(value("discoverLimit") || 50),
    include_local_mining: state.discoverIncludeLocal,
  };
  const result = await safeAction("discoverResult", async () => api(`/api/feishu/apps/${appConfigId}/resources/discover`, {
    method: "POST",
    body: JSON.stringify(payload),
  }));
  if (result?.queued) {
    renderDiscoveredItems([]);
    window.setTimeout(() => {
      loadResources();
      loadSyncRuns();
    }, 3000);
  } else {
    renderDiscoveredItems(result?.items || []);
  }
  await loadResources();
}

async function openFeishuUserOAuth() {
  if (!state.selectedCompanyId) {
    showResult("discoverResult", "请先选择公司。");
    return;
  }
  const openId = selectedUserIdentityOpenId();
  if (!openId) {
    showResult("discoverResult", "请填写员工飞书身份 open_id；这里仅用于本地 CLI 调试授权，员工正式授权请从大飞哥卡片进入。");
    return;
  }
  const result = await safeAction("discoverResult", async () => api("/api/user-identity/oauth/feishu/cli/start", {
    method: "POST",
    body: JSON.stringify({
      company_id: state.selectedCompanyId,
      open_id: openId,
    }),
  }));
  if (!result) return;
  setValue("feishuCliDeviceCode", result.device_code || "");
  renderFeishuCliAuthBoard(result);
}

async function completeFeishuCliUserAuth() {
  if (!state.selectedCompanyId) {
    showResult("discoverResult", "请先选择公司。");
    return;
  }
  const openId = selectedUserIdentityOpenId();
  const deviceCode = value("feishuCliDeviceCode");
  if (!openId || !deviceCode) {
    showResult("discoverResult", "请先点击“CLI 调试授权”，扫码授权后再完成 CLI 授权。员工正式授权请从大飞哥卡片进入。");
    return;
  }
  const result = await safeAction("discoverResult", async () => api("/api/user-identity/oauth/feishu/cli/complete", {
    method: "POST",
    body: JSON.stringify({
      company_id: state.selectedCompanyId,
      open_id: openId,
      device_code: deviceCode,
    }),
  }));
  if (!result) return;
  renderFeishuCliAuthBoard(result);
  await loadBotUsers();
}

async function probeUserIdentityTools() {
  if (!state.selectedCompanyId) {
    showResult("discoverResult", "请先选择公司。");
    return;
  }
  const openId = selectedUserIdentityOpenId();
  if (!openId) {
    showResult("discoverResult", "请填写员工飞书身份 open_id；个人能力探针必须绑定资源所有者本人。");
    return;
  }
  const probes = [
    ["feishu_approval_task_query", "有没有需要我处理的审批", "审批实时待办"],
    ["calendar_qa", "今天我的日程有哪些", "日程问答"],
    ["personal_tasks", "我的待办事项有哪些", "本人待办"],
  ];
  const results = [];
  showResult("discoverResult", `正在通过 Agent Runtime 探测 ${openId} 的个人能力边界...`);
  for (const [route, question, label] of probes) {
    const result = await safeAction("discoverResult", async () => api("/api/v5/agent/trace-preview", {
      method: "POST",
      body: JSON.stringify({
        company_id: state.selectedCompanyId,
        question,
        normalized_command: question,
        actor_role: "member",
        actor_access_scope: "personal",
        actor_domains: [],
        actor_display_name: value("agentPreviewDisplayName") || "员工 Agent",
        actor_open_id: openId,
      }),
    }));
    if (!result) return;
    results.push(userIdentityProbeSummary(result, route, label));
  }
  showResult("discoverResult", {
    probe_owner_open_id: openId,
    company_id: state.selectedCompanyId,
    runtime_path: "Console -> Agent Runtime -> Tool Router -> Tool -> source",
    final_answer_owner: "agent_runtime",
    items: results,
    next_action: results.some((item) => item.authorization_required)
      ? "仍有个人能力未授权；请先完成 CLI 授权，再重新运行探针。"
      : "个人能力已通过 Agent Runtime 探测，可继续做飞书机器人真实消息验证。",
  });
  await loadAgentTraces();
}

function userIdentityProbeSummary(result, expectedRoute, label) {
  const trace = result?.trace || {};
  const toolStep = (trace.steps || []).find((step) => step.kind === "tool") || {};
  const structured = toolStep.metadata?.structured_result || {};
  return {
    label,
    expected_route: expectedRoute,
    actual_route: trace.route_path,
    status: toolStep.status || "-",
    business_tool: structured.business_tool || toolStep.metadata?.business_tool || "-",
    authorization_required: structured.user_identity_authorization_required === true,
    required_resources: structured.required_user_identity_resources || [],
    data_source: structured.data_source || toolStep.metadata?.data_source || null,
    execution_source: structured.execution_source || toolStep.metadata?.execution_source || null,
    final_answer_owner: structured.final_answer_owner || toolStep.metadata?.final_answer_owner || "agent_runtime",
    answer_preview: (result.answer || "").slice(0, 180),
  };
}

function selectedUserIdentityOpenId() {
  const explicit = value("agentPreviewOpenId") || value("adminOpenId") || value("toolExecutionActorOpenId");
  if (explicit) return explicit;
  return state.botUsers.length === 1 ? state.botUsers[0]?.open_id || "" : "";
}

function renderFeishuCliAuthBoard(result) {
  const rows = [
    ["状态", result.status || "-", "User Identity 只按资源所有者本人授权范围开放"],
    ["CLI Profile", result.cli_profile || "-", "后续公司可使用各自独立 CLI Profile"],
    ["授权账号", result.identity_open_id || "-", result.identity_match === false ? "授权账号与资源所有者不一致" : "可识别时必须与当前 open_id 一致"],
    ["身份一致", result.identity_match === true ? "一致" : (result.identity_match === false ? "不一致" : "未返回 open_id"), "不一致时后端不会标记个人能力包为已授权"],
    ["授权范围", result.domains || "-", "一次整体授权，覆盖个人飞书、邮箱、钉钉、微信等用户级能力模型"],
    ["授权链接", result.verification_url || "-", "请用资源所有者本人账号打开或扫码"],
    ["完成方式", result.instruction || result.message || "授权完成后点击完成 CLI 授权", "系统不缓存 device_code，只用于本次完成登录"],
  ];
  setHtml("feishuCliAuthBoard", `
    ${rows.map(([label, status, detail]) => `
      <div class="architecture-row">
        <span>${escapeHtml(label)}</span>
        <strong>${escapeHtml(status)}</strong>
        <small>${escapeHtml(detail)}</small>
      </div>
    `).join("")}
    ${result.qr_code_data_url ? `
      <div class="architecture-row">
        <span>二维码</span>
        <strong><img alt="飞书 CLI 用户授权二维码" src="${escapeAttr(result.qr_code_data_url)}" style="width:160px;height:160px;border:1px solid #dee0e3;border-radius:8px;background:#fff;" /></strong>
        <small>扫码完成授权后，点击“完成 CLI 授权”。</small>
      </div>
    ` : ""}
  `);
}

async function loadFeishuUserAccounts() {
  const appConfigId = await ensureDefaultAppConfigId();
  if (!appConfigId) {
    return;
  }
  await safeAction("discoverResult", async () => api(`/api/feishu/apps/${appConfigId}/user-accounts`));
}

async function ensureDefaultAppConfigId(target = "discoverResult") {
  const current = value("discoverAppConfigId");
  if (current) return current;
  if (state.defaultAppConfigId) {
    setValue("discoverAppConfigId", state.defaultAppConfigId);
    return state.defaultAppConfigId;
  }
  showResult(target, "正在读取默认飞书应用...");
  const resolved = await resolveDefaultAppConfigId();
  if (resolved) {
    state.defaultAppConfigId = resolved;
    setValue("discoverAppConfigId", resolved);
    return resolved;
  }
  showResult(target, "未找到可用的飞书应用配置。请先在“公司与资源”里完成飞书应用配置，或刷新资源库后再试。");
  return "";
}

async function resolveDefaultAppConfigId() {
  const overview = await safeLoad("/api/dashboard/overview");
  if (overview?.default_app_config_id) return overview.default_app_config_id;

  const v5Resources = await safeLoad(`/api/v5/resources${companyQuery()}`);
  const v5AppConfigId = firstAppConfigId(v5Resources?.items || []);
  if (v5AppConfigId) return v5AppConfigId;

  const resourceFallback = await safeLoad(`/api/feishu/resources${companyQuery()}`);
  return firstAppConfigId(resourceFallback?.items || []);
}

function firstAppConfigId(items) {
  return (items || []).map((item) => item?.app_config_id).find(Boolean) || "";
}

function selectResource(item) {
  state.selectedResourceId = item.id;
  state.selectedResourceLabel = `${item.resource_type || item.legacy_resource_type || "resource"} / ${item.resource_name || item.resource_id}`;
  const label = document.getElementById("selectedResourceLabel");
  if (label) label.textContent = `已选择：${state.selectedResourceLabel}`;
}

async function syncSelectedResource() {
  if (!state.selectedResourceId) {
    showResult("discoverResult", "请先在右侧资源登记表里点击选择一个资源");
    return;
  }
  const payload = { limit: 20, max_pages: 3, extract_items: true, ...syncPolicyModes() };
  await safeAction("discoverResult", async () => api(`/api/v5/resources/${state.selectedResourceId}/sync`, {
    method: "POST",
    body: JSON.stringify(payload),
  }));
  await loadResources();
}

async function clearSelectedResourceBlock() {
  if (!state.selectedResourceId) {
    showResult("discoverResult", "请先在资源登记表里点击选择一个资源");
    return;
  }
  await safeAction("discoverResult", async () => api(`/api/v5/resources/${state.selectedResourceId}/clear-access-block`, {
    method: "POST",
  }));
  await loadResources();
}

async function retrySelectedResourceBlock() {
  if (!state.selectedResourceId) {
    showResult("discoverResult", "请先在治理建议或资源登记表里选择一个资源");
    return;
  }
  const payload = { limit: 20, max_pages: 3, extract_items: true, ...syncPolicyModes() };
  await safeAction("discoverResult", async () => api(`/api/v5/resources/${state.selectedResourceId}/retry-access-block`, {
    method: "POST",
    body: JSON.stringify(payload),
  }));
  await loadResources();
  await loadResourceSyncStatus();
  await loadResourceMonitoring();
}

async function setSelectedResourceAccessDecision(decision) {
  if (!state.selectedResourceId) {
    showResult("resourceSyncResult", "请先在治理建议或资源登记表里选择一个资源");
    return;
  }
  const labels = {
    business_group: "已标记为业务群",
    owner_confirmed: "已标记为负责人确认",
    do_not_connect: "已标记为不接入",
    ignore: "已忽略本次建议",
    reset: "已重置接入判断",
  };
  await safeAction("resourceSyncResult", async () => api(`/api/v5/resources/${state.selectedResourceId}/access-decision`, {
    method: "POST",
    body: JSON.stringify({ decision, note: labels[decision] || "" }),
  }));
  await loadResources();
  await loadResourceSyncStatus();
  await loadResourceMonitoring();
}

function syncPolicyModes() {
  return {
    large_document_mode: value("largeDocumentMode") || "index_only",
    bitable_mode: value("bitableMode") || "master_data_index",
    memory_mode: value("memoryMode") || "stable_facts_only",
    vector_mode: value("vectorMode") || "summaries_and_hot_knowledge",
  };
}

function setDiscoverPreset(kinds, includeLocal) {
  state.discoverIncludeLocal = includeLocal;
  document.getElementById("discoverKinds").value = kinds;
  showResult("discoverResult", `已切换发现范围：${kinds}\n本地挖掘：${includeLocal ? "包含" : "不包含"}\n点击“开始发现并登记”后，系统会自动查找可同步资源。`);
}

async function generateReport() {
  const companyId = state.selectedCompanyId || null;
  const payload = { report_date: value("reportDate"), company_id: companyId };
  await safeAction("reportsResult", async () => api("/api/reports/daily", {
    method: "POST",
    body: JSON.stringify(payload),
  }));
}

async function advisorChat() {
  if (!state.selectedCompanyId) {
    showResult("advisorChatResult", "请先选择公司。");
    return;
  }
  const question = value("advisorQuestion");
  if (!question) {
    showResult("advisorChatResult", "请输入问题。");
    return;
  }
  const result = await safeAction("advisorChatResult", async () => api("/api/v5/agent/trace-preview", {
    method: "POST",
    body: JSON.stringify({
      company_id: state.selectedCompanyId,
      question,
      normalized_command: question,
      actor_role: "owner",
      actor_access_scope: "company",
    }),
  }));
  if (result?.answer) showResult("advisorChatResult", result.answer);
  if (result?.trace) showResult("advisorTraceResult", result.trace);
  await loadAgentTraces();
}

async function loadAgentSettings() {
  if (!state.selectedCompanyId) {
    showResult("aiSettingsResult", "请先选择公司后配置 Agent。");
    return;
  }
  const data = await safeLoad(`/api/v5/agent/settings?company_id=${encodeURIComponent(state.selectedCompanyId)}`);
  const settings = data?.settings || {};
  setChecked("agentEnabled", settings.enabled !== false);
  setValue("agentDefaultModel", settings.default_model || "qwen-local");
  setChecked("agentPlannerEnabled", settings.planner_enabled === true);
  setValue("agentMaxToolCalls", settings.max_tool_calls ?? 4);
  setValue("agentMaxPlannerSteps", settings.max_planner_steps ?? 3);
  setValue("agentMemoryMode", settings.memory_mode || "stable_facts_only");
  setValue("agentAnswerStyle", settings.answer_style || "concise_business");
  setChecked("agentTraceEnabled", settings.trace_enabled !== false);
  setChecked("agentAllowWriteTools", settings.allow_write_tools !== false);
  setChecked("agentRequireWriteConfirmation", settings.require_write_confirmation !== false);
  showResult("aiSettingsResult", data || "暂无 Agent 设置");
}

async function saveAgentSettings() {
  if (!state.selectedCompanyId) {
    showResult("aiSettingsResult", "请先选择公司。");
    return;
  }
  const payload = {
    enabled: checked("agentEnabled"),
    default_model: value("agentDefaultModel") || "qwen-local",
    planner_enabled: checked("agentPlannerEnabled"),
    max_tool_calls: Number(value("agentMaxToolCalls") || 4),
    max_planner_steps: Number(value("agentMaxPlannerSteps") || 3),
    memory_mode: value("agentMemoryMode") || "stable_facts_only",
    answer_style: value("agentAnswerStyle") || "concise_business",
    trace_enabled: checked("agentTraceEnabled"),
    allow_write_tools: checked("agentAllowWriteTools"),
    require_write_confirmation: checked("agentRequireWriteConfirmation"),
  };
  const result = await safeAction("aiSettingsResult", async () => api(`/api/v5/agent/settings?company_id=${encodeURIComponent(state.selectedCompanyId)}`, {
    method: "PUT",
    body: JSON.stringify(payload),
  }));
  if (result?.settings) await loadAgentSettings();
}

async function loadAgentTraces() {
  if (!state.selectedCompanyId) {
    renderChips("agentTraceStats", {});
    renderTable("agentTraceTable", [], [], { emptyMessage: "请先选择公司" });
    return;
  }
  const params = new URLSearchParams({
    company_id: state.selectedCompanyId,
    limit: "50",
  });
  if (value("agentTraceRoutePath")) params.set("route_path", value("agentTraceRoutePath"));
  if (value("agentTraceAgentId")) params.set("agent_id", value("agentTraceAgentId"));
  if (value("agentTraceOwnerOpenId")) params.set("agent_owner_open_id", value("agentTraceOwnerOpenId"));
  const data = await safeLoad(`/api/v5/agent/traces?${params.toString()}`);
  renderChips("agentTraceStats", data?.counts || {});
  renderTable("agentTraceTable", data?.items || [], [
    ["agent_owner_display_name", "员工 Agent"],
    ["agent_owner_open_id", "员工飞书身份"],
    ["agent_id", "Agent ID"],
    ["agent_type", "Agent 类型"],
    ["shared_business_tool_count", "共享 Tool"],
    ["route_path", "路由"],
    ["reply_mode_label", "回复模式"],
    ["reply_mode_data_requirement", "数据需求"],
    ["reply_mode_enterprise_data_required", "企业数据"],
    ["reply_mode_pre_reply_required", "预回复"],
    ["route_scope", "范围"],
    ["data_access_scope", "数据边界"],
    ["personal_owner_open_id", "资源所有者"],
    ["company_data_allowed", "公司数据"],
    ["cross_user_data_allowed", "跨人数据"],
    ["route_reason", "原因"],
    ["semantic_intent", "语义"],
    ["step_count", "步骤"],
    ["supports_write", "写操作"],
    ["requires_dry_run", "需 dry-run"],
    ["write_policy", "写策略"],
    ["confirmed_execution_requires", "确认要求"],
    ["actor", "调用人"],
    ["status", "状态"],
    ["created_at", "时间"],
  ], {
    compactId: true,
    compactKeys: ["agent_id", "agent_owner_open_id"],
    emptyMessage: "暂无 Agent 执行轨迹",
    rowAction: showAgentTraceDetail,
  });
  showResult("agentTraceResult", {
    filters: {
      route_path: value("agentTraceRoutePath") || null,
      agent_id: value("agentTraceAgentId") || null,
      agent_owner_open_id: value("agentTraceOwnerOpenId") || null,
    },
    latest: (data?.items || []).slice(0, 3),
  });
}

function showAgentTraceDetail(item) {
  if (!item) return;
  showResult("agentTraceResult", {
    agent_identity: {
      agent_id: item.agent_id,
      agent_type: item.agent_type,
      owner_open_id: item.agent_owner_open_id,
      owner_display_name: item.agent_owner_display_name,
      shared_business_tool_count: item.shared_business_tool_count,
      contract: item.agent_identity,
    },
    route: {
      path: item.route_path,
      scope: item.route_scope,
      reason: item.route_reason,
      semantic_intent: item.semantic_intent,
      reply_mode: item.reply_mode,
      step_count: item.step_count,
      status: item.status,
    },
    data_boundary: {
      data_access_scope: item.data_access_scope,
      personal_owner_open_id: item.personal_owner_open_id,
      company_data_allowed: item.company_data_allowed,
      cross_user_data_allowed: item.cross_user_data_allowed,
      actor_context: item.actor_context,
    },
    write_policy: {
      supports_write: item.supports_write,
      requires_dry_run: item.requires_dry_run,
      write_policy: item.write_policy,
      confirmed_execution_requires: item.confirmed_execution_requires,
    },
    created_at: item.created_at,
  });
}

const agentReplyModePreviewPresets = {
  fast: {
    question: "你好，你是谁",
    role: "member",
    accessScope: "chat",
    displayName: "员工 Agent",
    routeHint: "general_chat",
  },
  normal: {
    question: "今天有什么会议",
    role: "member",
    accessScope: "personal",
    displayName: "员工 Agent",
    routeHint: "calendar_qa",
  },
  thinking: {
    question: "公司今天有什么风险",
    role: "owner",
    accessScope: "company",
    displayName: "Joon",
    routeHint: "company_qa",
  },
};

async function previewAgentReplyModePreset(mode) {
  const preset = agentReplyModePreviewPresets[mode] || agentReplyModePreviewPresets.thinking;
  setValue("agentPreviewQuestion", preset.question);
  setValue("agentPreviewRole", preset.role);
  setValue("agentPreviewAccessScope", preset.accessScope);
  if (!value("agentPreviewDisplayName")) setValue("agentPreviewDisplayName", preset.displayName);
  showResult("agentPreviewResult", {
    preset: mode,
    route_hint: preset.routeHint,
    next_action: "点击预演，或已自动按预设执行。",
  });
  return previewAgentReplyMode();
}

async function previewAgentReplyMode() {
  if (!state.selectedCompanyId) {
    showResult("agentPreviewResult", "请先选择公司。");
    return null;
  }
  const question = value("agentPreviewQuestion") || "公司今天有什么风险";
  const actorOpenId = value("agentPreviewOpenId");
  const payload = {
    company_id: state.selectedCompanyId,
    question,
    normalized_command: question,
    chat_id: value("agentPreviewChatId") || null,
    actor_role: value("agentPreviewRole") || "owner",
    actor_access_scope: value("agentPreviewAccessScope") || "company",
    actor_domains: ["finance", "sales", "rd", "delivery", "hr", "admin"],
    actor_display_name: value("agentPreviewDisplayName") || null,
    actor_open_id: actorOpenId || null,
  };
  const result = await safeAction("agentPreviewResult", async () => api("/api/v5/agent/trace-preview", {
    method: "POST",
    body: JSON.stringify(payload),
  }));
  if (!result) return null;
  const trace = result.trace || {};
  showResult("agentPreviewResult", {
    answer: result.answer,
    reply_mode: trace.reply_mode,
    thinking_preview: trace.thinking_preview,
    route_path: trace.route_path,
    route_scope: trace.route_scope,
    agent_identity: trace.agent_identity,
    actor_context: trace.actor_context,
    runtime_contract: trace.runtime_contract,
    tool_steps: (trace.steps || []).filter((step) => step.kind === "tool"),
  });
  await loadAgentTraces();
  return result;
}

async function loadAgentReplyModes(options = {}) {
  if (state.replyModes && options.silent) {
    renderReplyModeBoard("agentReplyModeBoard", state.replyModes.items || []);
    renderReplyModeRuntime(state.replyModes.runtime_boundary);
    return state.replyModes;
  }
  try {
    const data = await api("/api/v5/agent/reply-modes");
    state.replyModes = data;
    renderReplyModeBoard("agentReplyModeBoard", data.items || []);
    renderReplyModeRuntime(data.runtime_boundary);
    return data;
  } catch (err) {
    if (!options.silent) showResult("agentReplyModeRuntime", err.message);
    return state.replyModes;
  }
}

async function vectorSearch() {
  if (!state.selectedCompanyId) {
    showResult("reportsResult", "请先选择公司");
    return;
  }
  const payload = { company_id: state.selectedCompanyId, query: value("searchQuery"), limit: 10 };
  await safeAction("reportsResult", async () => api("/api/vector/search", {
    method: "POST",
    body: JSON.stringify(payload),
  }));
}

async function safeLoad(path) {
  if (state.databaseUnavailable && !path.startsWith("/api/v5/os/overview")) {
    console.warn("API load skipped while database is unavailable", path);
    return null;
  }
  try {
    return await api(path);
  } catch (err) {
    console.warn("API load failed", path, err.message);
    return null;
  }
}

function capabilityRegistryCompanyId() {
  return state.selectedCompanyId || state.defaultCompanyId || "";
}

async function loadCapabilityRegistry(options = {}) {
  const companyId = capabilityRegistryCompanyId();
  if (!companyId) return null;
  if (state.capabilityRegistry && state.capabilityRegistryCompanyId === companyId) {
    return state.capabilityRegistry;
  }
  const payload = await safeLoad(`/api/v5/capability-registry?company_id=${encodeURIComponent(companyId)}`);
  if (!payload) {
    if (!options.silent) console.warn("Capability registry unavailable; falling back to legacy console data.");
    return null;
  }
  state.capabilityRegistry = payload;
  state.capabilityRegistryCompanyId = companyId;
  return payload;
}

function capabilityCatalogCards(catalogPayload) {
  return (catalogPayload?.domains || []).map((domain) => {
    const capabilities = domain.capabilities || [];
    const available = capabilities.filter((item) => item.status === "available").length;
    const planned = capabilities.filter((item) => item.status !== "available").length;
    return [
      domain.domain_id,
      domain.label || domain.domain_id,
      domain.description || "",
      `${available}/${capabilities.length} 可用 · ${planned} 规划中`,
    ];
  });
}

function skillRegistryRows(skillRegistryPayload) {
  const rows = [];
  for (const capability of skillRegistryPayload?.capabilities || []) {
    for (const skill of capability.skills || []) {
      const bindings = skill.provider_bindings || [];
      const providerNames = bindings.map((item) => item.provider_name || item.provider_id).filter(Boolean);
      rows.push({
        tool_name: skill.skill_id,
        business_tool: capability.label || skill.capability_id,
        provider: providerNames[0] || "unbound",
        compatible_providers: providerNames,
        enabled: skill.status === "enabled",
        supports_write: Boolean(skill.requires_confirmation || skill.risk_level === "high"),
        required_permissions: bindings.flatMap((item) => item.permission_required || []),
        audit_action: skill.skill_id,
        business_tool_capabilities: [skill.label || skill.operation || skill.skill_id],
        provider_boundaries: {},
        config_json: {
          registry_source: "skill_registry_payload",
          domain_id: skill.domain_id,
          capability_id: skill.capability_id,
          skill_type: skill.skill_type,
          risk_level: skill.risk_level,
          runtime_supported: skill.runtime_supported,
          receipt_supported: skill.receipt_supported,
          provider_binding_count: skill.provider_binding_count,
        },
        registry_source: "skill_registry_payload",
      });
    }
  }
  return rows;
}

function registryCoverage(total, covered) {
  return total > 0 ? Number(((covered / total) * 100).toFixed(1)) : 0;
}

function logCapabilityRegistryDiff(scope, legacyPayload, registryPayload, coverage) {
  const diff = {
    scope,
    legacy: legacyPayload,
    registry: registryPayload,
    coverage,
    generated_at: state.capabilityRegistry?.generated_at || null,
  };
  state.capabilityRegistryDiffs.push(diff);
  console.info("[capability-registry-diff]", diff);
}

async function safeAction(target, fn) {
  if (state.databaseUnavailable) {
    showResult(target, "数据库未连接，操作已暂停。");
    return null;
  }
  showResult(target, "执行中...");
  try {
    const result = await fn();
    showResult(target, result);
    return result;
  } catch (err) {
    showResult(target, err.message);
    return null;
  }
}

function renderMetrics(data) {
  const moduleByKey = {};
  for (const item of data?.modules || []) moduleByKey[item.key] = item;
  const metric = (key, name, fallback = 0) => moduleByKey[key]?.metrics?.[name] ?? fallback;
  const cards = [
    ["今日事件", metric("today-focus", "today_events")],
    ["今日重点", moduleByKey["today-focus"]?.count],
    ["开放风险", metric("risks", "open", moduleByKey.risks?.count)],
    ["开放待办", metric("tasks", "open", moduleByKey.tasks?.count)],
    ["待决策", metric("decisions", "open", moduleByKey.decisions?.count)],
    ["审批动态", moduleByKey.approvals?.count],
    ["项目动态", moduleByKey.projects?.count],
    ["数据盲区", metric("resources", "data_blindspots")],
    ["同步异常", metric("resources", "sync_errors")],
    ["报告", moduleByKey.reports?.count],
  ];
  document.getElementById("metrics").innerHTML = cards
    .map(([label, value]) => `<div class="metric"><span>${escapeHtml(label)}</span><b>${value ?? 0}</b></div>`)
    .join("");
}

function renderOwnerCommandCenter(data, operatingState) {
  const moduleByKey = {};
  for (const item of data?.modules || []) moduleByKey[item.key] = item;
  const os = operatingState?.os || {};
  const total = os.total_counts || {};
  const companies = os.companies || [];
  const readiness = os.release_readiness || {};
  const syncCounts = operatingState?.syncStatus?.counts || {};
  const activeCompany = state.selectedCompanyId
    ? companies.find((company) => company.id === state.selectedCompanyId)
    : null;
  const companyLabel = activeCompany?.name || (state.selectedCompanyId ? "当前公司" : "全部公司");

  setHtml("commandCenterPills", [
    ["公司", total.companies ?? companies.length ?? 0],
    ["员工智能体", total.users ?? 0],
    ["WorkEvent", total.work_events ?? 0],
    ["数据源", total.resources ?? 0],
    ["同步异常", syncCounts.failed || syncCounts.error || 0],
    ["上线状态", readiness.status === "ready" ? "Ready" : "Degraded"],
  ].map(([label, value]) => `
    <div class="command-pill">
      <span>${escapeHtml(label)}</span>
      <strong>${escapeHtml(value)}</strong>
    </div>
  `).join(""));

  const todayItems = [
    ["今日重点", moduleByKey["today-focus"]?.summary || `${companyLabel} 暂无必须立即处理的经营重点`],
    ["风险", moduleByKey.risks?.summary || "暂无开放高优先级风险"],
    ["待办", moduleByKey.tasks?.summary || "暂无开放待办"],
    ["审批", moduleByKey.approvals?.summary || "暂无新的审批动态"],
    ["会议", moduleByKey.meetings?.summary || "暂无需要跟进的会议日程"],
  ];
  setHtml("ownerTodayBrief", todayItems.map(([label, value]) => `
    <div class="brief-row">
      <span>${escapeHtml(label)}</span>
      <strong>${escapeHtml(value)}</strong>
    </div>
  `).join(""));

  const agentItems = companies.length ? companies.map((company) => {
    const counts = company.counts || {};
    return `
      <div class="brief-row">
        <span>${escapeHtml(company.name)} (${escapeHtml(company.code)})</span>
        <strong>${escapeHtml(counts.users || 0)} 个员工智能体 · ${escapeHtml(counts.resources || 0)} 个数据源 · ${escapeHtml(counts.work_events || 0)} 个 WorkEvent</strong>
      </div>
    `;
  }) : [`
    <div class="brief-row">
      <span>固势</span>
      <strong>等待公司与飞书 App 初始化</strong>
    </div>
  `];
  setHtml("companyAgentList", agentItems.join(""));
}

function renderV5ArchitectureBoard(operatingState) {
  const os = operatingState?.os || {};
  const total = os.total_counts || {};
  const readiness = os.release_readiness || {};
  const entrypoints = os.entrypoints || {};
  const syncCounts = operatingState?.syncStatus?.counts || {};
  const tools = operatingState?.tools?.items || [];
  const enabledByFamily = {};
  for (const item of tools) {
    const family = item.business_tool;
    if (!family) continue;
    if (!enabledByFamily[family]) enabledByFamily[family] = { total: 0, enabled: 0, write: 0 };
    enabledByFamily[family].total += 1;
    if (item.enabled !== false) enabledByFamily[family].enabled += 1;
    if (item.supports_write) enabledByFamily[family].write += 1;
  }
  const catalogPayload = state.capabilityRegistry?.catalog_payload;
  const catalogCards = capabilityCatalogCards(catalogPayload);
  if (catalogCards.length) {
    const summary = catalogPayload.summary || {};
    logCapabilityRegistryDiff(
      "catalog_payload",
      {
        tool_families: businessToolFamilies.length,
        tools: tools.length,
      },
      {
        domains: summary.domain_count || catalogCards.length,
        capabilities: summary.capability_count || 0,
        visible_capabilities: summary.visible_capability_count || 0,
      },
      {
        catalog_coverage: registryCoverage(summary.capability_count || 0, summary.visible_capability_count || 0),
      },
    );
  }

  const dataLayers = [
    ["Operational Data", "审批、项目、客户、日历、群消息", `${total.resources || 0} 个资源 · ${syncCounts.failed || syncCounts.error || 0} 个同步异常`],
    ["Knowledge Data", "Wiki、Doc、制度、会议纪要", "L1 热知识本地索引 · L2 冷知识 token 登记 · L3 外部知识实时查询"],
    ["Memory", "用户偏好、长期上下文、Agent 记忆", `${total.users || 0} 个员工智能体上下文入口`],
    ["WorkEvent", "统一经营事件流", `${total.work_events || 0} 条事件沉淀`],
  ];
  setHtml("dataLayerBoard", dataLayers.map(([label, scope, status]) => `
    <div class="architecture-row">
      <span>${escapeHtml(label)}</span>
      <strong>${escapeHtml(scope)}</strong>
      <small>${escapeHtml(status)}</small>
    </div>
  `).join(""));

  const toolFamilyRows = catalogCards.length ? catalogCards : businessToolFamilies.map(([family, label, scope]) => {
    const stats = enabledByFamily[family];
    const status = stats
      ? `${stats.enabled}/${stats.total} 启用 · ${stats.write} 写能力`
      : "待绑定能力";
    return [family, label, scope, status];
  });
  setHtml("toolFamilyBoard", toolFamilyRows.map(([family, label, scope, status]) => {
    return `
      <div class="tool-family">
        <span>${escapeHtml(family)}</span>
        <strong>${escapeHtml(label)}</strong>
        <small>${escapeHtml(scope)}</small>
        <em>${escapeHtml(status)}</em>
      </div>
    `;
  }).join(""));

  const bot = entrypoints.feishu_bot || {};
  const identityBoundary = bot.identity_boundary || {};
  const admin = entrypoints.admin_console || {};
  const ios = entrypoints.ios_app || {};
  const entrypointRows = [
    ["大飞哥机器人", bot.ai_mode_enabled ? "AI 兜底已开" : "AI 兜底未开", bot.execution_boundary || "Agent Runtime -> Tool Router -> Tool -> 数据源/执行源；最终回复只由 Agent Runtime 生成"],
    ["员工 Agent", identityBoundary.agent_model || "每个飞书用户一个专属 Agent", identityBoundary.tool_model || "9 个业务 Tool 全局共享，数据按身份边界收口"],
    ["权限边界", identityBoundary.enterprise_resource_boundary || "App Identity + Company Scope + Role Scope", identityBoundary.digital_advisor_permission_policy || "只能收紧权限，不能突破飞书 App 或用户原始授权范围"],
    ["管理后台", admin.enabled ? "经营后台已启用" : "未启用", readiness.status === "ready" ? "上线状态 Ready" : `阻塞项 ${readiness.blockers?.length || 0} 个`],
    ["iOS App", ios.status || "planned", "后续移动入口"],
  ];
  setHtml("entrypointBoard", entrypointRows.map(([label, status, detail]) => `
    <div class="architecture-row">
      <span>${escapeHtml(label)}</span>
      <strong>${escapeHtml(status)}</strong>
      <small>${escapeHtml(detail)}</small>
    </div>
  `).join(""));
  renderReplyModeBoard("replyModeBoard", state.replyModes?.items || defaultReplyModes());
}

function renderReplyModeBoard(id, items, options = {}) {
  const rows = (items && items.length ? items : defaultReplyModes()).map((mode) => {
    const examples = (mode.route_examples || []).join(" / ");
    const contract = (mode.response_contract || []).join("；");
    return `
      <div class="reply-mode-card ${escapeAttr(mode.mode_id || "normal")}">
        <span>${escapeHtml(mode.label || mode.mode_id || "-")}</span>
        <strong>${escapeHtml(mode.data_requirement || "-")}</strong>
        <small>${escapeHtml(mode.trigger || "")}</small>
        <em>${escapeHtml(contract)}</em>
        ${options.compact ? "" : `<b>${escapeHtml(examples ? `示例路由：${examples}` : "")}</b>`}
      </div>
    `;
  });
  setHtml(id, rows.join(""));
}

function renderReplyModeRuntime(boundary) {
  if (!document.getElementById("agentReplyModeRuntime")) return;
  showResult("agentReplyModeRuntime", {
    chain: boundary?.chain || [
      "Feishu Message Gateway",
      "Agent Runtime",
      "Tool Router",
      "Tool",
      "Data/Execution Source",
      "Tool structured result",
      "Agent Runtime final answer",
    ],
    final_answer_owner: boundary?.final_answer_owner || "agent_runtime",
    tool_returns_structured_result: boundary?.tool_returns_structured_result ?? true,
  });
}

function defaultReplyModes() {
  return [
    {
      mode_id: "fast",
      label: "Fast（无需企业数据）",
      data_requirement: "纯推理 / 简单外部查询",
      trigger: "无需读取企业数据即可回答",
      response_contract: ["直接回答", "不伪装读取企业数据", "不展示思考路径"],
      route_examples: ["general_chat"],
    },
    {
      mode_id: "normal",
      label: "normal（实时企业数据）",
      data_requirement: "MCP/CLI / 单 Tool",
      trigger: "需要实时企业数据或单工具结果",
      response_contract: ["通过 Tool 获取实时企业数据", "简洁给出结果", "不展示思考路径"],
      route_examples: ["calendar_qa", "bitable_qa", "feishu_im_message_list"],
    },
    {
      mode_id: "thinking",
      label: "Thinking（需要分析数据）",
      data_requirement: "WorkEvent / Knowledge / Memory / 多 Tool 分析",
      trigger: "需要分析企业沉淀数据、长期记忆或多个工具结果",
      response_contract: ["回复栏先显示一个思考路径", "先说明使用了哪些数据层或工具", "再给结论、依据和下一步"],
      route_examples: ["company_qa", "domain_qa", "chat_summary", "mail_qa", "task_qa"],
    },
  ];
}

function renderOperatingCenter(os, syncStatus, tools) {
  const total = os?.total_counts || {};
  const companies = os?.companies || [];
  const syncCounts = syncStatus?.counts || {};
  const governanceActions = syncStatus?.governance_actions || [];
  const toolItems = tools?.items || [];
  const enabledTools = toolItems.filter((item) => item.enabled !== false).length;
  const writeTools = toolItems.filter((item) => item.supports_write).length;
  const entrypoints = os?.entrypoints || {};
  const botEntrypoint = entrypoints.feishu_bot || {};
  const readiness = os?.release_readiness || {};
  const readinessChecks = Object.fromEntries((readiness.checks || []).map((item) => [item.key, item]));
  const cliCheck = readinessChecks.feishu_cli || {};
  const databaseUnavailable = os?.status?.database === "unavailable";
  const companyLabel = state.selectedCompanyId ? "当前公司" : "全部公司";
  const backendReady = Boolean(os || syncStatus || tools);
  const subtitle = document.getElementById("operatingCenterSubtitle");
  subtitle.textContent = backendReady
    ? `${companyLabel} · 飞书是主数据源，数字参谋负责分析、记忆、工具执行和报告。`
    : `${companyLabel} · 数据服务待连接，页面结构已就绪。`;
  document.getElementById("operatingCenterMetrics").innerHTML = [
    ["公司", total.companies ?? companies.length],
    ["员工智能体", total.users],
    ["飞书资源", total.resources],
    ["工作事件", total.work_events],
    ["同步异常", syncCounts.failed || syncCounts.error || 0],
    ["待治理", governanceActions.length],
  ].map(([label, value]) => `
    <div class="operating-metric">
      <span>${escapeHtml(label)}</span>
      <strong>${escapeHtml(value ?? 0)}</strong>
    </div>
  `).join("");

  const signals = [
    {
      label: "上线检查",
      value: readiness.status === "ready"
        ? "核心入口与职责边界已就绪"
        : `降级可读，阻塞项 ${readiness.blockers?.length || 0} 个`,
      tone: readiness.status === "ready" ? "ok" : "warn",
    },
    {
      label: "数据服务",
      value: databaseUnavailable ? "数据库未连接，首屏降级可读" : "数据库连接正常",
      tone: databaseUnavailable ? "warn" : "ok",
    },
    {
      label: "飞书 CLI",
      value: cliCheck.detail || "等待 CLI 自检",
      tone: cliCheck.status === "ready" ? "ok" : "warn",
    },
    {
      label: "飞书连接",
      value: backendReady
        ? (total.resources ? `${total.resources} 个数据源已登记` : "等待登记飞书数据源")
        : "等待数据服务连接",
      tone: backendReady && total.resources ? "ok" : "warn",
    },
    {
      label: "同步健康",
      value: governanceActions.length ? `${governanceActions.length} 个动作需要处理` : "暂无关键治理动作",
      tone: governanceActions.length ? "warn" : "ok",
    },
    {
      label: "工具状态",
      value: state.selectedCompanyId ? `${enabledTools}/${toolItems.length} 个工具启用，${writeTools} 个写操作` : "选择公司后查看工具边界",
      tone: state.selectedCompanyId && toolItems.length ? "ok" : "neutral",
    },
    {
      label: "入口",
      value: botEntrypoint.ai_mode_enabled ? "大飞哥 AI 兜底已开 · 管理后台 · iOS 预留" : "大飞哥 AI 兜底未开 · 管理后台 · iOS 预留",
      tone: botEntrypoint.ai_mode_enabled ? "ok" : "warn",
    },
  ];
  document.getElementById("operatingCenterSignals").innerHTML = signals.map((signal) => `
    <div class="operating-signal ${signal.tone}">
      <span>${escapeHtml(signal.label)}</span>
      <strong>${escapeHtml(signal.value)}</strong>
    </div>
  `).join("");
}

function renderCockpitCards(data) {
  const moduleByKey = {};
  for (const item of data.modules || []) moduleByKey[item.key] = item;
  const cards = [
    {
      view: "today-focus",
      eyebrow: "今日重点",
      title: moduleCardTitle(moduleByKey["today-focus"], "条事件"),
      meta: moduleByKey["today-focus"]?.summary || "今日经营重点",
      tone: "blue",
    },
    {
      view: "risks",
      eyebrow: "风险预警",
      title: riskCardTitle(moduleByKey.risks),
      meta: moduleByKey.risks?.summary || "点击查看开放风险和优先级",
      tone: "red",
    },
    {
      view: "tasks",
      eyebrow: "待办事项",
      title: moduleCardTitle(moduleByKey.tasks, "条待办"),
      meta: moduleByKey.tasks?.summary || "点击查看负责人和截止时间",
      tone: "green",
    },
    {
      view: "approvals",
      eyebrow: "审批动态",
      title: moduleCardTitle(moduleByKey.approvals, "条动态"),
      meta: moduleByKey.approvals?.summary || "付款、报销、合同、采购等",
      tone: "amber",
    },
    {
      view: "projects",
      eyebrow: "项目动态",
      title: moduleCardTitle(moduleByKey.projects, "条动态"),
      meta: moduleByKey.projects?.summary || "项目、交付、研发和客户现场",
      tone: "violet",
    },
    {
      view: "decisions",
      eyebrow: "决策事项",
      title: moduleCardTitle(moduleByKey.decisions, "条决策"),
      meta: moduleByKey.decisions?.summary || "点击查看决策依据和动作",
      tone: "cyan",
    },
    {
      view: "communications",
      eyebrow: "消息邮件",
      title: moduleCardTitle(moduleByKey.communications, "条沟通"),
      meta: moduleByKey.communications?.summary || "飞书消息、群聊、邮件线索",
      tone: "blue",
    },
    {
      view: "meetings",
      eyebrow: "会议日程",
      title: moduleCardTitle(moduleByKey.meetings, "条日程"),
      meta: moduleByKey.meetings?.summary || "日程、会议和纪要同步状态",
      tone: "green",
    },
    {
      view: "settings",
      settingsTab: "resources",
      eyebrow: "数据覆盖",
      title: resourceCoverageTitle(moduleByKey.resources),
      meta: moduleByKey.resources?.summary || "系统可观察的数据范围和数据盲区",
      tone: "amber",
    },
    {
      view: "reports",
      eyebrow: "报告中心",
      title: moduleCardTitle(moduleByKey.reports, "份报告"),
      meta: moduleByKey.reports?.summary || "日报、周报、风险和决策报告",
      tone: "slate",
    },
  ];
  const el = document.getElementById("cockpitCards");
  el.innerHTML = cards.map((card) => `
    <button class="cockpit-card ${card.tone}" data-view="${escapeAttr(card.view)}"${card.settingsTab ? ` data-settings-target="${escapeAttr(card.settingsTab)}"` : ""}>
      <span>${escapeHtml(card.eyebrow)}</span>
      <strong>${escapeHtml(card.title)}</strong>
      <small>${escapeHtml(card.meta)}</small>
    </button>
  `).join("");
  el.querySelectorAll("[data-view]").forEach((button) => {
    button.addEventListener("click", () => {
      if (button.dataset.settingsTarget) showSettingsTab(button.dataset.settingsTarget);
      showView(button.dataset.view);
    });
  });
}

function renderOverviewGovernanceActions(data) {
  const el = document.getElementById("overviewGovernanceActions");
  if (!el) return;
  const resourcesModule = (data?.modules || []).find((item) => item.key === "resources") || {};
  const actions = (resourcesModule.metrics?.owner_actions || [])
    .filter((action) => action.action_code === "invite_bot_to_chats")
    .map((action) => ({
      ...action,
      resources: (action.resources || []).filter((resource) => {
        const label = resource.access_recommendation?.label || "";
        return ["建议接入", "已确认接入"].includes(label);
      }),
    }))
    .filter((action) => action.resources.length);
  if (!actions.length) {
    el.innerHTML = `<div class="empty">暂无需要你关注的关键群接入建议</div>`;
    return;
  }
  el.innerHTML = actions.map((action, index) => {
    const names = action.resources
      .map((resource) => resource.resource_name || resource.resource_id)
      .filter(Boolean)
      .slice(0, 4)
      .join("、");
    const targets = (action.recommended_notify_targets || []).filter((target) => target && target !== "不通知").join(" / ");
    return `
      <button class="overview-action" data-overview-governance-index="${index}">
        <span>${escapeHtml(action.title || "关键群接入")}</span>
        <strong>${escapeHtml(action.access_recommendation_summary || `${action.resources.length} 个建议`)}</strong>
        <small>${escapeHtml(names || action.business_impact || "关键经营群暂未接入")}</small>
        <em>${escapeHtml(targets ? `建议确认人：${targets}` : "先由业务负责人确认")}</em>
      </button>
    `;
  }).join("");
  el.querySelectorAll("[data-overview-governance-index]").forEach((button) => {
    button.addEventListener("click", () => {
      selectGovernanceAction(actions[Number(button.dataset.overviewGovernanceIndex)]);
    });
  });
}

function moduleCardTitle(module, suffix) {
  return `${module?.count ?? 0} ${suffix}`;
}

function riskCardTitle(module) {
  const open = module?.metrics?.open ?? module?.count ?? 0;
  const coverage = module?.metrics?.data_coverage_risks ?? 0;
  if (coverage) return `${open} 条开放风险 / ${coverage} 个数据盲区`;
  return `${open} 条开放风险`;
}

function resourceCoverageTitle(module) {
  const blindspots = module?.metrics?.data_blindspots ?? module?.metrics?.access_blocked ?? 0;
  const errors = module?.metrics?.sync_errors ?? 0;
  const pending = module?.metrics?.auto_sync_pending ?? 0;
  if (blindspots || errors) return `${blindspots} 个盲区 / ${errors} 个异常`;
  if (pending) return `${pending} 项等待自动同步`;
  return `${module?.count ?? 0} 项来源`;
}

function renderFoundationMetrics(data) {
  const counts = data?.total_counts || {};
  const cards = [
    ["公司", counts.companies],
    ["设置", counts.settings],
    ["资源", counts.resources],
    ["用户", counts.users],
    ["角色", counts.roles],
    ["权限", counts.permissions],
    ["工作事件", counts.work_events],
  ];
  document.getElementById("foundationMetrics").innerHTML = cards
    .map(([label, value]) => `<div class="metric"><span>${escapeHtml(label)}</span><b>${value ?? 0}</b></div>`)
    .join("");
}

function renderSyncRuns(items) {
  renderTable("syncRuns", items, [
    ["sync_action", "同步动作"],
    ["status", "状态"],
    ["items_seen", "看到"],
    ["items_indexed", "索引"],
    ["items_skipped", "跳过"],
    ["document_store_summary", "文档库"],
    ["rag_indexing_summary", "RAG 入队"],
    ["started_at", "开始时间"],
    ["error_message", "错误"],
  ]);
}

function renderCompanySelect(items) {
  const select = document.getElementById("companySelect");
  const current = state.selectedCompanyId;
  select.disabled = false;
  select.innerHTML = `<option value="">全部公司</option>` + items
    .map((item) => `<option value="${escapeAttr(item.id)}">${escapeHtml(item.name)} (${escapeHtml(item.code)})</option>`)
    .join("");
  if (current && items.some((item) => item.id === current)) {
    select.value = current;
  } else if (state.defaultCompanyId && items.some((item) => item.id === state.defaultCompanyId)) {
    state.selectedCompanyId = state.defaultCompanyId;
    select.value = state.selectedCompanyId;
  } else if (!current) {
    state.selectedCompanyId = "";
    select.value = "";
  }
}

function renderCompanyUnavailable() {
  state.companies = [];
  state.selectedCompanyId = "";
  const select = document.getElementById("companySelect");
  select.disabled = true;
  select.innerHTML = `<option value="">数据库未连接</option>`;
  select.value = "";
}

function renderChips(id, data) {
  const entries = Object.entries(data || {});
  document.getElementById(id).innerHTML = entries.length
    ? entries.map(([k, v]) => `<span class="chip">${escapeHtml(localizeCellValue(k))} · ${v}</span>`).join("")
    : `<div class="empty">暂无数据</div>`;
}

function countBy(items, key) {
  return (items || []).reduce((acc, item) => {
    const value = item?.[key] || "unknown";
    acc[value] = (acc[value] || 0) + 1;
    return acc;
  }, {});
}

function renderResourceStats(items) {
  const counts = {};
  for (const item of items || []) {
    const key = `${item.platform || "unknown"} / ${item.resource_type || "unknown"}`;
    counts[key] = (counts[key] || 0) + 1;
  }
  renderChips("resourceStats", counts);
}

function renderRiskStats(items) {
  const counts = { 全部: items.length };
  for (const item of items || []) {
    const status = localizeCellValue(item.status || "unknown");
    const priority = item.priority ? `优先级 ${localizeCellValue(item.priority)}` : "未分级";
    counts[status] = (counts[status] || 0) + 1;
    counts[priority] = (counts[priority] || 0) + 1;
  }
  renderChips("riskStats", counts);
}

function renderTaskStats(id, items) {
  const counts = { 全部: items.length };
  for (const item of items || []) {
    counts[localizeCellValue(item.status || "open")] = (counts[localizeCellValue(item.status || "open")] || 0) + 1;
    if (item.owner) counts[`负责人 ${item.owner}`] = (counts[`负责人 ${item.owner}`] || 0) + 1;
  }
  renderChips(id, counts);
}

function renderEventStats(id, events) {
  const counts = { 全部: events.length };
  for (const event of events || []) {
    counts[localizeCellValue(event.source || "unknown")] = (counts[localizeCellValue(event.source || "unknown")] || 0) + 1;
    counts[event.event_type || "unknown"] = (counts[event.event_type || "unknown"] || 0) + 1;
  }
  renderChips(id, counts);
}

function renderEventTable(id, events) {
  renderTable(id, events, [
    ["occurred_at", "时间"],
    ["source", "来源"],
    ["event_type", "类型"],
    ["title", "标题"],
    ["content_text", "摘要"],
  ]);
}

function summarizeRisks(items) {
  if (!items.length) {
    return "当前公司暂时没有开放风险。建议保持自动同步开启，继续覆盖审批、邮箱、重点群和会议纪要。";
  }
  const openItems = items.filter((item) => !["closed", "done", "resolved"].includes(String(item.status || "").toLowerCase()));
  const highItems = openItems.filter((item) => ["high", "urgent", "p0", "p1"].includes(String(item.priority || "").toLowerCase()));
  const lines = [
    `当前共 ${items.length} 条风险，其中开放 ${openItems.length} 条，高优先级 ${highItems.length} 条。`,
    "优先关注：",
  ];
  for (const item of (highItems.length ? highItems : openItems).slice(0, 8)) {
    lines.push(`- ${item.title}${item.owner ? `（负责人：${item.owner}）` : ""}`);
  }
  return lines.join("\n");
}

function summarizeTasks(items) {
  if (!items.length) return "当前没有开放待办。建议继续同步重点群、审批和邮件，系统会自动抽取任务。";
  const openItems = items.filter((item) => !["closed", "done", "resolved"].includes(String(item.status || "").toLowerCase()));
  const lines = [`当前共 ${items.length} 条任务，其中开放 ${openItems.length} 条。`, "优先处理："];
  for (const item of openItems.slice(0, 8)) {
    lines.push(`- ${item.title}${item.owner ? `（负责人：${item.owner}）` : ""}${item.due_at ? `，截止：${item.due_at}` : ""}`);
  }
  return lines.join("\n");
}

function summarizeDecisions(items) {
  if (!items.length) return "当前没有开放决策事项。建议继续同步会议纪要、审批和重点群，系统会自动沉淀决策。";
  const openItems = items.filter((item) => !["closed", "done", "resolved"].includes(String(item.status || "").toLowerCase()));
  const lines = [`当前共 ${items.length} 条决策事项，其中开放 ${openItems.length} 条。`, "需要确认："];
  for (const item of openItems.slice(0, 8)) {
    lines.push(`- ${item.title}${item.owner ? `（负责人：${item.owner}）` : ""}`);
  }
  return lines.join("\n");
}

function summarizeEvents(events, label) {
  if (!events.length) return `当前没有命中的${label}动态。可以先确认相关资源已经登记并开启自动同步。`;
  const lines = [`最近命中 ${events.length} 条${label}动态。`, "重点关注："];
  for (const event of events.slice(0, 8)) {
    lines.push(`- ${event.title || event.event_type}: ${shortText(event.content_text)}`);
  }
  return lines.join("\n");
}

function moduleSummary(module) {
  if (!module) return "暂无数据。";
  const lines = [module.summary || "暂无摘要。"];
  if (module.next_actions?.length) {
    lines.push("", "建议动作：");
    for (const action of module.next_actions) lines.push(`- ${action}`);
  }
  return lines.join("\n");
}

function renderDiscoveredItems(items) {
  renderTable("discoveredItemsTable", items || [], [
    ["resource_type", "类型"],
    ["resource_name", "名称"],
    ["id", "系统资源编号"],
    ["resource_id", "飞书资源 ID"],
    ["resource_sub_id", "子资源 ID"],
    ["legacy_external_id", "旧原始 ID"],
    ["resource_mapping", "资源映射"],
  ]);
}

function containsAny(text, keywords) {
  const normalized = String(text || "").toLowerCase();
  return keywords.some((keyword) => normalized.includes(String(keyword).toLowerCase()));
}

function shortText(text) {
  const value = String(text || "").replace(/\s+/g, " ").trim();
  return value.length > 90 ? `${value.slice(0, 90)}...` : value;
}

function renderTable(id, items, columns, options = {}) {
  const el = document.getElementById(id);
  if (!el) return;
  if (!items || !items.length) {
    el.innerHTML = `<div class="empty">${escapeHtml(options.emptyMessage || "暂无数据")}</div>`;
    return;
  }
  el.innerHTML = `
    <table>
      <thead><tr>${columns.map(([, label]) => `<th>${escapeHtml(label)}</th>`).join("")}</tr></thead>
      <tbody>
        ${items.map((item, index) => `<tr data-row-index="${index}" class="${options.rowAction ? "clickable-row" : ""}">${columns.map(([key]) => `<td>${formatCell(item[key], { compactId: options.compactId && (key === "id" || (options.compactKeys || []).includes(key)) })}</td>`).join("")}</tr>`).join("")}
      </tbody>
    </table>
  `;
  if (options.rowAction) {
    el.querySelectorAll("tbody tr").forEach((row) => {
      row.addEventListener("click", () => options.rowAction(items[Number(row.dataset.rowIndex)]));
    });
  }
}

function formatCell(value, options = {}) {
  if (value === true) return `<span class="status-ok">是</span>`;
  if (value === false) return `<span class="status-bad">否</span>`;
  if (value === null || value === undefined || value === "") return "-";
  if (typeof value === "object") return escapeHtml(JSON.stringify(value));
  const text = String(value);
  const localized = localizeCellValue(text);
  if (localized !== text) return escapeHtml(localized);
  if (options.compactId && text.length > 12) return `<span title="${escapeAttr(text)}">${escapeHtml(`${text.slice(0, 8)}...`)}</span>`;
  return escapeHtml(text.length > 120 ? `${text.slice(0, 120)}...` : text);
}

function localizeCellValue(value) {
  const mapping = {
    active: "启用",
    disabled: "停用",
    needs_setup: "待配置",
    realtime: "实时",
    realtime_or_near_realtime: "实时/准实时",
    scheduled_index: "定时索引",
    scheduled_chunking: "定时切片",
    index_only_on_change: "变更时只索引",
    derived_after_sync: "同步后提炼",
    derived_after_permission_filter: "权限过滤后生成",
    scheduled: "定时",
    manual: "手动",
    success: "成功",
    failed: "失败",
    error: "错误",
    warning: "警告",
    info: "信息",
    partial: "部分成功",
    skipped: "已跳过",
    running: "运行中",
    pending: "待处理",
    open: "待处理",
    total: "全部",
    unknown: "未知",
    approved: "已通过",
    rejected: "已拒绝",
    cancelled: "已取消",
    canceled: "已取消",
    closed: "已关闭",
    never_synced: "未同步",
    stale: "已过期",
    unsupported: "暂不支持",
    policy_excluded: "策略排除",
    manual_review_required: "需人工确认",
    high: "高",
    urgent: "紧急",
    medium: "中",
    low: "低",
    today_events: "今日事件",
    open_risks: "开放风险",
    open_tasks: "开放待办",
    open_decisions: "待决策",
    focus_items: "今日重点",
    auto_sync_healthy: "自动同步正常",
    auto_sync_pending: "等待自动同步",
    data_blindspots: "数据盲区",
    sync_errors: "同步异常",
    owner_final_decision: "需要老板拍板",
    evidence_needed: "需要补充依据",
    decision_record: "已沉淀记录",
    owner_decision: "需要你决策",
    assignee_action: "需要负责人推进",
    system_record: "系统记录",
    must_handle: "必须处理",
    monitor: "可关注",
    system_managed: "系统自动处理",
    pending_for_me: "待我审批",
    completed: "已完成",
    sync_only: "仅同步记录",
    approval_events: "审批动态",
    project_risk: "客户/交付风险",
    rd_progress: "研发测试进展",
    project_record: "普通项目记录",
    project_events: "项目动态",
    owner_reply: "需要你回应",
    business_attention: "需要关注",
    communication_record: "背景记录",
    communication_events: "沟通线索",
    meeting_decision: "需要决策",
    meeting_follow_up: "会后跟进",
    meeting_record: "背景日程",
    meeting_events: "会议日程",
    resources: "资源",
    tool: "工具",
    tools: "工具",
    agent: "Agent",
    gateway: "Gateway",
    sync: "同步",
    system: "系统",
    report: "报告",
    workspace: "工作空间",
    administration: "管理配置",
    enabled: "启用",
    disabled: "停用",
    feishu: "飞书",
    realtime_work_events: "实时工作事件",
    master_data_index: "主数据索引",
    knowledge_hot: "高频知识",
    knowledge_cold: "低频大型资料",
    long_term_memory: "长期记忆",
    qdrant_vectors: "语义检索索引",
    manual_review: "人工确认",
    realtime_event: "实时事件",
    document_index_only: "仅文档索引",
    knowledge_vectorize: "知识向量化",
    memory_extract_later: "稍后提炼记忆",
    lark_cli_first_then_work_event_cache: "飞书 CLI 优先 + 工作事件缓存",
    lark_cli_first_then_index: "飞书 CLI 优先 + 索引",
    lark_cli_realtime: "飞书 CLI 实时查询",
    external_search_realtime: "外部实时搜索",
    local_rag: "本地 RAG",
    memory_facts: "长期记忆事实",
    vector_search: "向量检索",
    none: "不查询",
    manual_review_required: "需要人工确认",
    access_blocked: "访问阻断",
    bot_not_in_chat: "机器人未入群",
    app_identity: "应用身份",
    app_identity_with_bot_membership: "应用身份 + 机器人入群",
    app_identity_with_auto_discovered_chat: "应用身份 + 自动群资源",
    app_identity_with_auto_discovered_approval_resource: "应用身份 + 自动审批资源",
    app_identity_with_auto_discovered_mailbox: "应用身份 + 自动邮箱资源",
    app_identity_with_mail_scope: "应用身份 + 邮箱权限",
    app_identity_or_user_authorization_with_auto_discovery: "应用身份/用户授权 + 自动发现",
    user_authorization_or_specific_calendar_id: "用户授权",
    app_identity_or_user_authorization: "应用身份或用户授权",
    app_identity_with_auto_discovered_bitable: "应用身份 + 自动多维表格",
    app_identity_with_auto_discovered_document: "应用身份 + 自动文档资源",
    app_identity_with_auto_discovered_wiki: "应用身份 + 自动知识库",
    app_identity_with_resource_id: "应用身份 + 自动资源",
    outbound_and_mentioned_events: "主动发送 + 被 @ 事件",
    skip: "跳过",
    ready: "可执行",
    index_only: "只做索引",
    stable_facts_only: "只存稳定事实",
    summaries_and_hot_knowledge: "摘要和高频知识",
  };
  return mapping[value] || value;
}

function showResult(id, data) {
  const el = document.getElementById(id);
  if (!el) return;
  el.textContent = typeof data === "string" ? data : JSON.stringify(data, null, 2);
}

function companyQuery(extra = "") {
  const params = new URLSearchParams(extra);
  if (state.selectedCompanyId) params.set("company_id", state.selectedCompanyId);
  const text = params.toString();
  return text ? `?${text}` : "";
}

function setToday() {
  document.getElementById("reportDate").value = new Date().toISOString().slice(0, 10);
}

function fillDiscoveryDefaults() {
  const appConfigInput = document.getElementById("discoverAppConfigId");
  const mailboxesInput = document.getElementById("discoverMailboxes");
  if (appConfigInput && !appConfigInput.value && state.defaultAppConfigId) {
    appConfigInput.value = state.defaultAppConfigId;
  }
  if (mailboxesInput && !mailboxesInput.value && state.defaultMailboxId) {
    mailboxesInput.value = state.defaultMailboxId;
  }
}

function prefillQuickSetupFromOverview(os) {
  if (!os || os.status?.database === "unavailable") return;
  const companies = os.companies || [];
  const company = state.selectedCompanyId
    ? companies.find((item) => item.id === state.selectedCompanyId)
    : (companies.length === 1 ? companies[0] : null);
  if (company) {
    setDefaultInputValue("companyName", company.name || "");
    setDefaultInputValue("companyCode", company.code || "");
  }
  const checks = Object.fromEntries(((os.release_readiness || {}).checks || []).map((item) => [item.key, item]));
  const appResolved = checks.feishu_cli?.identity_status?.app_resolved?.message || "";
  const appId = appResolved.match(/app:\s*([^\s(]+)/)?.[1] || "";
  setDefaultInputValue("appName", "固势大飞哥");
  setDefaultInputValue("appCliProfile", "v5-local-prod");
  setDefaultInputValue("appId", appId);
  setDefaultInputValue("adminOpenId", "ou_6d06c92f4dab6f8626735b94f2d55bd2");
}

function setDefaultInputValue(id, value) {
  const el = document.getElementById(id);
  if (el && !el.value && value) el.value = value;
}

function value(id) {
  return document.getElementById(id).value.trim();
}

function setHtml(id, html) {
  const el = document.getElementById(id);
  if (el) el.innerHTML = html;
}

function setText(id, text) {
  const el = document.getElementById(id);
  if (el) el.textContent = text ?? "";
}

function setValue(id, value) {
  const el = document.getElementById(id);
  if (el) el.value = value ?? "";
}

function checked(id) {
  return Boolean(document.getElementById(id)?.checked);
}

function setChecked(id, value) {
  const el = document.getElementById(id);
  if (el) el.checked = Boolean(value);
}

function csv(text) {
  return text.split(",").map((item) => item.trim()).filter(Boolean);
}

function lines(text) {
  return text.split("\n").map((item) => item.trim()).filter(Boolean);
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function escapeAttr(value) {
  return escapeHtml(value);
}
