const state = {
  data: null,
  filters: {
    query: "",
    topic: "all",
    level: "all",
    days: "all",
  },
};

const nodes = {
  updatedAt: document.querySelector("#updatedAt"),
  paperCount: document.querySelector("#paperCount"),
  topicCount: document.querySelector("#topicCount"),
  llmStatus: document.querySelector("#llmStatus"),
  resultCount: document.querySelector("#resultCount"),
  paperList: document.querySelector("#paperList"),
  topicFilter: document.querySelector("#topicFilter"),
  levelFilter: document.querySelector("#levelFilter"),
  dateFilter: document.querySelector("#dateFilter"),
  searchInput: document.querySelector("#searchInput"),
  template: document.querySelector("#paperTemplate"),
};

function formatDate(value) {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value.slice(0, 10);
  return date.toLocaleDateString("zh-CN", { year: "numeric", month: "2-digit", day: "2-digit" });
}

function daysSince(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return Infinity;
  const now = new Date();
  return Math.floor((now.setHours(0, 0, 0, 0) - date.setHours(0, 0, 0, 0)) / 86400000);
}

function textIncludes(paper, query) {
  if (!query) return true;
  const haystack = [
    paper.title,
    paper.summary,
    (paper.authors || []).join(" "),
    (paper.categories || []).join(" "),
    paper.best_match?.reason,
    paper.chinese_summary?.innovation,
  ]
    .join(" ")
    .toLowerCase();
  return haystack.includes(query.toLowerCase());
}

function matchesFilters(paper) {
  const best = paper.best_match || {};
  if (!textIncludes(paper, state.filters.query)) return false;
  if (state.filters.topic !== "all" && best.topic_id !== state.filters.topic) return false;
  if (state.filters.level !== "all" && best.level !== state.filters.level) return false;
  if (state.filters.days !== "all" && daysSince(paper.published) > Number(state.filters.days)) return false;
  return true;
}

function setText(parent, selector, text) {
  parent.querySelector(selector).textContent = text || "暂无";
}

function renderPaper(paper) {
  const node = nodes.template.content.firstElementChild.cloneNode(true);
  const best = paper.best_match || {};
  const summary = paper.chinese_summary || {};
  const badge = node.querySelector(".match-badge");

  badge.textContent = `${best.level || "low"} ${(best.score ?? 0).toFixed(2)}`;
  badge.classList.add(best.level || "low");

  setText(node, ".paper-date", formatDate(paper.published));
  setText(node, ".paper-source", paper.source || "paper");
  setText(node, ".paper-title", paper.title);
  setText(node, ".paper-authors", (paper.authors || []).slice(0, 8).join(", "));
  setText(node, ".summary-problem", summary.problem);
  setText(node, ".summary-method", summary.method);
  setText(node, ".summary-innovation", summary.innovation);
  setText(node, ".summary-evidence", summary.evidence);
  setText(node, ".summary-limitations", summary.limitations);
  setText(node, ".summary-relevant", summary.why_relevant);
  setText(node, ".match-reason", `${best.topic_name || "未分类"}：${best.reason || ""}`);

  const tags = node.querySelector(".paper-tags");
  for (const category of (paper.categories || []).slice(0, 8)) {
    const tag = document.createElement("span");
    tag.className = "tag";
    tag.textContent = category;
    tags.appendChild(tag);
  }

  const absLink = node.querySelector(".abs-link");
  const pdfLink = node.querySelector(".pdf-link");
  absLink.href = paper.paper_url || "#";
  pdfLink.href = paper.pdf_url || paper.paper_url || "#";
  return node;
}

function render() {
  const papers = (state.data?.papers || []).filter(matchesFilters);
  nodes.paperList.textContent = "";
  nodes.resultCount.textContent = `${papers.length} 篇`;

  if (!papers.length) {
    const empty = document.createElement("div");
    empty.className = "empty-state";
    empty.textContent = "当前筛选条件下没有论文。";
    nodes.paperList.appendChild(empty);
    return;
  }

  const fragment = document.createDocumentFragment();
  for (const paper of papers) fragment.appendChild(renderPaper(paper));
  nodes.paperList.appendChild(fragment);
}

function hydrateFilters() {
  nodes.topicFilter.innerHTML = '<option value="all">全部方向</option>';
  for (const topic of state.data.topics || []) {
    const option = document.createElement("option");
    option.value = topic.id;
    option.textContent = topic.name;
    nodes.topicFilter.appendChild(option);
  }
}

function bindEvents() {
  nodes.searchInput.addEventListener("input", (event) => {
    state.filters.query = event.target.value.trim();
    render();
  });
  nodes.topicFilter.addEventListener("change", (event) => {
    state.filters.topic = event.target.value;
    render();
  });
  nodes.levelFilter.addEventListener("change", (event) => {
    state.filters.level = event.target.value;
    render();
  });
  nodes.dateFilter.addEventListener("change", (event) => {
    state.filters.days = event.target.value;
    render();
  });
}

async function loadData() {
  const response = await fetch("./data/papers.json", { cache: "no-store" });
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return response.json();
}

async function main() {
  bindEvents();
  try {
    state.data = await loadData();
  } catch (error) {
    state.data = {
      generated_at_iso: new Date().toISOString(),
      topics: [],
      papers: [],
      stats: { llm_enabled: false },
    };
    nodes.updatedAt.textContent = `数据读取失败：${error.message}`;
  }

  const stats = state.data.stats || {};
  nodes.updatedAt.textContent = `更新于 ${formatDate(state.data.generated_at_iso)} · ${state.data.config_source || "file"}`;
  nodes.paperCount.textContent = String(state.data.papers?.length || 0);
  nodes.topicCount.textContent = String(state.data.topics?.length || 0);
  nodes.llmStatus.textContent = stats.llm_enabled ? "LLM" : "基础";
  hydrateFilters();
  render();
}

main();
