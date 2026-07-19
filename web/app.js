const REPORT_ROOT = "./reports/";
const LABEL_ORDER = ["high", "AI"];

const state = {
  reports: [],
  candidates: [],
  filters: { watch: "all", relevance: "all", topic: "all", query: "" },
};

const nodes = {
  statusLine: document.querySelector("#statusLine"),
  reportCount: document.querySelector("#reportCount"),
  highCount: document.querySelector("#highCount"),
  aiCount: document.querySelector("#aiCount"),
  failedCount: document.querySelector("#failedCount"),
  watchSummary: document.querySelector("#watchSummary"),
  watchGrid: document.querySelector("#watchGrid"),
  topicChart: document.querySelector("#topicChart"),
  sourceChart: document.querySelector("#sourceChart"),
  candidateCount: document.querySelector("#candidateCount"),
  candidateList: document.querySelector("#candidateList"),
  watchFilter: document.querySelector("#watchFilter"),
  relevanceFilter: document.querySelector("#relevanceFilter"),
  topicFilter: document.querySelector("#topicFilter"),
  searchInput: document.querySelector("#searchInput"),
  watchTemplate: document.querySelector("#watchTemplate"),
  candidateTemplate: document.querySelector("#candidateTemplate"),
};

function text(value, fallback = "-") {
  return String(value || fallback);
}

function reportLink(path) {
  return path ? `${REPORT_ROOT}${path}` : "";
}

function labelText(label) {
  return label === "high" ? "High" : "AI";
}

function topicText(topic) {
  return text(topic, "other").replaceAll("_", " ");
}

function watchName(report) {
  return report.part_name || report.watch_id || "Research Watch";
}

function frequencyText(frequency) {
  return frequency === "monthly" ? "每月" : "每周";
}

function candidateKey(candidate) {
  return [candidate.title, candidate.source, candidate.link].join("|").toLowerCase();
}

async function fetchJson(path) {
  const response = await fetch(path, { cache: "no-store" });
  if (!response.ok) throw new Error(`${path}: HTTP ${response.status}`);
  return response.json();
}

async function loadReports() {
  const index = await fetchJson(`${REPORT_ROOT}index.json`);
  const entries = Array.isArray(index.reports) ? index.reports : [];
  const loaded = await Promise.all(
    entries.map(async (entry) => {
      try {
        const data = await fetchJson(reportLink(entry.json));
        return { entry, data };
      } catch (error) {
        return { entry, data: null, error };
      }
    }),
  );
  return loaded.filter((item) => item.data);
}

function buildCandidates(reports) {
  const seen = new Set();
  const candidates = [];
  for (const report of reports) {
    for (const candidate of report.data.candidates || []) {
      if (!LABEL_ORDER.includes(candidate.relevance_label)) continue;
      const key = candidateKey(candidate);
      if (seen.has(key)) continue;
      seen.add(key);
      candidates.push({ ...candidate, watch_id: report.data.watch_id, watch_name: watchName(report.data) });
    }
  }
  return candidates.sort((a, b) => {
    const labelDifference = LABEL_ORDER.indexOf(a.relevance_label) - LABEL_ORDER.indexOf(b.relevance_label);
    return labelDifference || a.title.localeCompare(b.title);
  });
}

function renderMetrics() {
  const reports = state.reports;
  const candidates = state.candidates;
  nodes.reportCount.textContent = String(reports.length);
  nodes.highCount.textContent = String(candidates.filter((item) => item.relevance_label === "high").length);
  nodes.aiCount.textContent = String(candidates.filter((item) => item.relevance_label === "AI").length);
  nodes.failedCount.textContent = String(reports.reduce((sum, item) => sum + Number(item.data.failed_source_count || 0), 0));
  nodes.watchSummary.textContent = reports.length ? `${reports.length} 份最新报告` : "暂无报告";
}

function createReportLink(label, path) {
  if (!path) return null;
  const link = document.createElement("a");
  link.href = reportLink(path);
  link.textContent = label;
  return link;
}

function renderWatches() {
  nodes.watchGrid.textContent = "";
  if (!state.reports.length) {
    nodes.watchGrid.append(emptyState("还没有可显示的报告。运行一次 Paper Watch 后，首页会自动更新。"));
    return;
  }
  const fragment = document.createDocumentFragment();
  for (const report of state.reports) {
    const { entry, data } = report;
    const node = nodes.watchTemplate.content.firstElementChild.cloneNode(true);
    node.querySelector(".watch-frequency").textContent = frequencyText(data.frequency);
    node.querySelector(".watch-period").textContent = text(entry.period);
    node.querySelector(".watch-name").textContent = watchName(data);
    node.querySelector(".watch-meta").textContent = `${data.successful_source_count || 0}/${data.source_count || 0} 个来源成功 · ${data.raw_entry_count || 0} 条原始记录`;
    const stats = node.querySelector(".watch-stats");
    for (const [label, value] of [["High", data.candidates?.filter((item) => item.relevance_label === "high").length || 0], ["AI", data.candidates?.filter((item) => item.relevance_label === "AI").length || 0], ["Failed", data.failed_source_count || 0]]) {
      const item = document.createElement("span");
      item.innerHTML = `<b>${value}</b> ${label}`;
      stats.appendChild(item);
    }
    const links = node.querySelector(".watch-links");
    for (const [label, path] of [["Markdown", entry.markdown], ["JSON", entry.json], ["Excel", entry.xlsx]]) {
      const link = createReportLink(label, path);
      if (link) links.appendChild(link);
    }
    fragment.appendChild(node);
  }
  nodes.watchGrid.appendChild(fragment);
}

