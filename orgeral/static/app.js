/* ── State ─────────────────────────────────────────── */
let calendar;
let tasks = [];
let currentView = "calendar"; // "calendar" | "list"
let selectedIds = new Set();

const SUBJECT_COLORS = {
  "Português":  "#27ae60",
  "Matemática": "#2980b9",
  "Ciências":   "#8e44ad",
  "História":   "#e74c3c",
  "Geografia":  "#e67e22",
  "Inglês":     "#16a085",
  "Artes":      "#bb8fce",
  "Ed. Física": "#c0392b",
  "Religião":   "#f1c40f",
  "Outros":     "#555555",
};

function subjectColor(subject) {
  return SUBJECT_COLORS[subject] || "#555555";
}

/* ── Init ──────────────────────────────────────────── */
document.addEventListener("DOMContentLoaded", async () => {
  initCalendar();
  await loadTasks();
  bindUpload();
  bindTaskForm();
  bindModal();
  bindSubjectSelects();
  bindViewToggle();
  bindListControls();
  bindBulkBar();
  bindCalendarSync();
  bindSidebarCollapse();
  bindSettings();
  bindChat();
  applyTheme(currentTheme());
});

/* ── View Toggle ───────────────────────────────────── */
function bindViewToggle() {
  document.getElementById("btnViewCalendar").addEventListener("click", () => switchView("calendar"));
  document.getElementById("btnViewList").addEventListener("click", () => switchView("list"));
}

function switchView(view) {
  currentView = view;
  selectedIds.clear();
  updateBulkBar();

  const isCalendar = view === "calendar";
  document.getElementById("calendarView").classList.toggle("hidden", !isCalendar);
  document.getElementById("listView").classList.toggle("hidden", isCalendar);
  document.getElementById("upcomingSection").classList.toggle("hidden", !isCalendar);
  document.getElementById("listControls").classList.toggle("hidden", isCalendar);
  document.getElementById("viewTitle").textContent = isCalendar ? "Calendário" : "Lista de Tarefas";
  document.getElementById("btnViewCalendar").classList.toggle("active", isCalendar);
  document.getElementById("btnViewList").classList.toggle("active", !isCalendar);

  if (!isCalendar) renderListView();
  else calendar.render();
}

/* ── Calendar ──────────────────────────────────────── */
function initCalendar() {
  const el = document.getElementById("calendar");
  calendar = new FullCalendar.Calendar(el, {
    initialView: "dayGridMonth",
    locale: "pt-br",
    headerToolbar: {
      left: "prev,next today",
      center: "title",
      right: "dayGridMonth,timeGridWeek,listWeek",
    },
    height: "100%",
    eventClick(info) {
      openModal(info.event.id);
    },
    dateClick(info) {
      document.getElementById("taskDate").value = info.dateStr;
      document.getElementById("taskTitle").focus();
    },
  });
  calendar.render();
}

/* ── Subject selects ───────────────────────────────── */
function bindSubjectSelects() {
  document.getElementById("taskSubject").addEventListener("change", function () {
    updateColorPreview("task", this.value);
  });
  document.getElementById("editSubject").addEventListener("change", function () {
    updateColorPreview("edit", this.value);
  });
}

function updateColorPreview(prefix, subject) {
  const color = subjectColor(subject);
  document.getElementById(`${prefix}ColorDot`).style.background = color;
  document.getElementById(`${prefix}ColorName`).textContent = subject;
}

/* ── Load / Render ─────────────────────────────────── */
async function loadTasks() {
  const res = await fetch("/api/tasks");
  tasks = await res.json();
  renderAll();
}

