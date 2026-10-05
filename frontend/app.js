/**
 * Document Intake Assistant — frontend application
 *
 * Architecture: thin client. All business logic lives on the server.
 * The browser only:
 *   1. Calls the REST API
 *   2. Renders the response (escaped — never raw HTML injection except
 *      for our own controlled markdown→HTML converter below)
 *   3. Manages simple UI state (active tab, which field is being edited)
 *
 * Security: user content is always set via textContent. The only place
 * innerHTML is used is our own renderMarkdown() function which escapes
 * all text before building HTML tags.
 */

"use strict";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const API = {
  health:   () => fetch("/api/health"),
  sessions: () => fetch("/api/sessions", { method: "POST" }),
  session:  (id) => fetch(`/api/sessions/${id}`),
  message:  (id, text) =>
    fetch(`/api/sessions/${id}/messages`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: text }),
    }),
  patch:    (id, updates) =>
    fetch(`/api/sessions/${id}/state`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ updates }),
    }),
  reset:    (id) => fetch(`/api/sessions/${id}/reset`, { method: "POST" }),
};

const FIELD_LABELS = {
  "full_name":                "Full name",
  "home_address":             "Home address",
  "covers_worldwide_assets":  "Worldwide assets",
  "has_children":             "Has children",
  "children_names":           "Children's names",
  "executor.name":            "Executor name",
  "executor.relationship":    "Executor relationship",
  "specific_gifts":           "Specific gifts",
  "additional_wishes":        "Additional wishes",
};

const BOOL_FIELDS = new Set(["covers_worldwide_assets", "has_children"]);
const LIST_FIELDS = new Set(["children_names", "specific_gifts"]);

// ---------------------------------------------------------------------------
// Application state (UI-only — source of truth is the server)
// ---------------------------------------------------------------------------

let sessionId    = null;
let editingField = null;
let lastFields   = [];         // latest field array from the server
let lastMarkdown = "";         // latest document markdown from the server

// ---------------------------------------------------------------------------
// Boot
// ---------------------------------------------------------------------------

document.addEventListener("DOMContentLoaded", async () => {
  setupTabs();
  setupChatForm();
  setupNewSessionButton();
  setupDownloadButtons();

  await checkHealth();
  await startSession();
});

// ---------------------------------------------------------------------------
// Health check
// ---------------------------------------------------------------------------

async function checkHealth() {
  try {
    const res  = await API.health();
    const data = await res.json();
    if (!data.llm_configured) {
      document.getElementById("llm-banner").hidden = false;
    }
  } catch {
    // Health check failure is non-fatal
  }
}

// ---------------------------------------------------------------------------
// Session management
// ---------------------------------------------------------------------------

async function startSession() {
  setLoading(true);
  try {
    const res  = await API.sessions();
    const data = await res.json();
    if (data.error) { showInlineError(data.error.message); return; }
    sessionId = data.session_id;
    renderSession(data);
  } catch {
    showInlineError("Could not connect to the server. Is it running?");
  } finally {
    setLoading(false);
  }
}

async function resetSession() {
  if (!sessionId) return;
  setLoading(true);
  try {
    const res  = await API.reset(sessionId);
    const data = await res.json();
    if (data.error) { showInlineError(data.error.message); return; }
    editingField = null;
    renderSession(data);
  } finally {
    setLoading(false);
  }
}

// ---------------------------------------------------------------------------
// Send message
// ---------------------------------------------------------------------------

async function sendMessage(text) {
  if (!sessionId || !text.trim()) return;

  appendChatBubble("user", text);
  setInputEnabled(false);
  setLoading(true);
  clearWarnings();

  try {
    const res  = await API.message(sessionId, text);
    const data = await res.json();

    if (data.error) {
      showInlineError(data.error.message);
      return;
    }

    renderSession(data);

  } catch {
    showInlineError("Network error. Please try again.");
  } finally {
    setInputEnabled(true);
    setLoading(false);
    focusChatInput();
  }
}

