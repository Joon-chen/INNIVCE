const state = {
  items: [],
  selectedIndex: -1,
  pendingAction: null,
  chatId: "",
  initialIndex: -1,
  detailOnly: false,
  riskFilter: "all",
};

const $ = (id) => document.getElementById(id);

document.addEventListener("DOMContentLoaded", () => {
  const params = new URLSearchParams(location.search);
  const sidepanelMode = document.body.classList.contains("sidepanel-mode");
  state.chatId = params.get("chat_id") || "";
  state.initialIndex = params.has("item") ? Math.max(0, Number(params.get("item") || "0") - 1) : -1;
  state.detailOnly = params.get("view") === "detail" || params.has("item");
  state.riskFilter = normalizeRiskFilter(params.get("risk") || "all");
  if (state.detailOnly) document.body.classList.add("detail-page-mode");
  $("appConfigId").value = params.get("app_config_id") || localStorage.getItem("portal.appConfigId") || "";
  $("openId").value = params.get("open_id") || localStorage.getItem("portal.openId") || "";
  $("adminToken").value = localStorage.getItem("portal.adminToken") || "";
  $("refreshBtn").addEventListener("click", loadApprovals);
  $("confirmCancel").addEventListener("click", closeConfirmSheet);
  $("confirmSubmit").addEventListener("click", submitConfirmedAction);
  bootstrapFromChat(state.chatId).then(() => {
    if (sidepanelMode || params.get("autoload") === "1") {
      loadApprovals();
    }
  });
});

async function bootstrapFromChat(chatId) {
  if (!chatId || ($("appConfigId").value.trim() && $("openId").value.trim())) return;
  try {
    const res = await fetch(`/api/portal/bootstrap?chat_id=${encodeURIComponent(chatId)}`);
    const data = await res.json();
    if (!data.available) return;
    $("appConfigId").value = data.app_config_id || "";
    $("openId").value = data.open_id || "";
    remember();
    setStatus(data.display_name ? `已识别：${data.display_name}。可以刷新待审批。` : "已从机器人会话识别身份，可以刷新待审批。");
  } catch (_error) {
    setStatus("暂未能从机器人会话识别身份，请手动填写参数。");
  }
}

function remember() {
  localStorage.setItem("portal.appConfigId", $("appConfigId").value.trim());
  localStorage.setItem("portal.openId", $("openId").value.trim());
  localStorage.setItem("portal.adminToken", $("adminToken").value.trim());
}

function headers() {
  const token = $("adminToken").value.trim();
  return {
    "Content-Type": "application/json",
    ...(token ? { "X-Admin-Token": token } : {}),
  };
}

async function postJson(path, body) {
  const res = await fetch(path, { method: "POST", headers: headers(), body: JSON.stringify(body) });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail ? JSON.stringify(data.detail) : res.statusText);
  return data;
}

async function loadApprovals() {
  remember();
  const appConfigId = $("appConfigId").value.trim();
  const openId = $("openId").value.trim();
  if (!appConfigId || !openId) {
    setStatus("请先填写 app_config_id 和 open_id。");
    return;
  }
  const cacheShown = await loadCachedApprovals(appConfigId, openId);
  if (state.detailOnly && cacheShown) {
    setStatus("已显示最近审批详情。");
    return;
  }
  setStatus(cacheShown ? "已先显示最近结果，正在刷新..." : "正在读取待审批...");
  if (!cacheShown) {
    $("approvalList").innerHTML = "";
    $("approvalDetail").className = "empty";
    $("approvalDetail").textContent = "正在加载。";
  }
  try {
    const data = await postJson("/api/portal/approvals/pending", {
      app_config_id: appConfigId,
      open_id: openId,
      chat_id: state.chatId,
      limit: 20,
    });
    state.items = Array.isArray(data.items) ? data.items : [];
    state.selectedIndex = resolveInitialSelectedIndex();
    renderList();
    renderDetail();
    setStatus(state.items.length ? `读取到 ${state.items.length} 条待审批。` : "暂无待审批。");
  } catch (error) {
    setStatus(cacheShown ? "实时刷新失败，已显示最近结果。" : `读取失败：${error.message}`);
    if (!cacheShown) {
      $("approvalDetail").className = "empty";
      $("approvalDetail").textContent = "读取失败。";
    }
  }
}

async function loadCachedApprovals(appConfigId, openId) {
  if (!state.chatId) return false;
  try {
    const data = await postJson("/api/portal/approvals/cached", {
      app_config_id: appConfigId,
      open_id: openId,
      chat_id: state.chatId,
      limit: 20,
    });
    if (!data.available || !Array.isArray(data.items) || !data.items.length) return false;
    state.items = data.items;
    state.selectedIndex = resolveInitialSelectedIndex();
    renderList();
    renderDetail();
    return true;
  } catch (_error) {
    return false;
  }
}

function setStatus(text) {
  $("status").textContent = text;
}