function renderAll() {
  // Calendar
  calendar.removeAllEvents();
  tasks.forEach((t) => {
    const done = t.completed;
    calendar.addEvent({
      id: String(t.id),
      title: (done ? "✓ " : "") + (t.subject ? `[${t.subject}] ${t.title}` : t.title),
      start: t.time ? `${t.date}T${t.time}` : t.date,
      backgroundColor: done ? "#333" : (t.color || "#555555"),
      textColor: done ? "#666" : "#f0f0f0",
      allDay: !t.time,
      extendedProps: t,
    });
  });

  // Task count
  const pending = tasks.filter(t => !t.completed).length;
  const total = tasks.length;
  document.getElementById("taskCount").textContent =
    total === 0 ? "" : `${pending} pendente${pending !== 1 ? "s" : ""} / ${total} total`;

  // Upcoming
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const upcoming = tasks
    .filter((t) => !t.completed && new Date(t.date + "T00:00:00") >= today)
    .sort((a, b) => a.date.localeCompare(b.date))
    .slice(0, 6);

  const ul = document.getElementById("upcomingList");
  ul.innerHTML = "";
  if (!upcoming.length) {
    ul.innerHTML = '<li class="upcoming-empty">Nenhuma tarefa futura</li>';
  } else {
    upcoming.forEach((t) => {
      const li = document.createElement("li");
      li.className = "upcoming-item";
      li.style.borderLeftColor = t.color || "#555";
      const meta = [t.subject, t.task_type].filter(Boolean).join(" · ");
      li.innerHTML = `
        <div class="up-title">${esc(t.title)}</div>
        ${meta ? `<div class="up-meta">${esc(meta)}</div>` : ""}
        <div class="up-date">${formatDate(t.date)}${t.time ? " " + t.time.slice(0,5) : ""}</div>`;
      li.onclick = () => openModal(t.id);
      ul.appendChild(li);
    });
  }

  if (currentView === "list") renderListView();
}

/* ── List View ─────────────────────────────────────── */
function bindListControls() {
  document.getElementById("listFilter").addEventListener("change", renderListView);
  document.getElementById("selectAll").addEventListener("change", function () {
    const filtered = getFilteredTasks();
    if (this.checked) {
      filtered.forEach(t => selectedIds.add(t.id));
    } else {
      filtered.forEach(t => selectedIds.delete(t.id));
    }
    renderListView();
    updateBulkBar();
  });
}

function getFilteredTasks() {
  const filter = document.getElementById("listFilter").value;
  if (filter === "pending") return tasks.filter(t => !t.completed);
  if (filter === "completed") return tasks.filter(t => t.completed);
  return tasks;
}

function renderListView() {
  const filtered = getFilteredTasks();
  const container = document.getElementById("taskList");
  container.innerHTML = "";

  if (!filtered.length) {
    container.innerHTML = '<div class="list-empty">Nenhuma tarefa encontrada.</div>';
    return;
  }

  // Group by date
  const groups = {};
  filtered.forEach(t => {
    if (!groups[t.date]) groups[t.date] = [];
    groups[t.date].push(t);
  });

  const sortedDates = Object.keys(groups).sort();
  sortedDates.forEach(date => {
    const groupEl = document.createElement("div");
    groupEl.className = "list-group";

    const header = document.createElement("div");
    header.className = "list-group-header";
    header.textContent = formatDateFull(date);
    groupEl.appendChild(header);

    groups[date].forEach(t => {
      const item = document.createElement("div");
      item.className = "list-item" + (t.completed ? " completed" : "");
      item.dataset.id = t.id;

      const checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      checkbox.className = "list-checkbox";
      checkbox.checked = selectedIds.has(t.id);
      checkbox.addEventListener("change", (e) => {
        e.stopPropagation();
        if (checkbox.checked) selectedIds.add(t.id);
        else selectedIds.delete(t.id);
        item.classList.toggle("selected", checkbox.checked);
        updateBulkBar();
        updateSelectAll();
      });

      const dot = document.createElement("span");
      dot.className = "list-dot";
      dot.style.background = t.color || "#555";

      const info = document.createElement("div");
      info.className = "list-info";
      const meta = [t.subject, t.task_type].filter(Boolean).join(" · ");
      info.innerHTML = `
        <div class="list-title">${esc(t.title)}</div>
        ${meta ? `<div class="list-meta">${esc(meta)}</div>` : ""}`;

      const checkBtn = document.createElement("button");
      checkBtn.className = "list-check-btn" + (t.completed ? " done" : "");
      checkBtn.title = t.completed ? "Marcar como pendente" : "Marcar como concluída";
      checkBtn.textContent = t.completed ? "✓" : "○";
      checkBtn.addEventListener("click", async (e) => {
        e.stopPropagation();
        await fetch(`/api/tasks/${t.id}/complete`, { method: "PATCH" });
        await loadTasks();
      });

      item.appendChild(checkbox);
      item.appendChild(dot);
      item.appendChild(info);
      item.appendChild(checkBtn);

      if (selectedIds.has(t.id)) item.classList.add("selected");

      item.addEventListener("click", () => openModal(t.id));

      groupEl.appendChild(item);
    });

    container.appendChild(groupEl);
  });

  updateSelectAll();
}

