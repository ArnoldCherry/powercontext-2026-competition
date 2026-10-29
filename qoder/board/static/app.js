const STATUS = {
  todo: "待开始",
  doing: "进行中",
  blocked: "阻塞",
  ready: "待提交",
  submitted: "已提交",
  scored: "已评分",
};

const state = {
  tasks: [],
  stats: {},
  directions: [],
  preferences: {},
  preferenceSource: "defaults",
  progress: [],
  memory: null,
  filters: { direction: "全部", difficulty: "全部", status: "全部", q: "" },
  editingId: null,
  initialized: false,
};

const dom = {
  boardTitle: document.getElementById("board-title"),
  memoryBadge: document.getElementById("memory-badge"),
  panelBadge: document.getElementById("memory-panel-badge"),
  refresh: document.getElementById("refresh-btn"),
  newTask: document.getElementById("new-task-btn"),
  kpi: {
    total: document.getElementById("kpi-total"),
    doing: document.getElementById("kpi-doing"),
    blocked: document.getElementById("kpi-blocked"),
    ready: document.getElementById("kpi-ready"),
    done: document.getElementById("kpi-done"),
    rate: document.getElementById("kpi-rate"),
    score: document.getElementById("kpi-score"),
    scored: document.getElementById("kpi-scored"),
  },
  attention: document.getElementById("attention"),
  search: document.getElementById("search-input"),
  filterDirection: document.getElementById("filter-direction"),
  filterDifficulty: document.getElementById("filter-difficulty"),
  filterStatus: document.getElementById("filter-status"),
  clearFilters: document.getElementById("clear-filters"),
  resultCount: document.getElementById("result-count"),
  taskList: document.getElementById("task-list"),
  progressList: document.getElementById("progress-list"),
  progressCount: document.getElementById("progress-count"),
  memoryError: document.getElementById("memory-error"),
  scopeLabel: document.getElementById("scope-label"),
  prefForm: document.getElementById("pref-form"),
  prefTitle: document.getElementById("pref-title"),
  prefDirection: document.getElementById("pref-direction"),
  prefStatus: document.getElementById("pref-status"),
  generatedAt: document.getElementById("generated-at"),
  dialog: document.getElementById("task-dialog"),
  dialogForm: document.getElementById("task-form"),
  dialogTitle: document.getElementById("dialog-title"),
  dialogEyebrow: document.getElementById("dialog-eyebrow"),
  dialogClose: document.getElementById("dialog-close"),
  dialogCancel: document.getElementById("dialog-cancel"),
  dialogSave: document.getElementById("dialog-save"),
  formError: document.getElementById("form-error"),
  fields: {
    id: document.getElementById("field-id"),
    title: document.getElementById("field-title"),
    direction: document.getElementById("field-direction"),
    difficulty: document.getElementById("field-difficulty"),
    status: document.getElementById("field-status"),
    owner: document.getElementById("field-owner"),
    priority: document.getElementById("field-priority"),
    repo: document.getElementById("field-repo"),
    branch: document.getElementById("field-branch"),
    commit: document.getElementById("field-commit"),
    taskNumber: document.getElementById("field-task-number"),
    score: document.getElementById("field-score"),
    submissionUrl: document.getElementById("field-submission-url"),
    next: document.getElementById("field-next"),
    evidence: document.getElementById("field-evidence"),
    notes: document.getElementById("field-notes"),
  },
  directionOptions: document.getElementById("direction-options"),
  toast: document.getElementById("toast"),
};

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = text;
  return node;
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
  });
  let data = null;
  try {
    data = await response.json();
  } catch {
    data = null;
  }
  if (!response.ok) {
    const detail = data && data.detail ? data.detail : `请求失败（${response.status}）`;
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return data;
}

function setOptions(select, values, current, allLabel = "全部") {
  const previous = current === undefined ? select.value : current;
  select.replaceChildren();
  for (const value of values) {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = value === "全部" ? allLabel : STATUS[value] || value;
    select.append(option);
  }
  if ([...select.options].some((option) => option.value === previous)) {
    select.value = previous;
  }
}

function setDocumentTitle(title) {
  document.title = `${title} · Qoder × OceanBase`;
}