function renderList() {
  if (state.detailOnly) {
    $("approvalList").innerHTML = "";
    return;
  }
  const entries = filteredApprovalEntries();
  const counts = approvalRiskCounts();
  $("approvalList").innerHTML = `
    <div class="filter-summary">
      <strong>${escapeHtml(riskFilterTitle())}</strong>
      <span>高风险 ${counts.hold} ｜ 需关注 ${counts.review} ｜ 可通过 ${counts.pass}</span>
    </div>
    ${entries.length ? entries.map(({ item, index }) => {
    const advice = refinedAdviceOf(item);
    return `
      <button class="item ${index === state.selectedIndex ? "active" : ""}" data-index="${index}">
        <strong>${escapeHtml(titleOf(item))}</strong>
        <span>${escapeHtml([amountOf(item), applicantOf(item)].filter(Boolean).join("｜") || "无摘要")}</span>
        <span>${escapeHtml(advice.reason)}</span>
      </button>
    `;
  }).join("") : `<div class="empty-list">当前分类没有待审批。</div>`}
  `;
  document.querySelectorAll(".item").forEach((button) => {
    button.addEventListener("click", () => {
      state.selectedIndex = Number(button.dataset.index);
      renderList();
      renderDetail();
    });
  });
}

function resolveInitialSelectedIndex() {
  if (!state.items.length) return -1;
  if (state.initialIndex >= 0 && state.initialIndex < state.items.length) return state.initialIndex;
  const first = filteredApprovalEntries()[0];
  return first ? first.index : -1;
}

function filteredApprovalEntries() {
  return state.items
    .map((item, index) => ({ item, index }))
    .filter(({ item }) => state.riskFilter === "all" || approvalRiskGroup(item) === state.riskFilter);
}

function approvalRiskCounts() {
  return state.items.reduce((counts, item) => {
    counts[approvalRiskGroup(item)] += 1;
    return counts;
  }, { hold: 0, review: 0, pass: 0 });
}

function approvalRiskGroup(item) {
  const riskLevel = String(item.assessment?.risk_level || item.risk_level || "").toLowerCase();
  if (["pass", "low"].includes(riskLevel)) return "pass";
  if (["high", "critical"].includes(riskLevel)) return "hold";
  if (["review", "medium", "pending"].includes(riskLevel)) return "review";
  const suggestion = String(suggestionOf(item) || "");
  if (["可通过", "可初步通过"].includes(suggestion)) return "pass";
  if (["拒绝", "补充后再审"].includes(suggestion)) return "hold";
  return "review";
}

function normalizeRiskFilter(value) {
  return ["hold", "review", "pass", "all"].includes(value) ? value : "all";
}

function riskFilterTitle() {
  if (state.riskFilter === "hold") return "高风险项";
  if (state.riskFilter === "review") return "需关注项";
  if (state.riskFilter === "pass") return "可通过项";
  return "全部待审批";
}

function renderDetail() {
  const item = state.items[state.selectedIndex];
  if (!item) {
    $("approvalDetail").className = "empty";
    $("approvalDetail").textContent = "暂无待审批。";
    return;
  }
  $("approvalDetail").className = "";
  const advice = refinedAdviceOf(item);
  $("approvalDetail").innerHTML = `
    <div class="detail-body approval-compact">
      <div class="approval-head">
        <div>
          <h2>${escapeHtml(titleOf(item))}</h2>
          <p>${escapeHtml(applicantOf(item) || "未知")} · ${escapeHtml(serialOf(item) || "单号未识别")}</p>
        </div>
        <strong>${escapeHtml(amountOf(item) || "未识别")}</strong>
      </div>
      <div class="advice">
        <strong>${escapeHtml(advice.suggestion)}</strong>
        <div>${escapeHtml(advice.reason)}</div>
      </div>
      ${renderBusinessSummary(item)}
      ${renderAttachments(item)}
      <label>
        处理意见
        <textarea id="comment" rows="3" placeholder="通过可不填；拒绝请填写理由。"></textarea>
      </label>
    </div>
    <div class="actions">
      <button id="approveBtn">同意</button>
      <button id="rejectBtn" class="danger">拒绝</button>
      <button class="secondary" disabled>转交</button>
      <button class="secondary" disabled>加签</button>
    </div>
  `;
  $("approveBtn").addEventListener("click", () => submitAction("approve"));
  $("rejectBtn").addEventListener("click", () => submitAction("reject"));
}

async function submitAction(action) {
  const item = state.items[state.selectedIndex];
  if (!item) return;
  const comment = $("comment").value.trim() || (action === "approve" ? "同意" : "");
  if (action === "reject" && !comment) {
    setStatus("拒绝前请填写理由。");
    return;
  }
  state.pendingAction = { action, comment, item, index: state.selectedIndex };
  $("confirmTitle").textContent = `确认${action === "approve" ? "同意" : "拒绝"}`;
  $("confirmSummary").textContent = `${titleOf(item)}｜${applicantOf(item) || "未知"}｜${amountOf(item) || "金额未识别"}｜意见：${comment}`;
  $("confirmSheet").classList.remove("hidden");
}

function closeConfirmSheet() {
  state.pendingAction = null;
  $("confirmSheet").classList.add("hidden");
}