// ---------------------------------------------------------------------------
// Direct state edit (PATCH)
// ---------------------------------------------------------------------------

async function applyDirectEdit(fieldPath, rawValue) {
  if (!sessionId) return;

  const update = buildUpdatePayload(fieldPath, rawValue);
  if (!update) return;

  setLoading(true);
  try {
    const res  = await API.patch(sessionId, [update]);
    const data = await res.json();
    if (data.error) { showInlineError(data.error.message); return; }
    editingField = null;
    renderSession(data);
  } finally {
    setLoading(false);
  }
}

function buildUpdatePayload(fieldPath, rawValue) {
  const trimmed = rawValue.trim();

  if (BOOL_FIELDS.has(fieldPath)) {
    const lower = trimmed.toLowerCase();
    if (lower === "true"  || lower === "yes") return { field: fieldPath, value_bool: true  };
    if (lower === "false" || lower === "no")  return { field: fieldPath, value_bool: false };
    if (trimmed === "")                        return { field: fieldPath, clear: true       };
    return null;
  }

  if (LIST_FIELDS.has(fieldPath)) {
    if (trimmed === "") return { field: fieldPath, clear: true };
    const items = trimmed.split(",").map(s => s.trim()).filter(Boolean);
    return { field: fieldPath, value_list: items };
  }

  if (trimmed === "") return { field: fieldPath, clear: true };
  return { field: fieldPath, value_text: trimmed };
}

// ---------------------------------------------------------------------------
// Markdown renderer
// A small, safe renderer. All text is HTML-escaped before tagging.
// Supports: headings (#, ##), bold (**), italic (*), blockquote (>),
//           unordered lists (-/*), horizontal rules (---), paragraphs.
// ---------------------------------------------------------------------------

function escHtml(str) {
  return str
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function inlineMarkdown(text) {
  // Bold **text**
  text = escHtml(text)
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/\*(.+?)\*/g,     "<em>$1</em>");
  return text;
}