function populateOptions() {
  const directions = ["全部", ...state.directions];
  setOptions(dom.filterDirection, directions, dom.filterDirection.value || "全部", "全部方向");
  setOptions(dom.prefDirection, directions, dom.prefDirection.value || "全部", "全部方向");
  setOptions(dom.filterStatus, ["全部", ...Object.keys(STATUS)], dom.filterStatus.value || "全部", "全部状态");
  setOptions(dom.prefStatus, ["全部", ...Object.keys(STATUS)], dom.prefStatus.value || "全部", "全部状态");
  setOptions(dom.filterDifficulty, ["全部", "S", "A", "B"], dom.filterDifficulty.value || "全部", "全部难度");
  dom.directionOptions.replaceChildren();
  for (const direction of state.directions) {
    const option = document.createElement("option");
    option.value = direction;
    dom.directionOptions.append(option);
  }
}

function applyPreferences(preferences) {
  dom.boardTitle.textContent = preferences.board_title || "生态赛交付看板";
  setDocumentTitle(dom.boardTitle.textContent);
  dom.prefTitle.value = preferences.board_title || "生态赛交付看板";
  const direction = preferences.default_direction || "全部";
  const status = preferences.default_status || "全部";
  const difficulty = preferences.default_difficulty || "全部";
  state.filters = {
    direction: [...dom.filterDirection.options].some((option) => option.value === direction) ? direction : "全部",
    status: [...dom.filterStatus.options].some((option) => option.value === status) ? status : "全部",
    difficulty: [...dom.filterDifficulty.options].some((option) => option.value === difficulty) ? difficulty : "全部",
    q: "",
  };
  dom.filterDirection.value = state.filters.direction;
  dom.filterStatus.value = state.filters.status;
  dom.filterDifficulty.value = state.filters.difficulty;
  dom.prefDirection.value = state.filters.direction;
  dom.prefStatus.value = state.filters.status;
  dom.search.value = "";
}

function syncPreferenceForm() {
  dom.prefTitle.value = state.preferences.board_title || "生态赛交付看板";
  dom.prefDirection.value = [...dom.prefDirection.options].some(
    (option) => option.value === (state.preferences.default_direction || "全部"),
  )
    ? state.preferences.default_direction || "全部"
    : "全部";
  dom.prefStatus.value = [...dom.prefStatus.options].some(
    (option) => option.value === (state.preferences.default_status || "全部"),
  )
    ? state.preferences.default_status || "全部"
    : "全部";
}

async function refresh(first = false) {
  const data = await api("/api/bootstrap");
  state.tasks = data.tasks || [];
  state.stats = data.stats || {};
  state.directions = data.directions || [];
  state.preferences = data.preferences || {};
  state.preferenceSource = data.preference_source || "defaults";
  state.progress = data.progress || [];
  state.memory = data.memory || null;
  populateOptions();
  if (first) {
    applyPreferences(state.preferences);
  } else {
    dom.boardTitle.textContent = state.preferences.board_title || dom.boardTitle.textContent;
    setDocumentTitle(dom.boardTitle.textContent);
    syncPreferenceForm();
  }
  renderStats();
  renderAttention();
  renderTasks();
  renderMemory();
  dom.generatedAt.textContent = data.generated_at ? `更新于 ${formatDate(data.generated_at)}` : "";
  state.initialized = true;
}

function renderStats() {
  const stats = state.stats;
  dom.kpi.total.textContent = stats.total ?? 0;
  dom.kpi.doing.textContent = stats.doing ?? 0;
  dom.kpi.blocked.textContent = stats.blocked ?? 0;
  dom.kpi.ready.textContent = stats.ready ?? 0;
  dom.kpi.done.textContent = stats.done ?? 0;
  dom.kpi.rate.textContent = `完成率 ${stats.completion_rate ?? 0}%`;
  dom.kpi.score.textContent = stats.score_sum ?? 0;
  dom.kpi.scored.textContent = `已评分任务 ${stats.score_count ?? 0} 个`;
}

function renderAttention() {
  const blocked = state.tasks.filter((task) => task.status === "blocked");
  const ready = state.tasks.filter((task) => task.status === "ready");
  dom.attention.replaceChildren();
  if (!blocked.length && !ready.length) {
    dom.attention.hidden = true;
    return;
  }
  dom.attention.hidden = false;
  const label = el("p", null, "需要关注");
  dom.attention.append(label);
  const items = [
    ...blocked.slice(0, 4).map((task) => ({ task, kind: "阻塞", status: "blocked" })),
    ...ready.slice(0, 3).map((task) => ({ task, kind: "待提交", status: "ready" })),
  ];
  for (const item of items) {
    const button = el("button", "attention-item", `${item.kind} · ${item.task.id} ${item.task.title}`);
    button.type = "button";
    button.title = `${item.task.title}${item.task.next_action ? ` · ${item.task.next_action}` : ""}`;
    button.addEventListener("click", () => {
      state.filters.status = item.status;
      dom.filterStatus.value = item.status;
      renderTasks();
      dom.taskList.scrollIntoView({ behavior: "smooth", block: "start" });
    });
    dom.attention.append(button);
  }
}