async function submitConfirmedAction() {
  const pending = state.pendingAction;
  if (!pending) return;
  const { action, comment, item } = pending;
  closeConfirmSheet();
  const appConfigId = $("appConfigId").value.trim();
  const openId = $("openId").value.trim();
  const payload = actionPayload(item, action, comment, openId, { dry_run: true, confirmed: false });
  setStatus("正在执行 dry-run...");
  try {
    const dryRun = await postJson("/api/portal/approvals/action", payload);
    const token = confirmationToken(dryRun);
    if (!token) {
      setStatus("dry-run 成功，但未拿到确认令牌，暂不真实提交。");
      return;
    }
    const confirmed = await postJson(
      "/api/portal/approvals/action",
      actionPayload(item, action, comment, openId, { dry_run: false, confirmed: true, confirmation_token: token }),
    );
    if (!confirmed.ok) {
      setStatus(`提交失败：${confirmed.error || confirmed.answer || "未知错误"}`);
      return;
    }
    state.items.splice(state.selectedIndex, 1);
    state.selectedIndex = Math.min(state.selectedIndex, state.items.length - 1);
    renderList();
    renderDetail();
    setStatus(state.detailOnly ? "已提交，这条审批已处理。" : "已提交，列表已更新。");
  } catch (error) {
    setStatus(`提交失败：${error.message}`);
  }
}

function actionPayload(item, action, comment, openId, flags) {
  return {
    open_id: openId,
    app_config_id: $("appConfigId").value.trim(),
    chat_id: state.chatId,
    approval_code: item.approval_code || item.definition_code || "",
    instance_code: item.instance_code || item.process_code || "",
    task_id: item.task_id || item.id || "",
    action,
    comment,
    ...flags,
  };
}

function confirmationToken(result) {
  const text = [result.answer, result.error, JSON.stringify(result.metadata || {})].filter(Boolean).join("\n");
  return (text.match(/confirmation_token:\s*([^\s]+)/) || [])[1] || "";
}

function titleOf(item) {
  const title = item.approval_name || item.definition_name || item.title || "审批";
  if (/reserve fund/i.test(title)) return "借款申请";
  if (/reimbursement/i.test(title)) return "费用报销";
  return title;
}

function rawOf(item) {
  return item.raw && typeof item.raw === "object" ? item.raw : item;
}

function detailOf(item) {
  const raw = rawOf(item);
  if (item.instance_detail && typeof item.instance_detail === "object") return item.instance_detail;
  return raw.instance_detail && typeof raw.instance_detail === "object" ? raw.instance_detail : {};
}

function applicantOf(item) {
  const raw = rawOf(item);
  const detail = detailOf(item);
  const applicant = detail.applicant && typeof detail.applicant === "object" ? detail.applicant : {};
  return item.applicant_name || item.user_name || item.applicant || item.user_id
    || raw.applicant_name || raw.user_name || raw.applicant || raw.user_id
    || detail.applicant_name || detail.user_name || applicant.name || applicant.open_id || "";
}

function amountOf(item) {
  const raw = rawOf(item);
  const value = item.amount || item.total_amount || item.form_amount
    || raw.amount || raw.total_amount || raw.form_amount
    || fieldValue(item, ["费用汇总", "金额", "报销金额", "借款金额", "申请金额", "付款金额"]) || "";
  return value ? `${value}元` : "";
}

function serialOf(item) {
  const raw = rawOf(item);
  const detail = detailOf(item);
  const instance = raw.instance && typeof raw.instance === "object" ? raw.instance : {};
  return item.serial_number || raw.serial_number || detail.serial_number
    || item.instance_code || item.process_code || raw.instance_code || raw.process_code
    || detail.instance_code || detail.process_code || instance.code || instance.instance_code || item.id || raw.id || "";
}

function suggestionOf(item) {
  return item.assessment?.suggestion || item.suggestion || "建议查看";
}

function reasonOf(item) {
  return item.assessment?.reason || item.reason || item.description || "暂无判断理由。";
}

function refinedAdviceOf(item) {
  const amountCheck = detailAmountCheck(item);
  const base = {
    suggestion: suggestionOf(item),
    reason: cleanAdviceReason(reasonOf(item), amountCheck),
  };
  if (amountCheck?.allowanceAmount > 1 && amountCheck?.days) {
    base.suggestion = "可通过，需留意补贴标准";
    base.reason = "金额结构可解释，需核对补贴标准和业务背景。";
  }
  const company = fieldValue(item, ["费用承担公司", "承担公司", "付款公司", "公司"]) || "";
  const invoiceBuyers = invoiceBuyerNames(item);
  if (!invoiceBuyers.length) return base;
  if (!company) {
    return {
      ...base,
      reason: appendSentence(base.reason, `发票抬头已识别为 ${invoiceBuyers.join("、")}，但未识别到费用承担公司。`),
    };
  }
  const matchedBuyer = invoiceBuyers.find((buyer) => companyNameMatches(buyer, company));
  if (matchedBuyer) {
    return {
      ...base,
      reason: appendSentence(base.reason, `发票抬头 ${matchedBuyer} 与费用承担公司一致。`),
    };
  }
  return {
    suggestion: "需核对抬头",
    reason: appendSentence(base.reason, `发票抬头 ${invoiceBuyers.join("、")} 与费用承担公司 ${company} 不一致。`),
  };
}

