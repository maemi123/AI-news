const form = document.getElementById("process-form");
const input = document.getElementById("bv-id");
const submitBtn = document.getElementById("submit-btn");
const statusBox = document.getElementById("status-box");
const resultCard = document.getElementById("result-card");
const errorCard = document.getElementById("error-card");
const errorMessage = document.getElementById("error-message");
const errorHint = document.getElementById("error-hint");

function setStatus(type, title, text) {
  statusBox.className = `status status-${type}`;
  statusBox.innerHTML = `
    <span class="status-dot"></span>
    <div>
      <p class="status-title">${title}</p>
      <p class="status-text">${text}</p>
    </div>
  `;
}

function clearCards() {
  resultCard.classList.add("hidden");
  errorCard.classList.add("hidden");
}

function renderTags(containerId, items) {
  const container = document.getElementById(containerId);
  container.innerHTML = "";

  if (!items || items.length === 0) {
    container.innerHTML = '<span class="tag">暂无</span>';
    return;
  }

  items.forEach((item) => {
    const span = document.createElement("span");
    span.className = "tag";
    span.textContent = item;
    container.appendChild(span);
  });
}

function renderPoints(points) {
  const list = document.getElementById("result-points");
  list.innerHTML = "";

  if (!points || points.length === 0) {
    const item = document.createElement("li");
    item.textContent = "暂无提炼出的重点。";
    list.appendChild(item);
    return;
  }

  points.forEach((point) => {
    const item = document.createElement("li");
    item.textContent = point;
    list.appendChild(item);
  });
}

function renderResult(data) {
  const notes = data.structured_notes || {};

  document.getElementById("result-title").textContent = data.title || "未命名视频";
  document.getElementById("result-category").textContent = data.category || "未分类";
  document.getElementById("result-bv").textContent = data.bv_id || "-";
  document.getElementById("result-source").textContent =
    data.transcript_source === "subtitle"
      ? "B 站 CC 字幕"
      : data.transcript_source === "whisper"
        ? "Whisper 音频转写"
        : "未知";
  document.getElementById("result-summary").textContent = data.summary || "暂无摘要";
  document.getElementById("result-core").textContent = notes.core_concept || "暂无核心概念";
  document.getElementById("result-example").textContent = notes.code_or_example || "暂无示例内容";

  renderTags("result-entities", data.key_entities || []);
  renderTags("result-tags", data.tags || []);
  renderPoints(notes.key_points || []);

  resultCard.classList.remove("hidden");
}

function renderError(detail, fallbackMessage) {
  const message = typeof detail === "string" ? detail : detail?.message || fallbackMessage;
  const hint = typeof detail === "object" ? detail?.hint : "";

  errorMessage.textContent = message || "处理失败，请稍后再试。";
  errorHint.textContent = hint || "请检查 BV 号、.env 配置和服务日志。";
  errorCard.classList.remove("hidden");
}

async function processVideo(bvId) {
  clearCards();
  setStatus("loading", "处理中", "正在抓取视频信息并生成结构化摘要，这一步可能需要几十秒。");
  submitBtn.disabled = true;
  submitBtn.textContent = "处理中...";

  try {
    const response = await fetch(`/test/process_video/${encodeURIComponent(bvId)}`, {
      method: "POST",
    });
    const contentType = response.headers.get("content-type") || "";
    let data;

    if (contentType.includes("application/json")) {
      data = await response.json();
    } else {
      data = {
        detail: {
          message: await response.text() || "服务返回了非 JSON 响应。",
          hint: "请查看后端日志确认是否有未处理异常。",
        },
      };
    }

    if (!response.ok) {
      renderError(data.detail, "接口返回异常");
      setStatus("error", "处理失败", "下方已经展示错误信息，可以根据提示修复后重试。");
      return;
    }

    renderResult(data);
    setStatus("success", "处理完成", "摘要、分类和结构化笔记已经生成完成。")
  } catch (error) {
    renderError("", error.message);
    setStatus("error", "请求失败", "页面请求没有成功完成，请检查后端服务、网络和终端日志。")
  } finally {
    submitBtn.disabled = false;
    submitBtn.textContent = "开始处理";
  }
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const bvId = input.value.trim();

  if (!bvId) {
    clearCards();
    setStatus("error", "请输入 BV 号", "例如 BV1xx411c7mD。输入后再开始处理。");
    return;
  }

  await processVideo(bvId);
});
