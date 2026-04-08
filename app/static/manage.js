const sourceForm = document.getElementById("source-form");
const formTitle = document.getElementById("form-title");
const sourceTableBody = document.getElementById("source-table-body");
const contentList = document.getElementById("content-list");
const categorySummary = document.getElementById("category-summary");
const refreshAllBtn = document.getElementById("refresh-all-btn");
const fetchNowBtn = document.getElementById("fetch-now-btn");
const pushTestBtn = document.getElementById("push-test-btn");
const resetSourceBtn = document.getElementById("reset-source-btn");
const actionStatus = document.getElementById("action-status");
const schedulerSummary = document.getElementById("scheduler-summary");
const pushplusSummary = document.getElementById("pushplus-summary");
const settingsForm = document.getElementById("settings-form");
const pushPreview = document.getElementById("push-preview");

let editingSourceId = null;

function setActionStatus(type, title, text) {
  actionStatus.className = `status status-${type} inline-status`;
  actionStatus.innerHTML = `
    <span class="status-dot"></span>
    <div>
      <p class="status-title">${title}</p>
      <p class="status-text">${text}</p>
    </div>
  `;
}

function resetForm() {
  editingSourceId = null;
  formTitle.textContent = "新增监控源";
  sourceForm.reset();
  document.getElementById("source-weight").value = 1;
  document.getElementById("source-platform").value = "bilibili";
  document.getElementById("source-category").value = "kol";
}

function fillStats(stats) {
  document.getElementById("stat-active-sources").textContent = stats.active_sources || 0;
  document.getElementById("stat-total-contents").textContent = stats.total_contents || 0;
  document.getElementById("stat-today-contents").textContent = stats.today_contents || 0;
  document.getElementById("stat-duplicates").textContent = stats.duplicate_contents || 0;
}

function renderCategorySummary(categories) {
  categorySummary.innerHTML = "";
  if (!categories || categories.length === 0) {
    categorySummary.innerHTML = '<span class="tag muted-tag">暂无分类统计</span>';
    return;
  }

  categories.forEach((item) => {
    const span = document.createElement("span");
    span.className = "tag muted-tag";
    span.textContent = `${item.category}: ${item.count}`;
    categorySummary.appendChild(span);
  });
}

function renderSystemSettings(settings) {
  if (!settings) {
    schedulerSummary.textContent = "未读取到调度配置。";
    pushplusSummary.textContent = "未读取到推送配置。";
    return;
  }

  schedulerSummary.textContent = settings.scheduler_enabled
    ? `已启用，每天 ${String(settings.daily_report_hour).padStart(2, "0")}:${String(settings.daily_report_minute).padStart(2, "0")} 执行，时区 ${settings.scheduler_timezone}，回看最近 ${settings.fetch_lookback_hours} 小时。`
    : "当前已关闭自动采集任务。";

  pushplusSummary.textContent = settings.pushplus_configured
    ? `已配置 PushPlus token（${settings.pushplus_token_masked || "已隐藏"}），可以直接测试推送。`
    : "尚未配置 PushPlus token，当前只能先完成采集和入库。";

  document.getElementById("scheduler-enabled").value = String(settings.scheduler_enabled);
  document.getElementById("daily-report-hour").value = settings.daily_report_hour;
  document.getElementById("daily-report-minute").value = settings.daily_report_minute;
  document.getElementById("fetch-lookback-hours").value = settings.fetch_lookback_hours;
  document.getElementById("scheduler-timezone").value = settings.scheduler_timezone;
  document.getElementById("pushplus-token").placeholder = settings.pushplus_token_masked
    ? `当前 token: ${settings.pushplus_token_masked}`
    : "输入新的 PushPlus token；留空表示保留现有 token";
}

function renderSources(items) {
  sourceTableBody.innerHTML = "";
  if (!items || items.length === 0) {
    sourceTableBody.innerHTML = '<tr><td colspan="7" class="empty-row">暂无监控源</td></tr>';
    return;
  }

  items.forEach((item) => {
    const row = document.createElement("tr");
    row.innerHTML = `
      <td>${item.name}</td>
      <td>${item.platform}</td>
      <td>${item.platform_id}</td>
      <td>${item.category}</td>
      <td>${item.importance_weight}</td>
      <td><span class="status-chip ${item.is_active ? "active" : "inactive"}">${item.is_active ? "启用" : "停用"}</span></td>
      <td>
        <div class="table-actions">
          <button class="table-btn" data-action="edit" data-id="${item.id}">编辑</button>
          <button class="table-btn" data-action="toggle" data-id="${item.id}">${item.is_active ? "停用" : "启用"}</button>
          <button class="table-btn danger" data-action="delete" data-id="${item.id}">删除</button>
        </div>
      </td>
    `;
    sourceTableBody.appendChild(row);
  });
}