function filteredTasks() {
  const query = state.filters.q.trim().toLowerCase();
  return state.tasks.filter((task) => {
    if (state.filters.direction !== "全部" && task.direction !== state.filters.direction) return false;
    if (state.filters.difficulty !== "全部" && task.difficulty !== state.filters.difficulty) return false;
    if (state.filters.status !== "全部" && task.status !== state.filters.status) return false;
    if (!query) return true;
    return [
      task.id,
      task.title,
      task.owner,
      task.repo,
      task.branch,
      task.next_action,
      task.notes,
    ]
      .filter(Boolean)
      .some((value) => String(value).toLowerCase().includes(query));
  });
}

function renderTasks() {
  const tasks = filteredTasks();
  dom.taskList.replaceChildren();
  dom.resultCount.textContent = `显示 ${tasks.length} / ${state.tasks.length} 个任务 · 阻塞与待提交优先`;
  if (!tasks.length) {
    const empty = el("div", "empty-state");
    empty.append(el("h3", null, "没有匹配的任务"));
    empty.append(el("p", null, "调整筛选条件，或新建一个任务。"));
    dom.taskList.append(empty);
    return;
  }
  for (const task of tasks) {
    dom.taskList.append(createTaskCard(task));
  }
}

function createTaskCard(task) {
  const card = el("article", "task-card");
  card.dataset.status = task.status;

  const heading = el("div", "task-heading");
  heading.append(el("span", "task-id", task.id));
  heading.append(el("h3", null, task.title));
  heading.append(el("span", "chip direction", task.direction || "未分类"));
  const difficulty = el("span", "chip difficulty", task.difficulty || "–");
  difficulty.dataset.level = task.difficulty || "";
  heading.append(difficulty);
  if (task.owner) heading.append(el("span", "chip", task.owner));

  const statusWrap = el("label", "status-control");
  const statusSelect = document.createElement("select");
  statusSelect.setAttribute("aria-label", `更新 ${task.id} 状态`);
  statusSelect.dataset.status = task.status;
  for (const [value, label] of Object.entries(STATUS)) {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = label;
    statusSelect.append(option);
  }
  statusSelect.value = task.status;
  statusSelect.addEventListener("change", () => updateStatus(task, statusSelect));
  statusWrap.append(statusSelect);
  heading.append(statusWrap);

  const editButton = el("button", "icon-button");
  editButton.type = "button";
  editButton.title = "编辑任务";
  editButton.setAttribute("aria-label", `编辑 ${task.id}`);
  editButton.innerHTML =
    '<svg viewBox="0 0 20 20" aria-hidden="true"><path d="M13.9 2.6a2 2 0 0 1 2.8 0l.7.7a2 2 0 0 1 0 2.8l-8.7 8.7-3.9 1.1 1.1-3.9 8-9.4zm1.4 1.4-8 9.4 1.4 1.4 8.7-8.7-2.1-2.1z"/></svg>';
  editButton.addEventListener("click", () => openEditor(task));
  heading.append(editButton);
  card.append(heading);

  const next = el("p", `next-action${task.next_action ? "" : " is-empty"}`);
  next.append(el("span", null, "下一步"));
  next.append(document.createTextNode(task.next_action || "尚未填写下一步"));
  card.append(next);

  const facts = el("dl", "facts");
  facts.append(fact("仓库", task.repo || "—", task.repo, Boolean(task.repo)));
  facts.append(fact("分支", task.branch || "—", task.branch, Boolean(task.branch), true));
  facts.append(fact("Commit", task.commit_hash ? shortSha(task.commit_hash) : "—", task.commit_hash, Boolean(task.commit_hash), true));
  facts.append(fact("提交编号", task.task_number || "—", task.task_number, Boolean(task.task_number), true));
  card.append(facts);

  const bottom = el("div", "task-bottom");
  const evidence = el("div", "evidence-list");
  const links = Array.isArray(task.evidence) ? task.evidence : [];
  if (!links.length) {
    evidence.append(el("span", "evidence-empty", "暂无证据链接"));
  } else {
    for (const link of links.slice(0, 4)) {
      evidence.append(evidenceChip(link));
    }
    if (links.length > 4) evidence.append(el("span", "evidence-empty", `+${links.length - 4}`));
  }
  bottom.append(evidence);

  const right = el("div", "task-meta-right");
  if (task.score !== null && task.score !== undefined) {
    const score = el("span", "score-badge");
    score.append(document.createTextNode("得分 "));
    score.append(el("b", null, String(task.score)));
    right.append(score);
  }
  if (task.submission_url) {
    const link = el("a", "evidence-chip", "提交页");
    link.href = safeUrl(task.submission_url) || "#";
    link.target = "_blank";
    link.rel = "noreferrer";
    link.title = task.submission_url;
    right.append(link);
  }
  right.append(el("span", "updated-at", task.updated_at ? `更新 ${formatDate(task.updated_at)}` : ""));
  bottom.append(right);
  card.append(bottom);

  return card;
}