function renderMarkdown(md) {
  const lines  = md.split("\n");
  let   html   = "";
  let   inList = false;

  const closeList = () => { if (inList) { html += "</ul>"; inList = false; } };

  for (let i = 0; i < lines.length; i++) {
    const raw  = lines[i];
    const line = raw.trimEnd();

    // Horizontal rule
    if (/^-{3,}$/.test(line.trim())) {
      closeList();
      html += "<hr>";
      continue;
    }

    // ATX headings
    const h2 = line.match(/^##\s+(.+)/);
    if (h2) { closeList(); html += `<h2>${inlineMarkdown(h2[1])}</h2>`; continue; }
    const h1 = line.match(/^#\s+(.+)/);
    if (h1) { closeList(); html += `<h1>${inlineMarkdown(h1[1])}</h1>`; continue; }

    // Blockquote
    const bq = line.match(/^>\s*(.*)/);
    if (bq) { closeList(); html += `<blockquote>${inlineMarkdown(bq[1])}</blockquote>`; continue; }

    // List item
    const li = line.match(/^[-*]\s+(.*)/);
    if (li) {
      if (!inList) { html += "<ul>"; inList = true; }
      html += `<li>${inlineMarkdown(li[1])}</li>`;
      continue;
    }

    // Blank line
    if (line.trim() === "") {
      closeList();
      continue;
    }

    // Paragraph
    closeList();
    html += `<p>${inlineMarkdown(line)}</p>`;
  }

  closeList();
  return html;
}

// ---------------------------------------------------------------------------
// JSON syntax highlighter
// ---------------------------------------------------------------------------

function highlightJson(obj) {
  const raw = JSON.stringify(obj, null, 2);
  return raw.replace(
    /("(\\u[a-fA-F0-9]{4}|\\[^u]|[^\\"])*"(\s*:)?|\b(true|false|null)\b|-?\d+(?:\.\d*)?(?:[eE][+\-]?\d+)?)/g,
    (match) => {
      if (/^"/.test(match)) {
        if (/:$/.test(match)) return `<span class="json-key">${escHtml(match)}</span>`;
        return `<span class="json-str">${escHtml(match)}</span>`;
      }
      if (/true|false/.test(match)) return `<span class="json-bool">${match}</span>`;
      if (/null/.test(match))       return `<span class="json-null">${match}</span>`;
      return `<span class="json-num">${match}</span>`;
    }
  );
}

// ---------------------------------------------------------------------------
// Render helpers
// ---------------------------------------------------------------------------

function renderSession(data) {
  renderMessages(data.messages || []);
  renderStateTable(data.fields  || []);
  renderDraft(data.document_markdown || "");
  renderWarnings(data.warnings || []);
  renderConflict(data.pending_conflict);

  // Cache for downloads
  lastFields   = data.fields || [];
  lastMarkdown = data.document_markdown || "";

  // Update JSON panel if it's visible
  renderJsonPanel(data.fields || []);
}

function renderMessages(messages) {
  const container = document.getElementById("chat-messages");
  container.innerHTML = "";
  for (const msg of messages) {
    appendChatBubble(msg.role, msg.text, container);
  }
  scrollChatToBottom();
}

function appendChatBubble(role, text, container) {
  container = container || document.getElementById("chat-messages");
  const div = document.createElement("div");
  div.className = `msg msg-${role === "user" ? "user" : "assistant"}`;

  if (role === "user") {
    // User messages: plain text, escape only
    div.textContent = text;
  } else {
    // Assistant messages: render lightweight markdown
    div.innerHTML = renderMarkdown(text);
  }

  container.appendChild(div);
  scrollChatToBottom();
}

function scrollChatToBottom() {
  const c = document.getElementById("chat-messages");
  c.scrollTop = c.scrollHeight;
}

function renderDraft(markdown) {
  const el   = document.getElementById("draft-content");
  lastMarkdown = markdown;
  // Render as formatted HTML using our markdown renderer
  el.innerHTML = renderMarkdown(markdown);
}

function renderJsonPanel(fields) {
  const el = document.getElementById("json-content");
  // Build a clean object from fields
  const obj = {};
  for (const f of fields) {
    // Convert dot-path "executor.name" → nested object
    const parts = f.path.split(".");
    let cursor = obj;
    for (let i = 0; i < parts.length - 1; i++) {
      if (!(parts[i] in cursor)) cursor[parts[i]] = {};
      cursor = cursor[parts[i]];
    }
    cursor[parts[parts.length - 1]] = {
      value:  f.value,
      status: f.status,
    };
  }
  el.innerHTML = highlightJson(obj);
}

function renderWarnings(warnings) {
  const area = document.getElementById("warnings-area");
  if (!warnings || warnings.length === 0) {
    area.hidden  = true;
    area.innerHTML = "";
    return;
  }
  area.hidden = false;
  area.innerHTML = "";
  const label = document.createElement("strong");
  label.textContent = "⚠ Notes from this turn:";
  area.appendChild(label);
  const ul = document.createElement("ul");
  for (const w of warnings) {
    const li = document.createElement("li");
    li.textContent = w;
    ul.appendChild(li);
  }
  area.appendChild(ul);
}

function renderConflict(conflict) {
  const area = document.getElementById("conflict-area");
  const span = document.getElementById("conflict-text");
  if (!conflict) {
    area.hidden     = true;
    span.textContent = "";
    return;
  }
  area.hidden     = false;
  span.textContent = " " + conflict.description;
}

function clearWarnings() {
  renderWarnings([]);
  renderConflict(null);
}

// ---------------------------------------------------------------------------
// State table
// ---------------------------------------------------------------------------

function renderStateTable(fields) {
  const tbody = document.getElementById("state-tbody");
  tbody.innerHTML = "";

  for (const field of fields) {
    const tr = document.createElement("tr");

    const tdPath = document.createElement("td");
    const pathSpan = document.createElement("span");
    pathSpan.className   = "field-path";
    pathSpan.textContent = FIELD_LABELS[field.path] || field.path;
    tdPath.appendChild(pathSpan);

    const tdVal = document.createElement("td");
    tdVal.appendChild(buildValueCell(field));

    const tdStatus = document.createElement("td");
    tdStatus.appendChild(buildBadge(field.status));

    tr.appendChild(tdPath);
    tr.appendChild(tdVal);
    tr.appendChild(tdStatus);
    tbody.appendChild(tr);
  }
}

function buildValueCell(field) {
  const wrapper = document.createElement("div");
  wrapper.className = "field-edit-row";

  if (editingField === field.path) {
    const input = buildInlineInput(field);
    wrapper.appendChild(input);

    const btnSave = document.createElement("button");
    btnSave.className   = "btn btn-ghost";
    btnSave.textContent = "Save";
    btnSave.addEventListener("click", () => applyDirectEdit(field.path, input.value));

    const btnCancel = document.createElement("button");
    btnCancel.className       = "btn btn-ghost";
    btnCancel.style.color     = "#64748b";
    btnCancel.textContent     = "Cancel";
    btnCancel.addEventListener("click", () => { editingField = null; refreshSession(); });

    wrapper.appendChild(btnSave);
    wrapper.appendChild(btnCancel);
    setTimeout(() => input.focus(), 0);
  } else {
    const display = document.createElement("span");
    display.className   = "field-value-display" + (field.status === "unknown" ? " field-value-unknown" : "");
    display.textContent = formatFieldValue(field);

    const btnEdit = document.createElement("button");
    btnEdit.className   = "btn btn-ghost";
    btnEdit.textContent = "Edit";
    btnEdit.setAttribute("aria-label", `Edit ${FIELD_LABELS[field.path] || field.path}`);
    btnEdit.addEventListener("click", () => { editingField = field.path; refreshSession(); });

    wrapper.appendChild(display);
    wrapper.appendChild(btnEdit);
  }

  return wrapper;
}

function buildInlineInput(field) {
  if (BOOL_FIELDS.has(field.path)) {
    const sel = document.createElement("select");
    sel.className = "inline-input";
    const opts = [
      { value: "",      label: "— select —" },
      { value: "true",  label: "Yes"         },
      { value: "false", label: "No"          },
    ];
    for (const o of opts) {
      const opt = document.createElement("option");
      opt.value       = o.value;
      opt.textContent = o.label;
      if (field.value !== null && field.value !== undefined) {
        if (o.value === String(field.value)) opt.selected = true;
      }
      sel.appendChild(opt);
    }
    return sel;
  }

  const input = document.createElement("input");
  input.type      = "text";
  input.className = "inline-input";

  if (LIST_FIELDS.has(field.path)) {
    input.placeholder = "comma-separated values";
    input.value       = Array.isArray(field.value) ? field.value.join(", ") : "";
  } else {
    input.value       = field.value !== null && field.value !== undefined ? String(field.value) : "";
    input.placeholder = "Enter value";
  }

  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter")  { e.preventDefault(); applyDirectEdit(field.path, input.value); }
    if (e.key === "Escape") { editingField = null; refreshSession(); }
  });

  return input;
}