function renderContents(items) {
  contentList.innerHTML = "";
  if (!items || items.length === 0) {
    contentList.innerHTML = '<article class="stream-empty">还没有处理内容，先添加监控源并执行一次采集。</article>';
    return;
  }

  items.forEach((item) => {
    const article = document.createElement("article");
    article.className = "stream-card";
    const publishedAt = item.published_at ? new Date(item.published_at).toLocaleString() : "未知时间";
    const stars = "★".repeat(Math.max(1, item.importance_stars || 1));
    const link = item.url ? `<a href="${item.url}" target="_blank" rel="noreferrer">查看原文</a>` : "";
    article.innerHTML = `
      <div class="stream-head">
        <div>
          <p class="stream-meta">${item.platform} · ${item.source_name || "未知来源"} · ${publishedAt}</p>
          <h3>${item.title}</h3>
        </div>
        <span class="pill">${item.category || "uncategorized"}</span>
      </div>
      <p class="stream-summary">${item.summary || item.content || "暂无摘要"}</p>
      <div class="stream-footer">
        <span class="stars">${stars}</span>
        <span>${item.importance_reason || "暂无评分说明"}</span>
        ${link}
      </div>
    `;
    contentList.appendChild(article);
  });
}

function getFormPayload() {
  return {
    name: document.getElementById("source-name").value.trim(),
    platform: document.getElementById("source-platform").value,
    platform_id: document.getElementById("source-platform-id").value.trim(),
    category: document.getElementById("source-category").value,
    importance_weight: Number(document.getElementById("source-weight").value || 1),
    source_url: document.getElementById("source-url").value.trim() || null,
    rss_url: document.getElementById("source-rss-url").value.trim() || null,
    is_active: true,
    extra_config: {},
  };
}

async function requestJson(url, options = {}) {
  const response = await fetch(url, {
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {}),
    },
    ...options,
  });

  const contentType = response.headers.get("content-type") || "";
  const data = contentType.includes("application/json") ? await response.json() : await response.text();

  if (!response.ok) {
    const message = typeof data === "string" ? data : data?.detail?.message || data?.detail || "请求失败";
    throw new Error(message);
  }

  return data;
}

async function loadDashboard() {
  const [sources, stats, categories, contents, systemSettings] = await Promise.all([
    requestJson("/api/monitor-sources"),
    requestJson("/api/stats"),
    requestJson("/api/categories"),
    requestJson("/api/contents?page=1&page_size=12"),
    requestJson("/api/system-settings"),
  ]);

  renderSources(sources);
  fillStats(stats);
  renderCategorySummary(categories);
  renderContents(contents.items || []);
  renderSystemSettings(systemSettings);
}

function getSettingsPayload() {
  return {
    scheduler_enabled: document.getElementById("scheduler-enabled").value === "true",
    daily_report_hour: Number(document.getElementById("daily-report-hour").value || 8),
    daily_report_minute: Number(document.getElementById("daily-report-minute").value || 0),
    fetch_lookback_hours: Number(document.getElementById("fetch-lookback-hours").value || 24),
    scheduler_timezone: document.getElementById("scheduler-timezone").value.trim() || "Asia/Shanghai",
    push_provider: "pushplus",
    pushplus_token: document.getElementById("pushplus-token").value.trim() || null,
  };
}

async function saveSource(event) {
  event.preventDefault();
  const payload = getFormPayload();
  if (!payload.name || !payload.platform_id) {
    setActionStatus("error", "表单不完整", "名称和平台 ID 是必填项。")
    return;
  }

  const url = editingSourceId ? `/api/monitor-sources/${editingSourceId}` : "/api/monitor-sources";
  const method = editingSourceId ? "PUT" : "POST";

  try {
    setActionStatus("loading", "保存中", "正在写入监控源配置...");
    await requestJson(url, {
      method,
      body: JSON.stringify(payload),
    });
    resetForm();
    await loadDashboard();
    setActionStatus("success", "保存成功", "监控源已经更新，采集缓存也已刷新。")
  } catch (error) {
    setActionStatus("error", "保存失败", error.message);
  }
}