function counts(items, getKey) {
  const values = new Map();
  for (const item of items) {
    const key = getKey(item);
    values.set(key, (values.get(key) || 0) + 1);
  }
  return [...values.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
}

function renderBars(container, values) {
  container.textContent = "";
  if (!values.length) {
    container.append(emptyState("暂无候选论文。"));
    return;
  }
  const maximum = values[0][1];
  const fragment = document.createDocumentFragment();
  for (const [label, count] of values.slice(0, 8)) {
    const row = document.createElement("div");
    row.className = "bar-row";
    row.innerHTML = `<span class="bar-label"></span><span class="bar-track"><i class="bar-fill"></i></span><b class="bar-count"></b>`;
    row.querySelector(".bar-label").textContent = label;
    row.querySelector(".bar-fill").style.width = `${Math.max(8, (count / maximum) * 100)}%`;
    row.querySelector(".bar-count").textContent = String(count);
    fragment.appendChild(row);
  }
  container.appendChild(fragment);
}

function renderVisuals() {
  renderBars(nodes.topicChart, counts(state.candidates, (item) => topicText(item.topic_label)));
  renderBars(nodes.sourceChart, counts(state.candidates, (item) => text(item.source)));
}

function hydrateFilters() {
  const watches = [...new Map(state.reports.map((report) => [report.data.watch_id, watchName(report.data)])).entries()];
  const topics = [...new Set(state.candidates.map((item) => topicText(item.topic_label)))].sort();
  nodes.watchFilter.innerHTML = '<option value="all">全部 watch</option>';
  nodes.topicFilter.innerHTML = '<option value="all">全部主题</option>';
  for (const [id, name] of watches) nodes.watchFilter.add(new Option(name, id));
  for (const topic of topics) nodes.topicFilter.add(new Option(topic, topic));
}

function filteredCandidates() {
  const query = state.filters.query.toLowerCase();
  return state.candidates.filter((candidate) => {
    if (state.filters.watch !== "all" && candidate.watch_id !== state.filters.watch) return false;
    if (state.filters.relevance !== "all" && candidate.relevance_label !== state.filters.relevance) return false;
    if (state.filters.topic !== "all" && topicText(candidate.topic_label) !== state.filters.topic) return false;
    if (!query) return true;
    return [candidate.title, candidate.source, candidate.matched_ai_terms?.join(" "), candidate.matched_social_terms?.join(" ")].join(" ").toLowerCase().includes(query);
  });
}

function termText(label, terms) {
  return `${label}: ${Array.isArray(terms) && terms.length ? terms.join(", ") : "-"}`;
}

function renderCandidates() {
  const candidates = filteredCandidates();
  nodes.candidateCount.textContent = `${candidates.length} 篇`;
  nodes.candidateList.textContent = "";
  if (!candidates.length) {
    nodes.candidateList.append(emptyState("当前筛选条件下没有候选论文。"));
    return;
  }
  const fragment = document.createDocumentFragment();
  for (const candidate of candidates) {
    const node = nodes.candidateTemplate.content.firstElementChild.cloneNode(true);
    const relevance = node.querySelector(".relevance-badge");
    relevance.textContent = labelText(candidate.relevance_label);
    relevance.classList.add(candidate.relevance_label);
    node.querySelector(".topic-badge").textContent = topicText(candidate.topic_label);
    node.querySelector(".candidate-watch").textContent = candidate.watch_name;
    const title = node.querySelector(".candidate-title");
    title.textContent = candidate.title;
    title.href = candidate.link || "#";
    node.querySelector(".candidate-source").textContent = text(candidate.source);
    node.querySelector(".ai-terms").textContent = termText("AI", candidate.matched_ai_terms);
    node.querySelector(".social-terms").textContent = termText("Social", candidate.matched_social_terms);
    fragment.appendChild(node);
  }
  nodes.candidateList.appendChild(fragment);
}

function emptyState(message) {
  const item = document.createElement("p");
  item.className = "empty-state";
  item.textContent = message;
  return item;
}

function bindEvents() {
  nodes.watchFilter.addEventListener("change", (event) => { state.filters.watch = event.target.value; renderCandidates(); });
  nodes.relevanceFilter.addEventListener("change", (event) => { state.filters.relevance = event.target.value; renderCandidates(); });
  nodes.topicFilter.addEventListener("change", (event) => { state.filters.topic = event.target.value; renderCandidates(); });
  nodes.searchInput.addEventListener("input", (event) => { state.filters.query = event.target.value.trim(); renderCandidates(); });
}

async function main() {
  bindEvents();
  try {
    state.reports = await loadReports();
    state.candidates = buildCandidates(state.reports);
    nodes.statusLine.textContent = state.reports.length ? `已读取 ${state.reports.length} 份最新报告；候选列表仅包含 High 与 AI 标签。` : "暂无索引报告。请运行一次 Paper Watch。";
  } catch (error) {
    nodes.statusLine.textContent = "报告索引尚未生成。运行一次 Paper Watch 后，此页面会自动显示。";
  }
  renderMetrics();
  renderWatches();
  renderVisuals();
  hydrateFilters();
  renderCandidates();
}

main();
