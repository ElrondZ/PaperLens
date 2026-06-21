const $ = id => document.getElementById(id);

const STEPS = ["search", "filter", "read", "write"];
const KEY_FIELDS = ["key_anthropic", "key_openai", "key_deepseek", "key_qwen", "key_kimi"];
const BASE_URL_FIELDS = ["base_url_anthropic", "base_url_openai", "base_url_deepseek", "base_url_qwen", "base_url_kimi"];
const TIER_FIELDS = ["tier_cheap", "tier_main", "tier_premium"];
const TAKEAWAY_FIELD = "核心结论";
const SECTION_FIELDS = [
  ["研究问题", "问题"],
  ["方法", "方法"],
  ["创新点", "贡献"],
  ["实验与结果", "结果"],
  ["局限", "局限"],
  ["对我们需求的相关性", "适用性"],
];

let SETTINGS = {};
let running = false;

function esc(value) {
  const d = document.createElement("div");
  d.textContent = value == null ? "" : String(value);
  return d.innerHTML;
}

function toast(message) {
  const t = $("toast");
  t.textContent = message;
  t.classList.add("show");
  setTimeout(() => t.classList.remove("show"), 2200);
}

function switchPage(page) {
  document.querySelectorAll(".nav-btn").forEach(btn => {
    btn.classList.toggle("active", btn.dataset.page === page);
  });
  document.querySelectorAll(".page").forEach(el => {
    el.classList.toggle("active", el.id === `page-${page}`);
  });
}

document.querySelectorAll(".nav-btn").forEach(btn => {
  btn.addEventListener("click", () => switchPage(btn.dataset.page));
});

$("num").addEventListener("input", e => $("numV").textContent = e.target.value);
$("year").addEventListener("input", e => $("yearV").textContent = e.target.value);

function openDrawer() {
  $("mask").classList.add("show");
  $("drawer").classList.add("show");
  $("drawer").setAttribute("aria-hidden", "false");
  loadSettings();
}

function closeDrawer() {
  $("mask").classList.remove("show");
  $("drawer").classList.remove("show");
  $("drawer").setAttribute("aria-hidden", "true");
}

$("openSet").addEventListener("click", openDrawer);
$("closeSet").addEventListener("click", closeDrawer);
$("mask").addEventListener("click", closeDrawer);
$("mailToggle").addEventListener("click", () => $("mailGroup").classList.toggle("open"));

document.addEventListener("keydown", e => {
  if (e.key === "Escape") closeDrawer();
});

$("fillMirror").addEventListener("click", () => {
  $("arxiv_mirror").value = "http://xxx.itp.ac.cn";
});

function providerName(provider) {
  return (SETTINGS.provider_label && SETTINGS.provider_label[provider]) || provider;
}

function buildModelOptions(catalog, availableProviders) {
  let html = "";
  for (const provider of Object.keys(catalog || {})) {
    const enabled = availableProviders.includes(provider);
    const label = providerName(provider);
    html += `<optgroup label="${esc(label)}${enabled ? "" : "（未填写 Key）"}">`;
    for (const model of catalog[provider]) {
      html += `<option value="${esc(`${provider}:${model}`)}" ${enabled ? "" : "disabled"}>${esc(label)} · ${esc(model)}</option>`;
    }
    html += "</optgroup>";
  }
  return html;
}

function formatModel(value) {
  if (!value) return "未配置";
  const [provider, model] = value.split(":");
  const shortModel = (model || "").replace(/^(claude-|gpt-|deepseek-|qwen-|moonshot-)/, "");
  return `${providerName(provider)} · ${shortModel}`;
}

function updateChips() {
  const map = {
    chip_cheap: "tier_cheap",
    chip_main: "tier_main",
    chip_premium: "tier_premium",
  };
  for (const [nodeId, tier] of Object.entries(map)) {
    const node = $(nodeId);
    if (node) node.textContent = formatModel(SETTINGS[tier]);
  }
}