function detailAmountCheck(item) {
  const attachments = attachmentResults(item);
  if (!attachments.length) return null;
  const fields = Object.fromEntries(formFields(item));
  const evidenceText = attachments.map((attachment) => `${attachment.name || ""} ${attachment.text_preview || attachment.summary || ""}`).join(" ");
  const haystack = `${evidenceText} ${Object.keys(fields).join(" ")} ${Object.values(fields).join(" ")}`;
  return amountReconciliation(item, attachments, haystack);
}

function invoiceBuyerNames(item) {
  return [...new Set(attachmentResults(item).map((attachment) => {
    const name = attachment.name || "";
    const text = `${name} ${attachment.text_preview || attachment.summary || ""}`;
    if (inferAttachmentType(name, text) !== "发票") return "";
    return extractInvoiceBuyer(text);
  }).filter(Boolean))];
}

function extractInvoiceBuyer(text) {
  return firstAfter(text, /购买方信息名称[:：]?\s*([^\s|，,]{4,60}(?:公司|有限公司)?)/)
    || firstAfter(text, /购买方[^\u4e00-\u9fa5]{0,12}([\u4e00-\u9fa5（）()]{4,60}(?:公司|有限公司)?)/);
}

function companyNameMatches(left, right) {
  const a = normalizeCompanyName(left);
  const b = normalizeCompanyName(right);
  return Boolean(a && b && (a.includes(b) || b.includes(a)));
}

function normalizeCompanyName(value) {
  return String(value || "").replace(/[（）()\s，,。；;:：|]/g, "");
}

function cleanAdviceReason(reason, amountCheck) {
  let output = String(reason || "")
    .replace(/，?建议确认发票抬头与公司一致。?/g, "")
    .replace(/，?建议确认发票抬头。?/g, "")
    .replace(/，?建议确认费用归属。?/g, "")
    .trim();
  if (amountCheck?.allowanceAmount > 1) {
    output = output
      .replace(/，?建议确认发票明细。?/g, "")
      .replace(/，?建议确认发票。?/g, "");
  }
  return output || "暂无判断理由。";
}

function appendSentence(text, sentence) {
  const base = String(text || "").replace(/[。；;,\s]+$/, "");
  return `${base}。${sentence}`;
}

function compactItem(item) {
  return {
    title: titleOf(item),
    applicant: applicantOf(item),
    amount: amountOf(item),
    approval_code: item.approval_code || item.definition_code,
    instance_code: item.instance_code || item.process_code,
    task_id: item.task_id || item.id,
    instance_detail: item.instance_detail || null,
  };
}

function renderFields(item) {
  const fields = formFields(item).slice(0, 12);
  if (!fields.length) return "";
  return `
    <section class="info-block">
      <h3>表单信息</h3>
      <div class="field-grid">
        ${fields.map(([key, value]) => `<div><span>${escapeHtml(key)}</span><strong>${escapeHtml(value)}</strong></div>`).join("")}
      </div>
    </section>
  `;
}

function renderAttachments(item) {
  const attachments = attachmentResults(item);
  if (!attachments.length) return "";
  return `
    <section class="info-block">
      <details>
        <summary>查看附件证据（${attachments.length} 个）</summary>
        <div class="evidence-list">
          ${attachments.map((attachment) => renderEvidenceCard(attachment)).join("")}
        </div>
      </details>
    </section>
  `;
}

function renderEvidenceCard(attachment) {
  const evidence = attachmentEvidence(attachment);
  return `
    <article class="evidence-card">
      <div class="evidence-head">
        <strong>${escapeHtml(attachment.name || "附件")}</strong>
        <span>${escapeHtml(evidence.type)}</span>
      </div>
      <div class="evidence-grid">
        ${evidence.facts.map(([label, value]) => `
          <div>
            <span>${escapeHtml(label)}</span>
            <strong>${escapeHtml(value)}</strong>
          </div>
        `).join("")}
      </div>
      ${evidence.note ? `<p>${escapeHtml(evidence.note)}</p>` : ""}
    </article>
  `;
}

function attachmentEvidence(attachment) {
  const name = attachment.name || "附件";
  const text = `${name} ${attachment.text_preview || attachment.summary || ""}`;
  const type = inferAttachmentType(name, text);
  if (type === "发票") return invoiceEvidence(name, text, attachment);
  if (type === "差旅明细") return travelSheetEvidence(name, text, attachment);
  if (type === "行程单") return tripEvidence(name, text, attachment);
  const facts = [];
  const amount = inferAttachmentAmount(text);
  const period = inferPeriod(text);
  const location = inferLocation(text);
  const company = firstCompany(text);
  const route = inferRoute(text);
  if (amount) facts.push(["金额", amount]);
  if (period) facts.push(["期间", period]);
  if (location) facts.push(["地点", location]);
  if (route) facts.push(["路线", route]);
  if (company) facts.push(["关联方", company]);
  if (!facts.length) facts.push(["读取结果", attachment.error ? "读取失败" : "已读取，未提取到关键字段"]);
  return {
    type,
    facts: facts.slice(0, 5),
    note: attachment.error ? `读取异常：${attachment.error}` : evidenceNote(type, facts),
  };
}

