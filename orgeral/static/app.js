/* ── State ─────────────────────────────────────────── */
let calendar;
let tasks = [];

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
});

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

/* ── Subject selects (auto color preview) ──────────── */
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
  calendar.removeAllEvents();
  tasks.forEach((t) => {
    calendar.addEvent({
      id: String(t.id),
      title: t.subject ? `[${t.subject}] ${t.title}` : t.title,
      start: t.time ? `${t.date}T${t.time}` : t.date,
      backgroundColor: t.color || "#555555",
      textColor: "#f0f0f0",
      allDay: !t.time,
      extendedProps: t,
    });
  });

  document.getElementById("taskCount").textContent =
    tasks.length === 1 ? "1 tarefa" : `${tasks.length} tarefas`;

  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const upcoming = tasks
    .filter((t) => new Date(t.date + "T00:00:00") >= today)
    .sort((a, b) => a.date.localeCompare(b.date))
    .slice(0, 6);

  const ul = document.getElementById("upcomingList");
  ul.innerHTML = "";
  if (!upcoming.length) {
    ul.innerHTML = '<li class="upcoming-empty">Nenhuma tarefa futura</li>';
    return;
  }
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

/* ── Upload ────────────────────────────────────────── */
function bindUpload() {
  const area  = document.getElementById("uploadArea");
  const input = document.getElementById("fileInput");

  area.addEventListener("click", () => input.click());

  area.addEventListener("dragover", (e) => {
    e.preventDefault();
    area.style.borderColor = "#aaa";
  });
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
    status.textContent =
      data.created === 0
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

    const body = {
      title:       document.getElementById("taskTitle").value.trim(),
      description: document.getElementById("taskDesc").value.trim(),
      date:        document.getElementById("taskDate").value,
      time:        document.getElementById("taskTime").value || null,
      subject,
      task_type:   document.getElementById("taskType").value,
    };

    const res = await fetch("/api/tasks", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });

    if (res.ok) {
      document.getElementById("taskForm").reset();
      updateColorPreview("task", "Outros");
      await loadTasks();
    }
  });
}

/* ── Modal (edit / delete) ──────────────────────────── */
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
    }
  });

  document.getElementById("deleteBtn").onclick = async () => {
    const id = document.getElementById("editId").value;
    if (!confirm("Excluir esta tarefa?")) return;
    await fetch(`/api/tasks/${id}`, { method: "DELETE" });
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

  document.getElementById("modal").classList.remove("hidden");
}

function closeModal() {
  document.getElementById("modal").classList.add("hidden");
}

/* ── Helpers ────────────────────────────────────────── */
function showLoader(show) {
  document.getElementById("loader").classList.toggle("hidden", !show);
}

function formatDate(dateStr) {
  const [y, m, d] = dateStr.split("-");
  return `${d}/${m}/${y}`;
}

function esc(str) {
  return str.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}