async function loadSettings() {
  try {
    const response = await fetch("/api/settings");
    const settings = await response.json();
    SETTINGS = settings;

    KEY_FIELDS.forEach(key => {
      const input = $(key);
      input.value = "";
      input.placeholder = settings[`${key}_set`] ? `已配置 ${settings[key]}` : input.getAttribute("placeholder");
    });
    BASE_URL_FIELDS.forEach(key => {
      const input = $(key);
      input.value = settings[key] || "";
    });
    $("custom_models_text").value = settings.custom_models_text || "";

    $("semantic_scholar_key").value = "";
    if (settings.semantic_scholar_key_set) {
      $("semantic_scholar_key").placeholder = `已配置 ${settings.semantic_scholar_key}`;
    }
    $("arxiv_mirror").value = settings.arxiv_mirror || "";
    $("smtp_user").value = settings.smtp_user || "";
    $("smtp_password").value = "";
    if (settings.smtp_password_set) $("smtp_password").placeholder = "已配置";

    const available = KEY_FIELDS.filter(k => settings[`${k}_set`]).map(k => k.replace("key_", ""));
    TIER_FIELDS.forEach(tier => {
      $(tier).innerHTML = buildModelOptions(settings.catalog || {}, available);
      if (settings[tier]) $(tier).value = settings[tier];
    });

    updateChips();
  } catch (error) {
    toast("读取设置失败");
  }
}

$("saveSet").addEventListener("click", async () => {
  const payload = {};

  KEY_FIELDS.forEach(key => {
    const value = $(key).value.trim();
    if (value) payload[key] = value;
  });
  BASE_URL_FIELDS.forEach(key => {
    payload[key] = $(key).value.trim();
  });
  payload.custom_models_text = $("custom_models_text").value.trim();

  const s2 = $("semantic_scholar_key").value.trim();
  if (s2) payload.semantic_scholar_key = s2;
  payload.arxiv_mirror = $("arxiv_mirror").value.trim();

  const smtpUser = $("smtp_user").value.trim();
  if (smtpUser) payload.smtp_user = smtpUser;
  const smtpPassword = $("smtp_password").value.trim();
  if (smtpPassword) payload.smtp_password = smtpPassword;

  TIER_FIELDS.forEach(tier => {
    if ($(tier).value) payload[tier] = $(tier).value;
  });

  try {
    await fetch("/api/settings", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(payload),
    });
    await loadSettings();
    closeDrawer();
    toast("设置已保存");
  } catch (error) {
    toast("保存失败");
  }
});

function setStage(stage, pct) {
  const idx = STEPS.indexOf(stage);
  document.querySelectorAll(".step").forEach((el, i) => {
    el.classList.toggle("done", idx > i || stage === "done");
    el.classList.toggle("active", idx === i);
  });
  if (typeof pct === "number") {
    $("progressBar").style.width = `${Math.max(0, Math.min(100, pct))}%`;
  }
}

function finishRun() {
  running = false;
  $("run").disabled = false;
  $("runTxt").textContent = "开始分析";
  $("runIcon").textContent = "→";
  $("runIcon").classList.remove("spin");
}

function splitPoint(text) {
  return String(text || "")
    .split(/\n+|；|;|(?<=[。.!?？])\s+/)
    .map(s => s.replace(/^[-•·\d.、\s]+/, "").trim())
    .filter(Boolean)
    .slice(0, 3);
}

function getSummaryValue(summary, label) {
  if (!summary) return "";
  return summary[label] || summary[label.replace("与", "和")] || "";
}

function compactText(value, fallback = "未明确说明") {
  const points = splitPoint(value);
  return points.length ? points.join("；") : fallback;
}

function renderPaperSummary(summary) {
  const takeaway = getSummaryValue(summary, TAKEAWAY_FIELD) || getSummaryValue(summary, "研究问题") || "暂无明确核心结论。";
  const sections = SECTION_FIELDS.map(([field, label]) => {
    const wide = field === "对我们需求的相关性" ? " wide" : "";
    return `<div class="summary-section${wide}">
      <h4>${esc(label)}</h4>
      <p>${esc(compactText(getSummaryValue(summary, field)))}</p>
    </div>`;
  }).join("");
  return `<p class="takeaway">${esc(takeaway)}</p><div class="summary-grid">${sections}</div>`;
}