function invoiceEvidence(name, text, attachment) {
  const facts = [];
  const amount = inferInvoiceAmount(text);
  const buyer = firstAfter(text, /购买方信息名称[:：]?\s*([^\s|，,]{4,60}(?:公司|有限公司)?)/) || firstAfter(text, /购买方[^\u4e00-\u9fa5]{0,8}([\u4e00-\u9fa5（）()]{4,60}(?:公司|有限公司)?)/);
  const seller = firstAfter(text, /销售方信息名称[:：]?\s*([^\s|，,]{4,60}(?:公司|有限公司|酒店管理有限公司)?)/) || sellerCompany(text);
  const date = firstAfter(text, /开票日期[:：]?\s*(20\d{2}[-年]\d{1,2}[-月]\d{1,2}日?)/);
  if (amount) facts.push(["金额", amount]);
  if (date) facts.push(["开票日期", normalizeDateText(date)]);
  if (buyer) facts.push(["购买方", buyer]);
  if (seller) facts.push(["开票方", seller]);
  if (!facts.length) facts.push(["读取结果", attachment.error ? "读取失败" : "已读取，未提取到发票关键字段"]);
  return {
    type: "发票",
    facts: facts.slice(0, 5),
    note: attachment.error ? `读取异常：${attachment.error}` : "用于核对购买方、开票方、金额与费用归属。",
  };
}

function travelSheetEvidence(name, text, attachment) {
  const facts = [];
  const traveler = firstAfter(text, /报销人\s*[|:：]\s*([\u4e00-\u9fa5]{2,6})/);
  const project = firstAfter(text, /项目编号\s*[|:：]\s*([A-Za-z0-9-]+)/);
  const days = firstAfter(text, /出差天数\s*[|:：]\s*(\d+)/);
  const location = firstAfter(text, /出差地\s*[|:：]\s*([^|，,\s]{2,24})/) || inferLocation(text);
  const traffic = inferExpenseTypes(text).filter((item) => item === "交通").length ? "滴滴 / 顺风车 / 交通" : "";
  if (traveler) facts.push(["报销人", traveler]);
  if (project) facts.push(["项目编号", project]);
  if (days) facts.push(["出差天数", `${days}天`]);
  if (location) facts.push(["出差地", location]);
  if (traffic) facts.push(["交通方式", traffic]);
  if (!facts.length) facts.push(["读取结果", attachment.error ? "读取失败" : "已读取，建议人工核对明细"]);
  return {
    type: "差旅明细",
    facts: facts.slice(0, 6),
    note: attachment.error ? `读取异常：${attachment.error}` : "用于核对出差期间、地点、天数、交通明细，不直接按单行金额判断总额。",
  };
}

function tripEvidence(name, text, attachment) {
  const facts = [];
  const amount = inferAttachmentAmount(text);
  const period = inferPeriod(text);
  const route = inferTripRoute(text);
  const platform = /滴滴/.test(text) ? "滴滴" : (/顺风车/.test(text) ? "顺风车" : "");
  if (amount) facts.push(["金额", amount]);
  if (period) facts.push(["期间", period]);
  if (route) facts.push(["路线", route]);
  if (platform) facts.push(["平台", platform]);
  if (!facts.length) facts.push(["读取结果", attachment.error ? "读取失败" : "已读取，未提取到行程关键字段"]);
  return {
    type: "行程单",
    facts: facts.slice(0, 5),
    note: attachment.error ? `读取异常：${attachment.error}` : "用于核对交通路线、出行时间和金额。",
  };
}

function inferAttachmentType(name, text) {
  if (/xlsx|Sheet|明细|差旅费/i.test(`${name} ${text}`)) return "差旅明细";
  if (/行程单|TRIP|滴滴|用车/i.test(`${name} ${text}`)) return "行程单";
  if (/发票|价税合计|购买方|销售方/.test(`${name} ${text}`)) return "发票";
  if (/pdf/i.test(name)) return "PDF 附件";
  return "附件";
}

function inferAttachmentAmount(text) {
  const candidates = [
    /合计\s*([0-9]+(?:\.[0-9]+)?)/,
    /价税合计[^\d]{0,12}([0-9]+(?:\.[0-9]+)?)/,
    /金额[^\d]{0,12}([0-9]+(?:\.[0-9]+)?)/,
    /¥\s*([0-9]+(?:\.[0-9]+)?)/,
  ];
  for (const pattern of candidates) {
    const value = firstMatch(text, pattern);
    if (value) return `${value}元`;
  }
  return "";
}

function inferInvoiceAmount(text) {
  const explicit = firstMatch(text, /价税合计[^\d]{0,16}([0-9]+(?:\.[0-9]+)?)/);
  if (explicit) return `${explicit}元`;
  const values = [...text.matchAll(/¥\s*([0-9]+(?:\.[0-9]+)?)/g)].map((match) => Number(match[1])).filter((value) => value > 0);
  if (values.length) return `${Math.max(...values).toFixed(2).replace(/\.00$/, "")}元`;
  return inferAttachmentAmount(text);
}