function fact(label, value, title, hasValue, mono = false) {
  const wrapper = el("div", "fact");
  wrapper.append(el("dt", null, label));
  const dd = el("dd", `${mono ? "mono " : ""}${hasValue ? "" : "muted-dash"}`, value);
  if (title) dd.title = title;
  wrapper.append(dd);
  return wrapper;
}

function evidenceChip(link) {
  const url = safeUrl(link.url);
  const chip = el(url ? "a" : "span", "evidence-chip");
  const icon = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  icon.setAttribute("viewBox", "0 0 20 20");
  icon.setAttribute("aria-hidden", "true");
  const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
  path.setAttribute("d", "M10.6 2.6h6.8v6.8h-2V6.1l-6.7 6.7-1.4-1.4 6.7-6.7h-3.4v-2zM4 4h5v2H6v8h8v-3h2v5H4V4z");
  icon.append(path);
  chip.append(icon);
  chip.append(document.createTextNode(link.label || link.url));
  chip.title = `${link.label || "证据"} · ${link.url}`;
  if (url) {
    chip.href = url;
    chip.target = "_blank";
    chip.rel = "noreferrer";
  }
  return chip;
}

function safeUrl(value) {
  if (!value) return "";
  try {
    const url = new URL(value, window.location.origin);
    return ["http:", "https:"].includes(url.protocol) ? url.href : "";
  } catch {
    return "";
  }
}

function shortSha(value) {
  return value.length > 10 ? value.slice(0, 10) : value;
}

function formatDate(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(date);
}

function renderMemory() {
  const memory = state.memory || { status: "offline" };
  const online = memory.status === "online";
  const label = online ? "记忆在线" : memory.status === "error" ? "记忆异常" : "记忆离线";
  for (const badge of [dom.memoryBadge, dom.panelBadge]) {
    badge.textContent = "";
    const dot = el("span", "pulse");
    badge.append(dot, document.createTextNode(label));
    badge.classList.toggle("is-online", online);
    badge.classList.toggle("is-offline", !online);
    badge.classList.remove("is-pending");
  }

  dom.progressList.replaceChildren();
  if (!state.progress.length) {
    dom.progressList.append(el("li", "timeline-empty", "还没有进展记忆，修改任务状态后会出现在这里。"));
  } else {
    for (const entry of state.progress) {
      const item = el("li", "timeline-item");
      item.dataset.status = entry.status || "";
      const top = el("div", "timeline-top");
      top.append(el("strong", null, entry.task || "任务"));
      top.append(el("span", null, STATUS[entry.status] || entry.status || "进展"));
      top.append(el("time", null, entry.at ? formatDate(entry.at) : ""));
      item.append(top);
      if (entry.next) item.append(el("p", "timeline-next", entry.next));
      dom.progressList.append(item);
    }
  }
  dom.progressCount.textContent = `${state.progress.length} 条`;

  if (online) {
    dom.memoryError.hidden = true;
    dom.memoryError.textContent = "";
    const scope = memory.scope_id || state.memory?.scope_id;
    dom.scopeLabel.textContent = scope ? `Scope ${scope}` : "Scope 尚未建立";
    dom.scopeLabel.title = memory.url || "";
  } else {
    dom.memoryError.hidden = false;
    dom.memoryError.textContent =
      memory.status === "error"
        ? memory.error || "PowerContext 记忆读写失败。"
        : "PowerContext 未连接。任务数据仍会保存，但偏好与进展记忆暂不可用。";
    dom.scopeLabel.textContent = memory.url ? `尝试连接 ${memory.url}` : "";
  }
}