function updateSelectAll() {
  const filtered = getFilteredTasks();
  const allSelected = filtered.length > 0 && filtered.every(t => selectedIds.has(t.id));
  document.getElementById("selectAll").checked = allSelected;
}

/* ── Bulk Bar ──────────────────────────────────────── */
function bindBulkBar() {
  document.getElementById("bulkComplete").addEventListener("click", async () => {
    if (!selectedIds.size) return;
    await fetch("/api/tasks/bulk", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action: "complete", ids: [...selectedIds] }),
    });
    selectedIds.clear();
    updateBulkBar();
    await loadTasks();
  });

  document.getElementById("bulkDelete").addEventListener("click", async () => {
    if (!selectedIds.size) return;
    if (!confirm(`Excluir ${selectedIds.size} tarefa(s)?`)) return;
    await fetch("/api/tasks/bulk", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action: "delete", ids: [...selectedIds] }),
    });
    selectedIds.clear();
    updateBulkBar();
    await loadTasks();
  });
}

function updateBulkBar() {
  const bar = document.getElementById("bulkBar");
  const count = selectedIds.size;
  bar.classList.toggle("hidden", count === 0);
  document.getElementById("bulkCount").textContent =
    `${count} tarefa${count !== 1 ? "s" : ""} selecionada${count !== 1 ? "s" : ""}`;
}

/* ── Upload ────────────────────────────────────────── */
function bindUpload() {
  const area  = document.getElementById("uploadArea");
  const input = document.getElementById("fileInput");

  area.addEventListener("click", () => input.click());
  area.addEventListener("dragover", (e) => { e.preventDefault(); area.style.borderColor = "#aaa"; });
  area.addEventListener("dragleave", () => (area.style.borderColor = ""));
  area.addEventListener("drop", (e) => {
    e.preventDefault();
    area.style.borderColor = "";
    if (e.dataTransfer.files[0]) uploadFile(e.dataTransfer.files[0]);
  });
  input.addEventListener("change", () => {
    if (input.files[0]) uploadFile(input.files[0]);
    input.value = "";
  });
}

async function uploadFile(file) {
  const status = document.getElementById("uploadStatus");
  status.className = "upload-status";
  status.textContent = `Enviando ${file.name}…`;
  status.classList.remove("hidden");
  showLoader(true);

  const fd = new FormData();
  fd.append("file", file);

  try {
    const res = await fetch("/api/upload", { method: "POST", body: fd });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Erro desconhecido");
    status.classList.add("success");
    status.textContent = data.created === 0
      ? "Nenhuma tarefa encontrada no arquivo."
      : `✓ ${data.created} tarefa${data.created > 1 ? "s" : ""} criada${data.created > 1 ? "s" : ""} automaticamente!`;
    await loadTasks();
  } catch (err) {
    status.classList.add("error");
    status.textContent = `✗ ${err.message}`;
  } finally {
    showLoader(false);
  }
}

/* ── Task Form (create) ─────────────────────────────── */
function bindTaskForm() {
  document.getElementById("taskForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const subject = document.getElementById("taskSubject").value;
    const btn = e.target.querySelector('button[type="submit"]');

    const body = {
      title:       document.getElementById("taskTitle").value.trim(),
      description: document.getElementById("taskDesc").value.trim(),
      date:        document.getElementById("taskDate").value,
      time:        document.getElementById("taskTime").value || null,
      subject,
      task_type:   document.getElementById("taskType").value,
    };

    setBusy(btn, true);
    try {
      const res = await fetch("/api/tasks", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (res.ok) {
        document.getElementById("taskForm").reset();
        updateColorPreview("task", "Outros");
        await loadTasks();
        toast("Tarefa adicionada" + (window.__calendarConnected ? " e enviada à agenda" : ""), "success");
      } else {
        const data = await res.json().catch(() => ({}));
        toast(data.error || "Erro ao adicionar tarefa", "error");
      }
    } catch (err) {
      toast("Falha de conexão", "error");
    } finally {
      setBusy(btn, false);
    }
  });
}