function inferRoute(text) {
  const match = text.match(/([\u4e00-\u9fa5]{2,8})\s*(?:-|—|至|到|→)\s*([\u4e00-\u9fa5]{2,8})/);
  if (match) return `${match[1]} → ${match[2]}`;
  const cities = inferLocation(text);
  return cities.includes("、") ? cities.replace("、", " → ") : "";
}

function inferTripRoute(text) {
  const start = firstAfter(text, /(?:起点|出发地|上车点)\s*[|:：]?\s*([^|，,\s]{2,32})/);
  const end = firstAfter(text, /(?:终点|目的地|下车点)\s*[|:：]?\s*([^|，,\s]{2,32})/);
  if (isValidTripPlace(start) && isValidTripPlace(end)) return `${start} → ${end}`;
  const route = text.match(/([\u4e00-\u9fa5]{2,16})\s*(?:-|—|至|→)\s*([\u4e00-\u9fa5]{2,16})/);
  if (route && isValidTripPlace(route[1]) && isValidTripPlace(route[2])) return `${route[1]} → ${route[2]}`;
  return "";
}

function isValidTripPlace(value) {
  if (!value) return false;
  if (/出发时间|到达时间|申请日期|起止日期|日期|时间|起点|终点|里程|公里|金额|备注|城市|车型|平台|手机号|序号|\[|\]/.test(value)) return false;
  return /[\u4e00-\u9fa5]{2,}/.test(value);
}

function evidenceNote(type, facts) {
  if (type === "发票") return "用于核对开票方、购买方、金额与费用归属。";
  if (type === "差旅明细") return "用于核对出差期间、天数、费用构成与报销人。";
  if (type === "行程单") return "用于核对交通路线、出行时间和金额。";
  return facts.length ? "已提取关键字段，原始 OCR 不默认展示。" : "";
}

function renderBusinessSummary(item) {
  const attachments = attachmentResults(item);
  if (!attachments.length) return "";
  const fields = Object.fromEntries(formFields(item));
  const evidenceText = attachments.map((attachment) => `${attachment.name || ""} ${attachment.text_preview || attachment.summary || ""}`).join(" ");
  const summary = structuredBusinessSummary(item, fields, evidenceText, attachments);
  const keyFacts = [
    ["类型", summary.category],
    ["金额", summary.amount],
    ["期间", summary.period],
    ["地点", summary.location],
    ["费用类型", summary.expenseTypes.join("、")],
    ["票据", summary.invoiceTypes.join("、")],
    ["费用承担", summary.company],
    ["项目/事由", summary.reason],
  ].filter(([, value]) => value && (!Array.isArray(value) || value.length));
  const risks = summary.risks.length ? summary.risks : ["未发现明显异常，但仍建议核对原始票据。"];
  const amountCheck = renderAmountCheck(summary.amountCheck);
  return `
    <section class="business-summary">
      <div class="summary-head">
        <div>
          <h3>业务摘要</h3>
          <p>${escapeHtml(summary.category)}</p>
        </div>
        <strong>${escapeHtml(summary.amount || "金额未识别")}</strong>
      </div>
      <div class="summary-grid">
        ${keyFacts.map(([label, value]) => `
          <div>
            <span>${escapeHtml(label)}</span>
            <strong>${escapeHtml(Array.isArray(value) ? value.join("、") : value)}</strong>
          </div>
        `).join("")}
      </div>
      ${amountCheck}
      <div class="risk-list">
        <span>判断提示</span>
        <ul>${risks.map((risk) => `<li>${escapeHtml(risk)}</li>`).join("")}</ul>
      </div>
    </section>
  `;
}

function structuredBusinessSummary(item, fields, evidenceText, attachments) {
  const haystack = `${evidenceText} ${Object.keys(fields).join(" ")} ${Object.values(fields).join(" ")}`;
  const amountCheck = amountReconciliation(item, attachments, haystack);
  return {
    category: inferBusinessCategory(haystack, fields),
    amount: amountOf(item) || firstMatch(haystack, /(?:金额|价税合计|合计|费用汇总)[^\d]{0,8}([0-9]+(?:\.[0-9]+)?)/),
    period: inferPeriod(haystack),
    location: inferLocation(haystack),
    expenseTypes: inferExpenseTypes(haystack),
    invoiceTypes: inferInvoiceTypes(haystack),
    company: fieldValue(item, ["费用承担公司", "承担公司", "付款公司", "公司"]) || firstCompany(haystack),
    reason: fieldValue(item, ["报销事由", "申请事由", "付款事由", "借款事由", "事由", "用途", "项目名称", "项目编码", "项目"]),
    amountCheck,
    risks: inferRisks(item, haystack, amountCheck),
  };
}