async function updateStatus(task, select) {
  const next = select.value;
  select.disabled = true;
  try {
    const result = await api(`/api/tasks/${encodeURIComponent(task.id)}`, {
      method: "PATCH",
      body: JSON.stringify({ status: next }),
    });
    const memorySaved = result.memory && result.memory.status === "saved";
    showToast(`${task.id} 已更新为「${STATUS[next]}」${memorySaved ? "，进展已写入 PowerContext" : ""}`, memorySaved ? "ok" : "warn");
    await refresh();
    if (!memorySaved && result.memory && result.memory.status === "error") {
      showToast("任务已保存，但记忆写入失败：请检查 PowerContext", "error");
    }
  } catch (error) {
    select.value = task.status;
    showToast(error.message, "error");
  } finally {
    select.disabled = false;
  }
}

function openEditor(task) {
  state.editingId = task ? task.id : null;
  dom.dialogTitle.textContent = task ? `编辑 ${task.id}` : "新增任务";
  dom.dialogEyebrow.textContent = task ? "EDIT TASK" : "NEW TASK";
  dom.dialogSave.textContent = task ? "保存任务" : "创建任务";
  dom.formError.hidden = true;
  dom.formError.textContent = "";

  const values = task || {
    id: "",
    title: "",
    direction: state.filters.direction !== "全部" ? state.filters.direction : state.directions[0] || "",
    difficulty: "A",
    status: "todo",
    owner: "",
    priority: "P1",
    repo: "",
    branch: "",
    commit_hash: "",
    submission_url: "",
    task_number: "",
    score: null,
    next_action: "",
    evidence: [],
    notes: "",
  };

  dom.fields.id.value = values.id || "";
  dom.fields.id.disabled = Boolean(task);
  dom.fields.title.value = values.title || "";
  dom.fields.direction.value = values.direction || "";
  dom.fields.difficulty.value = values.difficulty || "A";
  dom.fields.status.value = values.status || "todo";
  dom.fields.owner.value = values.owner || "";
  dom.fields.priority.value = values.priority || "P1";
  dom.fields.repo.value = values.repo || "";
  dom.fields.branch.value = values.branch || "";
  dom.fields.commit.value = values.commit_hash || "";
  dom.fields.taskNumber.value = values.task_number || "";
  dom.fields.score.value = values.score ?? "";
  dom.fields.submissionUrl.value = values.submission_url || "";
  dom.fields.next.value = values.next_action || "";
  dom.fields.evidence.value = (values.evidence || [])
    .map((link) => `${link.label || "证据"} | ${link.url}`)
    .join("\n");
  dom.fields.notes.value = values.notes || "";
  dom.dialog.showModal();
  dom.fields.title.focus();
}

function closeEditor() {
  dom.dialog.close();
  state.editingId = null;
}

function parseEvidence(text) {
  return text
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => {
      const index = line.indexOf("|");
      if (index === -1) return { label: "证据", url: line };
      return {
        label: line.slice(0, index).trim() || "证据",
        url: line.slice(index + 1).trim(),
      };
    })
    .filter((link) => link.url);
}