/* ── Modal (edit / delete / complete) ──────────────── */
function bindModal() {
  document.getElementById("modalClose").onclick = closeModal;
  document.getElementById("modal").onclick = (e) => {
    if (e.target === document.getElementById("modal")) closeModal();
  };

  document.getElementById("editForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const id      = document.getElementById("editId").value;
    const subject = document.getElementById("editSubject").value;

    const body = {
      title:       document.getElementById("editTitle").value.trim(),
      description: document.getElementById("editDesc").value.trim(),
      date:        document.getElementById("editDate").value,
      time:        document.getElementById("editTime").value || null,
      subject,
      task_type:   document.getElementById("editType").value,
    };

    const res = await fetch(`/api/tasks/${id}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });

    if (res.ok) {
      closeModal();
      await loadTasks();
      toast("Tarefa atualizada", "success");
    } else {
      toast("Erro ao salvar", "error");
    }
  });

  document.getElementById("deleteBtn").onclick = async () => {
    const id = document.getElementById("editId").value;
    if (!confirm("Excluir esta tarefa?")) return;
    await fetch(`/api/tasks/${id}`, { method: "DELETE" });
    closeModal();
    await loadTasks();
    toast("Tarefa excluída", "info");
  };

  document.getElementById("completeBtn").onclick = async () => {
    const id = document.getElementById("editId").value;
    await fetch(`/api/tasks/${id}/complete`, { method: "PATCH" });
    closeModal();
    await loadTasks();
  };
}

function openModal(id) {
  const task = tasks.find((t) => String(t.id) === String(id));
  if (!task) return;

  document.getElementById("editId").value    = task.id;
  document.getElementById("editTitle").value = task.title;
  document.getElementById("editDesc").value  = task.description || "";
  document.getElementById("editDate").value  = task.date;
  document.getElementById("editTime").value  = task.time || "";

  const subject = task.subject || "Outros";
  document.getElementById("editSubject").value = subject;
  updateColorPreview("edit", subject);
  document.getElementById("editType").value = task.task_type || "";

  const completeBtn = document.getElementById("completeBtn");
  if (task.completed) {
    completeBtn.textContent = "↩ Reabrir";
    completeBtn.classList.add("btn-reopen");
    completeBtn.classList.remove("btn-complete");
  } else {
    completeBtn.textContent = "✓ Concluir";
    completeBtn.classList.add("btn-complete");
    completeBtn.classList.remove("btn-reopen");
  }

  document.getElementById("modal").classList.remove("hidden");
}

function closeModal() {
  document.getElementById("modal").classList.add("hidden");
}

/* ── Helpers ────────────────────────────────────────── */
function showLoader(show) {
  document.getElementById("loader").classList.toggle("hidden", !show);
}

function setBusy(btn, busy) {
  if (!btn) return;
  if (busy) {
    btn.disabled = true;
    btn.dataset.label = btn.textContent;
    btn.textContent = "…";
  } else {
    btn.disabled = false;
    if (btn.dataset.label) btn.textContent = btn.dataset.label;
  }
}

/* ── Google Agenda ──────────────────────────────────── */
function bindCalendarSync() {
  const btn = document.getElementById("btnSyncCalendar");
  if (!btn) return;
  btn.addEventListener("click", async () => {
    setBusy(btn, true);
    try {
      const res = await fetch("/api/calendar/sync-all", { method: "POST" });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "Erro ao sincronizar");
      toast(`${data.synced} tarefa(s) sincronizada(s) com o Google Agenda`
        + (data.failed ? ` · ${data.failed} falharam` : ""), data.failed ? "error" : "success");
    } catch (err) {
      toast(err.message, "error");
    } finally {
      setBusy(btn, false);
    }
  });
}

function formatDate(dateStr) {
  const [y, m, d] = dateStr.split("-");
  return `${d}/${m}/${y}`;
}

function formatDateFull(dateStr) {
  const [y, m, d] = dateStr.split("-");
  const date = new Date(y, m - 1, d);
  return date.toLocaleDateString("pt-BR", { weekday: "long", day: "numeric", month: "long", year: "numeric" });
}

function esc(str) {
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

/* ── Recolher Sidebar ───────────────────────────────── */
function bindSidebarCollapse() {
  const reopen = document.getElementById("btnReopen");

  function setCollapsed(collapsed) {
    document.body.classList.toggle("sidebar-collapsed", collapsed);
    reopen.classList.toggle("hidden", !collapsed);
    try { localStorage.setItem("orgeral-sidebar", collapsed ? "1" : "0"); } catch (e) {}
    // FullCalendar precisa recalcular o tamanho após a animação.
    setTimeout(() => { if (calendar) calendar.updateSize(); }, 300);
  }

  document.getElementById("btnCollapse").onclick = () => setCollapsed(true);
  reopen.onclick = () => setCollapsed(false);

  let start = false;
  try { start = localStorage.getItem("orgeral-sidebar") === "1"; } catch (e) {}
  setCollapsed(start);
}

/* ── Tema (escuro / claro) ──────────────────────────── */
function currentTheme() {
  try { return localStorage.getItem("orgeral-theme") === "light" ? "light" : "dark"; }
  catch (e) { return "dark"; }
}

function applyTheme(theme) {
  if (theme === "light") document.documentElement.setAttribute("data-theme", "light");
  else document.documentElement.removeAttribute("data-theme");
  try { localStorage.setItem("orgeral-theme", theme); } catch (e) {}
  document.querySelectorAll(".theme-btn").forEach((b) =>
    b.classList.toggle("active", b.dataset.themeValue === theme)
  );
  if (calendar) calendar.render();
}

function bindSettings() {
  const modal = document.getElementById("settingsModal");
  document.getElementById("btnSettings").onclick = () => {
    applyTheme(currentTheme()); // garante o botão certo marcado
    modal.classList.remove("hidden");
  };
  document.getElementById("settingsClose").onclick = () => modal.classList.add("hidden");
  modal.onclick = (e) => { if (e.target === modal) modal.classList.add("hidden"); };
  document.querySelectorAll(".theme-btn").forEach((b) => {
    b.onclick = () => applyTheme(b.dataset.themeValue);
  });
}

/* ── Chat com a IA ──────────────────────────────────── */
let chatHistory = []; // [{ role, content }]

function bindChat() {
  const panel = document.getElementById("chatPanel");
  const input = document.getElementById("chatInput");
  const form  = document.getElementById("chatForm");

  document.getElementById("btnChat").onclick = () => {
    panel.classList.toggle("open");
    if (panel.classList.contains("open")) {
      if (!chatHistory.length) renderChatHint();
      input.focus();
    }
  };
  document.getElementById("chatClose").onclick = () => panel.classList.remove("open");

  // Cresce conforme o texto + detecta o comando "/".
  input.addEventListener("input", () => {
    input.style.height = "auto";
    input.style.height = Math.min(input.scrollHeight, 120) + "px";
    const v = input.value;
    if (v === "/") showTaskPicker();
    else if (!v.startsWith("/")) hideTaskPicker();
    else filterTaskPicker(v.slice(1));
  });

  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); form.requestSubmit(); }
    if (e.key === "Escape") hideTaskPicker();
  });

  form.addEventListener("submit", (e) => {
    e.preventDefault();
    const text = input.value.trim();
    if (!text || text.startsWith("/")) return; // "/" é só p/ abrir o seletor
    input.value = "";
    input.style.height = "auto";
    sendChat(text);
  });
}

/* — Seletor de tarefas pendentes ("/") — */
function pendingTasks() {
  return tasks.filter((t) => !t.completed).sort((a, b) => a.date.localeCompare(b.date));
}

function showTaskPicker() { renderTaskPicker(pendingTasks()); }

function filterTaskPicker(q) {
  q = q.toLowerCase();
  renderTaskPicker(pendingTasks().filter((t) =>
    t.title.toLowerCase().includes(q) || (t.subject || "").toLowerCase().includes(q)
  ));
}

function renderTaskPicker(list) {
  const picker = document.getElementById("taskPicker");
  picker.classList.remove("hidden");
  if (!list.length) {
    picker.innerHTML = '<div class="task-picker-empty">Nenhuma tarefa pendente.</div>';
    return;
  }
  picker.innerHTML = '<div class="task-picker-header">Suas tarefas pendentes</div>';
  list.slice(0, 8).forEach((t) => {
    const el = document.createElement("div");
    el.className = "task-picker-item";
    el.innerHTML =
      `<span class="list-dot" style="background:${t.color || "#555"}"></span>` +
      `<span>${esc(t.title)} <span style="color:var(--text-muted)">· ${formatDate(t.date)}</span></span>`;
    el.onclick = () => chooseTask(t);
    picker.appendChild(el);
  });
}