function formatFieldValue(field) {
  if (field.status === "unknown" || field.value === null || field.value === undefined) {
    return "Not yet provided";
  }
  if (typeof field.value === "boolean") return field.value ? "Yes" : "No";
  if (Array.isArray(field.value)) {
    return field.value.length === 0 ? "None" : field.value.join(", ");
  }
  if (field.value === "") return "None";
  return String(field.value);
}

function buildBadge(status) {
  const span = document.createElement("span");
  span.className   = `badge badge-${status}`;
  span.textContent = status.charAt(0).toUpperCase() + status.slice(1);
  return span;
}

// ---------------------------------------------------------------------------
// Download helpers
// ---------------------------------------------------------------------------

function downloadText(filename, content) {
  const blob = new Blob([content], { type: "text/plain;charset=utf-8" });
  const url  = URL.createObjectURL(blob);
  const a    = document.createElement("a");
  a.href     = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

function downloadJson(filename, obj) {
  const blob = new Blob([JSON.stringify(obj, null, 2)], { type: "application/json;charset=utf-8" });
  const url  = URL.createObjectURL(blob);
  const a    = document.createElement("a");
  a.href     = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

function setupDownloadButtons() {
  document.getElementById("btn-download-txt").addEventListener("click", () => {
    if (!lastMarkdown) return;
    downloadText("personal-wishes.txt", lastMarkdown);
  });

  document.getElementById("btn-download-json").addEventListener("click", () => {
    if (!lastFields.length) return;
    // Build clean nested object
    const obj = {};
    for (const f of lastFields) {
      const parts = f.path.split(".");
      let cursor = obj;
      for (let i = 0; i < parts.length - 1; i++) {
        if (!(parts[i] in cursor)) cursor[parts[i]] = {};
        cursor = cursor[parts[i]];
      }
      cursor[parts[parts.length - 1]] = {
        value:  f.value,
        status: f.status,
      };
    }
    downloadJson("personal-wishes.json", obj);
  });
}

// ---------------------------------------------------------------------------
// Refresh session from server
// ---------------------------------------------------------------------------

async function refreshSession() {
  if (!sessionId) return;
  try {
    const res  = await API.session(sessionId);
    const data = await res.json();
    if (!data.error) renderSession(data);
  } catch { /* silent */ }
}

// ---------------------------------------------------------------------------
// Error display
// ---------------------------------------------------------------------------

function showInlineError(message) {
  const container = document.getElementById("chat-messages");
  const div = document.createElement("div");
  div.className            = "msg msg-assistant";
  div.style.background     = "#fee2e2";
  div.style.borderColor    = "#fca5a5";
  div.style.color          = "#991b1b";
  div.textContent          = "⚠ " + message;
  container.appendChild(div);
  scrollChatToBottom();
}

// ---------------------------------------------------------------------------
// Loading state
// ---------------------------------------------------------------------------

function setLoading(on) {
  document.getElementById("loading").hidden   = !on;
  document.getElementById("btn-send").disabled = on;
}

function setInputEnabled(on) {
  document.getElementById("chat-input").disabled = !on;
  document.getElementById("btn-send").disabled   = !on;
}

function focusChatInput() {
  document.getElementById("chat-input").focus();
}

// ---------------------------------------------------------------------------
// Tab switching
// ---------------------------------------------------------------------------

function setupTabs() {
  const tabs = [
    { btn: "tab-state", panel: "panel-state" },
    { btn: "tab-draft", panel: "panel-draft" },
    { btn: "tab-json",  panel: "panel-json"  },
  ];

  tabs.forEach(({ btn, panel }) => {
    document.getElementById(btn).addEventListener("click", () => {
      tabs.forEach(({ btn: b, panel: p }) => {
        const isActive = b === btn;
        document.getElementById(b).classList.toggle("tab-active", isActive);
        document.getElementById(b).setAttribute("aria-selected", String(isActive));
        document.getElementById(p).hidden = !isActive;
      });
      // Refresh JSON content when switching to that tab
      if (btn === "tab-json") renderJsonPanel(lastFields);
    });
  });
}

// ---------------------------------------------------------------------------
// Chat form
// ---------------------------------------------------------------------------

function setupChatForm() {
  const form  = document.getElementById("chat-form");
  const input = document.getElementById("chat-input");

  form.addEventListener("submit", (e) => {
    e.preventDefault();
    const text = input.value.trim();
    if (!text) return;
    input.value = "";
    sendMessage(text);
  });

  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      form.dispatchEvent(new Event("submit"));
    }
  });
}

// ---------------------------------------------------------------------------
// New session button
// ---------------------------------------------------------------------------

function setupNewSessionButton() {
  document.getElementById("btn-new-session").addEventListener("click", () => {
    if (confirm("Start a new session? Your current answers will be lost.")) {
      editingField = null;
      clearWarnings();
      startSession();
    }
  });
}