async function handleTableClick(event) {
  const button = event.target.closest("button[data-action]");
  if (!button) return;

  const sourceId = Number(button.dataset.id);
  const action = button.dataset.action;

  try {
    if (action === "edit") {
      const sources = await requestJson("/api/monitor-sources");
      const source = sources.find((item) => item.id === sourceId);
      if (!source) return;
      editingSourceId = source.id;
      formTitle.textContent = `编辑监控源 #${source.id}`;
      document.getElementById("source-name").value = source.name || "";
      document.getElementById("source-platform").value = source.platform || "bilibili";
      document.getElementById("source-platform-id").value = source.platform_id || "";
      document.getElementById("source-category").value = source.category || "kol";
      document.getElementById("source-weight").value = source.importance_weight || 1;
      document.getElementById("source-url").value = source.source_url || "";
      document.getElementById("source-rss-url").value = source.rss_url || "";
      setActionStatus("idle", "已载入待编辑数据", "修改后点击“保存监控源”即可覆盖。")
      return;
    }

    if (action === "toggle") {
      setActionStatus("loading", "切换状态中", "正在更新监控源启用状态...");
      await requestJson(`/api/monitor-sources/${sourceId}/toggle`, { method: "PUT" });
      await loadDashboard();
      setActionStatus("success", "状态已更新", "新的启用状态已经生效。")
      return;
    }

    if (action === "delete") {
      const confirmed = window.confirm("确认删除这个监控源吗？");
      if (!confirmed) return;
      setActionStatus("loading", "删除中", "正在移除监控源...");
      await requestJson(`/api/monitor-sources/${sourceId}`, { method: "DELETE" });
      await loadDashboard();
      setActionStatus("success", "删除成功", "监控源已移除。")
    }
  } catch (error) {
    setActionStatus("error", "操作失败", error.message);
  }
}

async function triggerFetch() {
  try {
    setActionStatus("loading", "采集中", "正在抓取启用监控源的最新内容，并执行 AI 处理...");
    const result = await requestJson("/api/fetch/now?force_reload=true", { method: "POST" });
    await loadDashboard();
    setActionStatus(
      "success",
      "采集完成",
      `检查源 ${result.sources_checked} 个，抓到 ${result.fetched_items} 条，新增 ${result.new_items} 条，重复 ${result.duplicate_items} 条。`
    );
  } catch (error) {
    setActionStatus("error", "采集失败", error.message);
  }
}

async function triggerPushTest() {
  try {
    setActionStatus("loading", "推送中", "正在把今日内容整理成 PushPlus 日报...");
    const result = await requestJson("/api/push/test", { method: "POST" });
    if (!result.sent) {
      pushPreview.textContent = "今天还没有可推送内容，或者尚未配置 PushPlus token。";
      setActionStatus("idle", "没有可推送内容", "今天还没有已处理内容，或者尚未配置 PushPlus token。")
      return;
    }
    pushPreview.innerHTML = (result.preview || [])
      .map((chunk) => `<pre class="code-block preview-code">${chunk}</pre>`)
      .join("");
    setActionStatus("success", "推送完成", `已发送 ${result.message_chunks} 段消息，覆盖 ${result.items} 条内容。`)
  } catch (error) {
    setActionStatus("error", "推送失败", error.message);
  }
}

async function saveSettings(event) {
  event.preventDefault();
  try {
    setActionStatus("loading", "保存中", "正在保存调度和 PushPlus 配置，并重载定时任务...");
    await requestJson("/api/system-settings", {
      method: "PUT",
      body: JSON.stringify(getSettingsPayload()),
    });
    document.getElementById("pushplus-token").value = "";
    await loadDashboard();
    setActionStatus("success", "配置已保存", "新的调度时间和 PushPlus 设置已经生效。");
  } catch (error) {
    setActionStatus("error", "保存失败", error.message);
  }
}

sourceForm.addEventListener("submit", saveSource);
sourceTableBody.addEventListener("click", handleTableClick);
refreshAllBtn.addEventListener("click", async () => {
  try {
    setActionStatus("loading", "刷新中", "正在同步最新数据视图...");
    await loadDashboard();
    setActionStatus("success", "刷新完成", "页面数据已经更新到最新状态。")
  } catch (error) {
    setActionStatus("error", "刷新失败", error.message);
  }
});
fetchNowBtn.addEventListener("click", triggerFetch);
pushTestBtn.addEventListener("click", triggerPushTest);
resetSourceBtn.addEventListener("click", resetForm);
settingsForm.addEventListener("submit", saveSettings);

resetForm();
loadDashboard().catch((error) => {
  setActionStatus("error", "初始化失败", error.message);
});