function hideTaskPicker() {
  document.getElementById("taskPicker").classList.add("hidden");
}

function chooseTask(t) {
  hideTaskPicker();
  const input = document.getElementById("chatInput");
  input.value = "";
  input.style.height = "auto";
  const meta = [t.subject, t.task_type].filter(Boolean).join(" · ");
  const msg =
    `Preciso de ajuda com esta tarefa:\n\n` +
    `📌 ${t.title}` +
    (meta ? `\n${meta}` : "") +
    `\n📅 ${formatDate(t.date)}` +
    (t.description ? `\n\n${t.description}` : "") +
    `\n\nMe explique o conteúdo e como fazer, e no final me dê um prompt pronto ` +
    `pra eu colar em outra IA pra ela fazer essa tarefa por mim.`;
  sendChat(msg);
}

/* — Envio / renderização — */
async function sendChat(text) {
  const panel = document.getElementById("chatPanel");
  if (!panel.classList.contains("open")) panel.classList.add("open");
  clearChatHint();

  chatHistory.push({ role: "user", content: text });
  appendMessage("user", text);
  const typing = appendTyping();

  try {
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ messages: chatHistory }),
    });
    const data = await res.json();
    typing.remove();
    if (!res.ok) throw new Error(data.error || "Erro ao falar com a IA");
    chatHistory.push({ role: "assistant", content: data.reply });
    appendMessage("assistant", data.reply);
  } catch (err) {
    typing.remove();
    appendMessage("assistant", "⚠ " + err.message);
  }
}