function renderAmountCheck(check) {
  if (!check || !check.requestAmount) return "";
  const facts = [
    ["申请金额", formatMoney(check.requestAmount)],
    ["有票/行程金额", formatMoney(check.verifiedAmount)],
  ];
  if (check.allowanceAmount > 1) facts.push(["可能补贴/无票金额", formatMoney(check.allowanceAmount)]);
  if (check.days) facts.push(["出差天数", `${check.days}天`]);
  if (check.allowanceDaily) facts.push(["折算", `${formatMoney(check.allowanceDaily)}/天`]);
  return `
    <div class="amount-check">
      <span>金额核对</span>
      <div class="amount-check-grid">
        ${facts.map(([label, value]) => `
          <div>
            <small>${escapeHtml(label)}</small>
            <strong>${escapeHtml(value)}</strong>
          </div>
        `).join("")}
      </div>
    </div>
  `;
}

function amountReconciliation(item, attachments, haystack) {
  const requestAmount = parseMoney(amountOf(item));
  if (!requestAmount) return null;
  const verifiedAmount = roundMoney(attachments.reduce((sum, attachment) => sum + attachmentAuditAmount(attachment), 0));
  const allowanceAmount = roundMoney(Math.max(requestAmount - verifiedAmount, 0));
  const days = Number(firstMatch(haystack, /出差天数\s*[|:：]?\s*(\d+)/) || 0);
  const allowanceDaily = days && allowanceAmount > 1 ? roundMoney(allowanceAmount / days) : 0;
  let note = "已按附件中可核对金额做初步拆分。";
  if (allowanceAmount > 1 && days) {
    note = "差额可能为补贴或无票定额，按公司差旅标准核对。";
  } else if (allowanceAmount > 1) {
    note = `差额约 ${formatMoney(allowanceAmount)}，需核对补贴、未读附件或费用说明。`;
  } else if (verifiedAmount > requestAmount + 1) {
    note = "附件金额高于申请金额，可能存在明细总额、历史行程或重复附件，不应直接按附件总额审批。";
  } else if (verifiedAmount) {
    note = "申请金额与可核对附件金额基本一致。";
  }
  return { requestAmount, verifiedAmount, allowanceAmount, days, allowanceDaily, note };
}

function attachmentAuditAmount(attachment) {
  const name = attachment.name || "";
  const text = `${name} ${attachment.text_preview || attachment.summary || ""}`;
  const type = inferAttachmentType(name, text);
  if (type === "发票") return parseMoney(inferInvoiceAmount(text));
  if (type === "行程单") return parseMoney(inferAttachmentAmount(text));
  return 0;
}

function parseMoney(value) {
  const match = String(value || "").replace(/,/g, "").match(/([0-9]+(?:\.[0-9]+)?)/);
  return match ? Number(match[1]) : 0;
}

function roundMoney(value) {
  return Math.round((Number(value) || 0) * 100) / 100;
}

function formatMoney(value) {
  return `${roundMoney(value).toFixed(2).replace(/\.00$/, "")}元`;
}

function inferPeriod(text) {
  const range = text.match(/(20\d{2}[-/.年]\d{1,2}[-/.月]\d{1,2}日?)\s*(?:至|到|-|~|—)\s*(20\d{2}[-/.年]\d{1,2}[-/.月]\d{1,2}日?)/);
  if (range) return `${normalizeDateText(range[1])} 至 ${normalizeDateText(range[2])}`;
  const dates = [...text.matchAll(/20\d{2}[-/.年]\d{1,2}[-/.月]\d{1,2}日?/g)].map((match) => normalizeDateText(match[0]));
  return [...new Set(dates)].slice(0, 2).join(" 至 ");
}

function normalizeDateText(value) {
  return String(value).replace(/年|[/.]/g, "-").replace(/月/g, "-").replace(/日/g, "").replace(/-+/g, "-");
}

function inferLocation(text) {
  const cities = ["上海", "苏州", "北京", "深圳", "广州", "杭州", "南京", "成都", "武汉", "西安", "宁波", "无锡"];
  return cities.filter((city) => text.includes(city)).slice(0, 3).join("、");
}

function inferExpenseTypes(text) {
  const types = [
    ["住宿", /住宿|酒店|宾馆/],
    ["交通", /交通|滴滴|出租|打车|高铁|机票|车票|行程/],
    ["餐饮", /餐饮|饭店|餐费|招待/],
    ["办公/采购", /办公|采购|物料|设备/],
    ["服务费", /服务费|技术服务|咨询/],
  ];
  return types.filter(([, pattern]) => pattern.test(text)).map(([label]) => label);
}

function inferInvoiceTypes(text) {
  const types = [];
  if (/电子发票|普通发票/.test(text)) types.push("电子发票");
  if (/差旅|明细|xlsx|Sheet/i.test(text)) types.push("差旅明细");
  if (/行程单|TRIP|滴滴/i.test(text)) types.push("行程单");
  return types;
}

function inferRisks(item, text, amountCheck) {
  const risks = [];
  const hasReason = fieldValue(item, ["报销事由", "申请事由", "付款事由", "借款事由", "事由", "用途"]);
  const hasInvoice = inferInvoiceTypes(text).length;
  const hasPeriod = inferPeriod(text);
  if (amountCheck?.allowanceAmount > 1 && amountCheck?.days) {
    risks.push("补贴或无票定额费用需要按公司差旅标准核对。");
  }
  if (!hasReason && (hasInvoice || hasPeriod)) risks.push("表单事由不明显，建议确认业务背景或对应项目。");
  if (!hasInvoice) risks.push("未识别到明确票据类型。");
  if (!hasPeriod) risks.push("未识别到明确费用期间。");
  if (/金额未识别|未识别/.test(amountOf(item))) risks.push("金额字段不完整。");
  return risks;
}