function renderReport(result) {
  const root = $("report");
  root.innerHTML = "";
  $("emptyState").style.display = "none";

  let delay = 0;
  const block = html => {
    const div = document.createElement("div");
    div.className = "r-block";
    div.style.animationDelay = `${delay}s`;
    div.innerHTML = html;
    root.appendChild(div);
    delay += 0.06;
  };

  block(`<div class="report-head">
    <h2>检索总结报告</h2>
    <div class="report-meta">${esc(result.query)} · ${esc(result.date)} · 共 ${esc(result.count)} 篇</div>
  </div>`);

  block(`<section class="review">
    <div class="review-title">研究现状综述</div>
    <p class="review-body">${esc(result.review)}</p>
  </section>`);

  for (const group of result.groups || []) {
    let cards = "";
    for (const paper of group.papers || []) {
      const authors = (paper.authors || []).join(", ");
      const meta = [
        authors,
        paper.year || "",
        paper.venue || "",
        paper.url ? `<a href="${esc(paper.url)}" target="_blank">原文</a>` : "",
      ].filter(Boolean).join(" · ");

      const readLevel = paper.read_level || "全文精读";
      cards += `<article class="paper">
        <div class="paper-head">
          <h3 class="paper-title">${esc(paper.title)}</h3>
          <span class="read-badge">${esc(readLevel)}</span>
        </div>
        <div class="paper-meta">${meta}</div>
        ${renderPaperSummary(paper.summary)}
      </article>`;
    }

    block(`<section class="group">
      <div class="group-title">${esc(group.route)} <span class="group-badge">${esc((group.papers || []).length)} 篇</span></div>
      <p class="group-intro">${esc(group.intro)}</p>
      <div class="paper-grid">${cards}</div>
    </section>`);
  }

  setTimeout(() => $("report").scrollIntoView({behavior: "smooth", block: "start"}), 120);
}

$("run").addEventListener("click", async () => {
  if (running) return;

  const query = $("query").value.trim();
  if (!query) {
    toast("请先填写研究需求");
    return;
  }

  running = true;
  switchPage("work");
  $("run").disabled = true;
  $("runTxt").textContent = "分析中";
  $("runIcon").textContent = "◌";
  $("runIcon").classList.add("spin");
  $("report").innerHTML = "";
  $("emptyState").style.display = "grid";
  $("flow").classList.add("show");
  $("progressBar").style.width = "4%";
  $("progDesc").textContent = "正在启动分析任务";
  setStage("search", 4);

  try {
    const response = await fetch("/api/run", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({
        query,
        num_papers: Number($("num").value),
        years_back: Number($("year").value),
      }),
    });
    const started = await response.json();
    if (!started.ok) {
      finishRun();
      toast(started.error || "启动失败");
      if (started.error && started.error.includes("API Key")) openDrawer();
      return;
    }
  } catch (error) {
    finishRun();
    toast("启动失败");
    return;
  }

  const es = new EventSource("/api/progress");
  es.onmessage = ev => {
    const data = JSON.parse(ev.data);
    if (data.stage === "result") {
      renderReport(data.result);
      es.close();
      finishRun();
      return;
    }
    if (data.stage === "error") {
      es.close();
      finishRun();
      $("progDesc").textContent = `出错：${data.desc}`;
      toast("分析出错");
      return;
    }
    if (data.stage === "done") {
      setStage("done", 100);
    } else if (STEPS.includes(data.stage)) {
      setStage(data.stage, data.pct);
    }
    if (data.desc) $("progDesc").textContent = data.desc;
  };
  es.onerror = () => {
    es.close();
    finishRun();
  };
});

(async function init() {
  await loadSettings();
  const anyKey = KEY_FIELDS.some(k => SETTINGS[`${k}_set`]);
  if (!anyKey) {
    setTimeout(() => {
      $("progDesc").textContent = "首次使用请先打开设置，填写至少一个模型 API Key。";
      $("flow").classList.add("show");
    }, 400);
  }
})();
