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

function fillTags(containerId, items) {
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

function fillPoints(points) {
  const list = document.getElementById("result-points");
  list.innerHTML = "";

  if (!points || points.length === 0) {
    const item = document.createElement("li");
    item.textContent = "暂无提取到明显要点";
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
      ? "B站 CC 字幕"
      : data.transcript_source === "whisper"
        ? "Whisper 音频转写"
        : "未知";
  document.getElementById("result-summary").textContent = data.summary || "暂无摘要";
  document.getElementById("result-core").textContent = notes.core_concept || "暂无核心概念";
  document.getElementById("result-example").textContent = notes.code_or_example || "暂无示例内容";

  fillTags("result-entities", data.key_entities || []);
  fillTags("result-tags", data.tags || []);
  fillPoints(notes.key_points || []);

  resultCard.classList.remove("hidden");
}

function renderError(detail, fallbackMessage) {
  const message = typeof detail === "string" ? detail : detail?.message || fallbackMessage;
  const hint = typeof detail === "object" ? detail?.hint : "";

  errorMessage.textContent = message || "处理失败，请稍后再试。";
  errorHint.textContent = hint || "请先检查 BV 号、`.env` 配置和网络连接。";
  errorCard.classList.remove("hidden");
}

async function processVideo(bvId) {
  clearCards();
  setStatus("loading", "处理中", "正在抓取视频信息。若没有字幕，系统会自动下载音频并转写，可能需要几十秒到几分钟。");
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
      const text = await response.text();
      data = {
        detail: {
          message: text || "服务返回了非 JSON 响应",
          hint: "后端可能抛出了未处理异常，请查看终端日志。",
        },
      };
    }

    if (!response.ok) {
      renderError(data.detail, "接口返回异常");
      setStatus("error", "处理失败", "请看下方错误提示，通常按建议修改后重新处理即可。");
      return;
    }

    renderResult(data);
    setStatus("success", "处理完成", "已经成功生成摘要和分类，结果就在下方。");
  } catch (error) {
    renderError("", error.message);
    setStatus("error", "请求失败", "页面请求没有成功完成，请检查后端服务是否正常、网络是否可用，以及终端是否出现异常日志。");
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
    setStatus("error", "请输入 BV 号", "例如 `BV1xx411c7mD`。输入后再点击开始处理。");
    return;
  }

  await processVideo(bvId);
});