async function submitTask(event) {
  event.preventDefault();
  dom.formError.hidden = true;
  const id = state.editingId || dom.fields.id.value.trim();
  const title = dom.fields.title.value.trim();
  if (!id) return showFormError("任务编号不能为空");
  if (!/^[A-Za-z0-9][A-Za-z0-9._-]{0,31}$/.test(id)) {
    return showFormError("任务编号只能包含字母、数字、点、下划线和短横线");
  }
  if (!title) return showFormError("任务标题不能为空");

  const payload = {
    title,
    direction: dom.fields.direction.value.trim() || "未分类",
    difficulty: dom.fields.difficulty.value,
    status: dom.fields.status.value,
    owner: dom.fields.owner.value.trim(),
    priority: dom.fields.priority.value,
    repo: dom.fields.repo.value.trim(),
    branch: dom.fields.branch.value.trim(),
    commit_hash: dom.fields.commit.value.trim(),
    submission_url: dom.fields.submissionUrl.value.trim(),
    task_number: dom.fields.taskNumber.value.trim(),
    score: dom.fields.score.value === "" ? null : Number(dom.fields.score.value),
    next_action: dom.fields.next.value.trim(),
    evidence: parseEvidence(dom.fields.evidence.value),
    notes: dom.fields.notes.value.trim(),
  };

  dom.dialogSave.disabled = true;
  try {
    if (state.editingId) {
      const result = await api(`/api/tasks/${encodeURIComponent(state.editingId)}`, {
        method: "PATCH",
        body: JSON.stringify(payload),
      });
      if (result.memory && result.memory.status === "error") {
        showToast("任务已保存，但 PowerContext 记忆写入失败", "error");
      } else {
        showToast(`${state.editingId} 已保存`, "ok");
      }
    } else {
      payload.id = id;
      await api("/api/tasks", { method: "POST", body: JSON.stringify(payload) });
      showToast(`任务 ${id} 已创建`, "ok");
    }
    closeEditor();
    await refresh();
  } catch (error) {
    showFormError(error.message);
  } finally {
    dom.dialogSave.disabled = false;
  }
}

function showFormError(message) {
  dom.formError.textContent = message;
  dom.formError.hidden = false;
}

async function savePreferences(event) {
  event.preventDefault();
  const payload = {
    board_title: dom.prefTitle.value.trim() || "生态赛交付看板",
    default_direction: dom.prefDirection.value,
    default_status: dom.prefStatus.value,
    default_difficulty: state.preferences.default_difficulty || "全部",
  };
  try {
    const result = await api("/api/preferences", { method: "PUT", body: JSON.stringify(payload) });
    state.preferences = result.preferences;
    state.preferenceSource = "powercontext";
    dom.boardTitle.textContent = result.preferences.board_title;
    setDocumentTitle(result.preferences.board_title);
    showToast("偏好已写入 PowerContext，下次打开会自动恢复", "ok");
    await refresh();
  } catch (error) {
    showToast(error.message, "error");
  }
}

function applyFilterChange() {
  state.filters.direction = dom.filterDirection.value;
  state.filters.difficulty = dom.filterDifficulty.value;
  state.filters.status = dom.filterStatus.value;
  state.filters.q = dom.search.value;
  renderTasks();
}

function clearFilters() {
  state.filters = { direction: "全部", difficulty: "全部", status: "全部", q: "" };
  dom.filterDirection.value = "全部";
  dom.filterDifficulty.value = "全部";
  dom.filterStatus.value = "全部";
  dom.search.value = "";
  renderTasks();
}

let searchTimer = null;
function onSearchInput() {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(applyFilterChange, 160);
}

let toastTimer = null;
function showToast(message, kind = "ok") {
  clearTimeout(toastTimer);
  dom.toast.textContent = message;
  dom.toast.classList.toggle("error", kind === "error");
  dom.toast.classList.add("show");
  toastTimer = setTimeout(() => dom.toast.classList.remove("show"), 3200);
}

function bindEvents() {
  dom.filterDirection.addEventListener("change", applyFilterChange);
  dom.filterDifficulty.addEventListener("change", applyFilterChange);
  dom.filterStatus.addEventListener("change", applyFilterChange);
  dom.search.addEventListener("input", onSearchInput);
  dom.clearFilters.addEventListener("click", clearFilters);
  dom.refresh.addEventListener("click", async () => {
    try {
      await refresh();
      showToast("看板与记忆已刷新", "ok");
    } catch (error) {
      showToast(error.message, "error");
    }
  });
  dom.newTask.addEventListener("click", () => openEditor(null));
  dom.prefForm.addEventListener("submit", savePreferences);
  dom.dialogForm.addEventListener("submit", submitTask);
  dom.dialogClose.addEventListener("click", closeEditor);
  dom.dialogCancel.addEventListener("click", closeEditor);
  dom.dialog.addEventListener("cancel", () => {
    state.editingId = null;
  });
}

async function boot() {
  bindEvents();
  try {
    await refresh(true);
  } catch (error) {
    dom.taskList.replaceChildren();
    const empty = el("div", "empty-state");
    empty.append(el("h3", null, "无法加载看板"));
    empty.append(el("p", null, error.message));
    dom.taskList.append(empty);
    dom.resultCount.textContent = "加载失败";
    showToast(error.message, "error");
  }
}

boot();