function firstMatch(text, pattern) {
  const match = text.match(pattern);
  return match ? match[1] : "";
}

function firstAfter(text, pattern) {
  const match = text.match(pattern);
  return match ? String(match[1] || "").trim() : "";
}

function firstCompany(text) {
  const match = text.match(/[\u4e00-\u9fa5（）()]{2,40}(?:有限公司|公司)/);
  return match ? match[0] : "";
}

function sellerCompany(text) {
  const companies = [...text.matchAll(/[\u4e00-\u9fa5（）()]{2,40}(?:酒店管理有限公司|有限公司|公司)/g)].map((match) => match[0]);
  return companies.find((company) => !company.includes("固势")) || companies[0] || "";
}

function renderLegacyBusinessSummary(item) {
  const attachments = attachmentResults(item);
  if (!attachments.length) return "";
  const fields = Object.fromEntries(formFields(item));
  const evidenceText = attachments.map((attachment) => `${attachment.name || ""} ${attachment.text_preview || attachment.summary || ""}`).join(" ");
  const category = inferBusinessCategory(evidenceText, fields);
  const keyFacts = [
    fieldValue(item, ["费用承担公司", "承担公司", "付款公司", "公司"]) && `费用承担公司：${fieldValue(item, ["费用承担公司", "承担公司", "付款公司", "公司"])}`,
    fieldValue(item, ["报销事由", "申请事由", "付款事由", "借款事由", "事由", "用途"]) && `事由：${fieldValue(item, ["报销事由", "申请事由", "付款事由", "借款事由", "事由", "用途"])}`,
    fieldValue(item, ["项目名称", "项目编码", "项目"]) && `项目：${fieldValue(item, ["项目名称", "项目编码", "项目"])}`,
  ].filter(Boolean);
  const evidence = attachments.slice(0, 3).map((attachment) => {
    const name = attachment.name || "附件";
    const text = shortText(attachment.text_preview || attachment.summary || attachment.error || "未读取到摘要", 110);
    return `${name}：${text}`;
  });
  return `
    <section class="business-summary">
      <h3>业务摘要</h3>
      <div class="summary-line"><strong>${escapeHtml(category)}</strong><span>${escapeHtml(amountOf(item) || "金额未识别")}</span></div>
      ${keyFacts.length ? `<ul>${keyFacts.map((fact) => `<li>${escapeHtml(fact)}</li>`).join("")}</ul>` : ""}
      <p>${escapeHtml(evidence.join("；"))}</p>
    </section>
  `;
}

function formFields(item) {
  const form = detailOf(item).form;
  const output = [];
  const walk = (node) => {
    if (Array.isArray(node)) {
      node.forEach(walk);
      return;
    }
    if (!node || typeof node !== "object") return;
    const key = node.name || node.title || node.field_name || node.label;
    const value = node.value ?? node.text ?? node.display_value;
    if (key && value !== undefined && value !== null && String(value).trim()) {
      output.push([String(key), shortText(flattenValue(value), 80)]);
    }
    Object.keys(node).forEach((childKey) => {
      if (["children", "fields", "items", "value"].includes(childKey)) walk(node[childKey]);
    });
  };
  walk(form);
  return output;
}

function fieldValue(item, candidates) {
  const fields = formFields(item);
  for (const candidate of candidates) {
    const found = fields.find(([key]) => key.includes(candidate));
    if (found) return found[1];
  }
  return "";
}

function inferBusinessCategory(text, fields) {
  const haystack = `${text} ${Object.keys(fields).join(" ")} ${Object.values(fields).join(" ")}`;
  if (/酒店|住宿|行程|出差|滴滴|高铁|机票|车票|差旅/.test(haystack)) return "差旅/住宿报销";
  if (/借款|暂借|预支/.test(haystack)) return "借款申请";
  if (/发票|餐饮|采购|办公|服务费|费用/.test(haystack)) return "日常费用报销";
  return "费用报销";
}

function attachmentResults(item) {
  const fromItem = item._attachment_results;
  const fromRaw = rawOf(item)._attachment_results;
  return Array.isArray(fromItem) ? fromItem : (Array.isArray(fromRaw) ? fromRaw : []);
}

function flattenValue(value) {
  if (typeof value === "string" || typeof value === "number") return String(value);
  if (Array.isArray(value)) return value.map(flattenValue).filter(Boolean).join("、");
  if (value && typeof value === "object") {
    return Object.values(value).map(flattenValue).filter(Boolean).join("、");
  }
  return "";
}

function shortText(value, limit) {
  const text = String(value || "").replace(/\s+/g, " ").trim();
  return text.length > limit ? `${text.slice(0, limit)}...` : text;
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#039;",
  })[char]);
}