function appendMessage(role, content) {
  const box = document.getElementById("chatMessages");
  const el = document.createElement("div");
  el.className = "chat-msg " + role;
  if (role === "assistant") {
    el.innerHTML = renderAssistant(content);
    bindCopyButtons(el);
  } else {
    el.textContent = content;
  }
  box.appendChild(el);
  box.scrollTop = box.scrollHeight;
  return el;
}

function appendTyping() {
  const box = document.getElementById("chatMessages");
  const el = document.createElement("div");
  el.className = "chat-msg assistant typing";
  el.textContent = "digitando…";
  box.appendChild(el);
  box.scrollTop = box.scrollHeight;
  return el;
}

// Renderiza a resposta separando blocos de código (```) p/ botão de copiar.
function renderAssistant(text) {
  const parts = String(text).split("```");
  let html = "";
  parts.forEach((p, i) => {
    if (i % 2 === 1) {
      const code = p.replace(/^[a-zA-Z]*\n/, "").trim();
      html +=
        `<div class="chat-code"><button class="chat-copy">copiar</button>` +
        `<span class="chat-code-text">${esc(code)}</span></div>`;
    } else if (p.trim()) {
      html += `<span>${esc(p)}</span>`;
    }
  });
  return html || esc(text);
}

function bindCopyButtons(el) {
  el.querySelectorAll(".chat-copy").forEach((btn) => {
    btn.onclick = () => {
      const code = btn.parentElement.querySelector(".chat-code-text").textContent;
      navigator.clipboard.writeText(code).then(() => {
        btn.textContent = "copiado!";
        setTimeout(() => (btn.textContent = "copiar"), 1500);
      });
    };
  });
}

function renderChatHint() {
  const box = document.getElementById("chatMessages");
  if (document.getElementById("chatHint")) return;
  const el = document.createElement("div");
  el.className = "chat-msg empty-hint";
  el.id = "chatHint";
  el.textContent =
    'Olá! Pergunte o que quiser ou digite "/" para escolher uma tarefa pendente e pedir ajuda.';
  box.appendChild(el);
}

function clearChatHint() {
  const h = document.getElementById("chatHint");
  if (h) h.remove();
}

/* ── Toast ──────────────────────────────────────────── */
function toast(message, type = "info") {
  let container = document.getElementById("toastContainer");
  if (!container) {
    container = document.createElement("div");
    container.id = "toastContainer";
    container.className = "toast-container";
    document.body.appendChild(container);
  }
  const el = document.createElement("div");
  el.className = `toast toast-${type}`;
  el.textContent = message;
  container.appendChild(el);
  requestAnimationFrame(() => el.classList.add("show"));
  setTimeout(() => {
    el.classList.remove("show");
    setTimeout(() => el.remove(), 250);
  }, 3200);
}
