/**
 * Document Intake Assistant — frontend application
 *
 * Architecture: thin client. All business logic lives on the server.
 * The browser only:
 *   1. Calls the REST API
 *   2. Renders the response (escaped — never raw HTML injection)
 *   3. Manages simple UI state (active tab, which field is being edited)
 *
 * Security: user content and model text are always set via textContent,
 * never innerHTML. The draft document is rendered inside a <pre> via
 * textContent. No eval(), no dangerouslySetInnerHTML equivalent.
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

// Fields that need a boolean edit control
const BOOL_FIELDS = new Set(["covers_worldwide_assets", "has_children"]);
// Fields that need a list edit control (comma-separated)
const LIST_FIELDS = new Set(["children_names", "specific_gifts"]);

// ---------------------------------------------------------------------------
// Application state (UI-only — source of truth is the server)
// ---------------------------------------------------------------------------

let sessionId = null;
let editingField = null;   // path string of the field currently being edited

// ---------------------------------------------------------------------------
// Boot
// ---------------------------------------------------------------------------

document.addEventListener("DOMContentLoaded", async () => {
  setupTabs();
  setupChatForm();
  setupNewSessionButton();

  await checkHealth();
  await startSession();
});

// ---------------------------------------------------------------------------
// Health check
// ---------------------------------------------------------------------------

async function checkHealth() {
  try {
    const res = await API.health();
    const data = await res.json();
    if (!data.llm_configured) {
      document.getElementById("llm-banner").hidden = false;
    }
  } catch {
    // Health check failure is non-fatal — app continues
  }
}

// ---------------------------------------------------------------------------
// Session management
// ---------------------------------------------------------------------------

async function startSession() {
  setLoading(true);
  try {
    const res = await API.sessions();
    const data = await res.json();
    if (data.error) { showInlineError(data.error.message); return; }
    sessionId = data.session_id;
    renderSession(data);
  } catch (e) {
    showInlineError("Could not connect to the server. Is it running?");
  } finally {
    setLoading(false);
  }
}

async function resetSession() {
  if (!sessionId) return;
  setLoading(true);
  try {
    const res = await API.reset(sessionId);
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
    const res = await API.message(sessionId, text);
    const data = await res.json();

    if (data.error) {
      showInlineError(data.error.message);
      return;
    }

    renderSession(data);
    if (data.reply) appendChatBubble("assistant", data.reply);

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
    const res = await API.patch(sessionId, [update]);
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
    if (lower === "true" || lower === "yes") return { field: fieldPath, value_bool: true };
    if (lower === "false" || lower === "no")  return { field: fieldPath, value_bool: false };
    if (trimmed === "") return { field: fieldPath, clear: true };
    return null;  // invalid bool input — ignore
  }

  if (LIST_FIELDS.has(fieldPath)) {
    if (trimmed === "") return { field: fieldPath, clear: true };
    const items = trimmed.split(",").map(s => s.trim()).filter(Boolean);
    return { field: fieldPath, value_list: items };
  }

  // String field
  if (trimmed === "") return { field: fieldPath, clear: true };
  return { field: fieldPath, value_text: trimmed };
}

// ---------------------------------------------------------------------------
// Render helpers — all user/model content goes through textContent
// ---------------------------------------------------------------------------

function renderSession(data) {
  renderMessages(data.messages || []);
  renderStateTable(data.fields || []);
  renderDraft(data.document_markdown || "");
  renderWarnings(data.warnings || []);
  renderConflict(data.pending_conflict);
}

function renderMessages(messages) {
  const container = document.getElementById("chat-messages");
  container.innerHTML = "";   // safe: we immediately repopulate with textContent
  for (const msg of messages) {
    appendChatBubble(msg.role, msg.text, container);
  }
  scrollChatToBottom();
}

function appendChatBubble(role, text, container) {
  container = container || document.getElementById("chat-messages");
  const div = document.createElement("div");
  div.className = `msg msg-${role === "user" ? "user" : "assistant"}`;
  // SECURITY: textContent — never innerHTML
  div.textContent = text;
  container.appendChild(div);
  scrollChatToBottom();
}

function scrollChatToBottom() {
  const c = document.getElementById("chat-messages");
  c.scrollTop = c.scrollHeight;
}

function renderDraft(markdown) {
  const el = document.getElementById("draft-content");
  // SECURITY: textContent — user/model text never injected as HTML
  el.textContent = markdown;
}

function renderWarnings(warnings) {
  const area = document.getElementById("warnings-area");
  if (!warnings || warnings.length === 0) {
    area.hidden = true;
    area.innerHTML = "";
    return;
  }
  area.hidden = false;
  area.innerHTML = "";  // safe: populated with textContent below
  const label = document.createElement("strong");
  label.textContent = "⚠ Notes from this turn:";
  area.appendChild(label);
  const ul = document.createElement("ul");
  for (const w of warnings) {
    const li = document.createElement("li");
    li.textContent = w;  // textContent — safe
    ul.appendChild(li);
  }
  area.appendChild(ul);
}

function renderConflict(conflict) {
  const area = document.getElementById("conflict-area");
  const span = document.getElementById("conflict-text");
  if (!conflict) {
    area.hidden = true;
    span.textContent = "";
    return;
  }
  area.hidden = false;
  span.textContent = " " + conflict.description;  // textContent — safe
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
  tbody.innerHTML = "";  // safe: repopulated with DOM methods

  for (const field of fields) {
    const tr = document.createElement("tr");

    // Path cell
    const tdPath = document.createElement("td");
    const pathSpan = document.createElement("span");
    pathSpan.className = "field-path";
    pathSpan.textContent = FIELD_LABELS[field.path] || field.path;
    tdPath.appendChild(pathSpan);

    // Value cell — edit control or display
    const tdVal = document.createElement("td");
    tdVal.appendChild(buildValueCell(field));

    // Status badge cell
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
    // Inline edit input
    const input = buildInlineInput(field);
    wrapper.appendChild(input);

    const btnSave = document.createElement("button");
    btnSave.className = "btn btn-ghost";
    btnSave.textContent = "Save";
    btnSave.addEventListener("click", () => {
      applyDirectEdit(field.path, input.value);
    });

    const btnCancel = document.createElement("button");
    btnCancel.className = "btn btn-ghost";
    btnCancel.style.color = "#64748b";
    btnCancel.textContent = "Cancel";
    btnCancel.addEventListener("click", () => {
      editingField = null;
      renderStateTable(
        // Re-render with current fields from last known session data
        // We re-fetch to avoid stale state
        document.getElementById("state-tbody")
          .__fields || []
      );
      // Simpler: just reload current session
      refreshSession();
    });

    wrapper.appendChild(btnSave);
    wrapper.appendChild(btnCancel);
    // Focus input on next tick
    setTimeout(() => input.focus(), 0);
  } else {
    // Display value + edit button
    const display = document.createElement("span");
    display.className = "field-value-display" + (field.status === "unknown" ? " field-value-unknown" : "");
    display.textContent = formatFieldValue(field);

    const btnEdit = document.createElement("button");
    btnEdit.className = "btn btn-ghost";
    btnEdit.textContent = "Edit";
    btnEdit.setAttribute("aria-label", `Edit ${FIELD_LABELS[field.path] || field.path}`);
    btnEdit.addEventListener("click", () => {
      editingField = field.path;
      refreshSession();
    });

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
      { value: "", label: "— select —" },
      { value: "true", label: "Yes" },
      { value: "false", label: "No" },
    ];
    for (const o of opts) {
      const opt = document.createElement("option");
      opt.value = o.value;
      opt.textContent = o.label;
      if (field.value !== null && field.value !== undefined) {
        if (o.value === String(field.value)) opt.selected = true;
      }
      sel.appendChild(opt);
    }
    return sel;
  }

  const input = document.createElement("input");
  input.type = "text";
  input.className = "inline-input";

  if (LIST_FIELDS.has(field.path)) {
    input.placeholder = "comma-separated values";
    input.value = Array.isArray(field.value) ? field.value.join(", ") : "";
  } else {
    input.value = field.value !== null && field.value !== undefined ? String(field.value) : "";
    input.placeholder = "Enter value";
  }

  // Submit on Enter
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      applyDirectEdit(field.path, input.value);
    }
    if (e.key === "Escape") {
      editingField = null;
      refreshSession();
    }
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
  span.className = `badge badge-${status}`;
  span.textContent = status.charAt(0).toUpperCase() + status.slice(1);
  return span;
}

// ---------------------------------------------------------------------------
// Refresh session from server (used after cancel on inline edit)
// ---------------------------------------------------------------------------

async function refreshSession() {
  if (!sessionId) return;
  try {
    const res = await API.session(sessionId);
    const data = await res.json();
    if (!data.error) renderSession(data);
  } catch { /* silent */ }
}

// ---------------------------------------------------------------------------
// Error display
// ---------------------------------------------------------------------------

function showInlineError(message) {
  // Append as a special chat bubble so it's visible in context
  const container = document.getElementById("chat-messages");
  const div = document.createElement("div");
  div.className = "msg msg-assistant";
  div.style.background = "#fee2e2";
  div.style.borderColor = "#fca5a5";
  div.style.color = "#991b1b";
  div.textContent = "⚠ " + message;  // textContent — safe
  container.appendChild(div);
  scrollChatToBottom();
}

// ---------------------------------------------------------------------------
// Loading state
// ---------------------------------------------------------------------------

function setLoading(on) {
  document.getElementById("loading").hidden = !on;
  document.getElementById("btn-send").disabled = on;
}

function setInputEnabled(on) {
  const inp = document.getElementById("chat-input");
  const btn = document.getElementById("btn-send");
  inp.disabled = !on;
  btn.disabled = !on;
}

function focusChatInput() {
  document.getElementById("chat-input").focus();
}

// ---------------------------------------------------------------------------
// Tab switching
// ---------------------------------------------------------------------------

function setupTabs() {
  const tabState = document.getElementById("tab-state");
  const tabDraft = document.getElementById("tab-draft");
  const panelState = document.getElementById("panel-state");
  const panelDraft = document.getElementById("panel-draft");

  tabState.addEventListener("click", () => {
    tabState.classList.add("tab-active");
    tabState.setAttribute("aria-selected", "true");
    tabDraft.classList.remove("tab-active");
    tabDraft.setAttribute("aria-selected", "false");
    panelState.hidden = false;
    panelDraft.hidden = true;
  });

  tabDraft.addEventListener("click", () => {
    tabDraft.classList.add("tab-active");
    tabDraft.setAttribute("aria-selected", "true");
    tabState.classList.remove("tab-active");
    tabState.setAttribute("aria-selected", "false");
    panelDraft.hidden = false;
    panelState.hidden = true;
  });
}

// ---------------------------------------------------------------------------
// Chat form
// ---------------------------------------------------------------------------

function setupChatForm() {
  const form = document.getElementById("chat-form");
  const input = document.getElementById("chat-input");

  form.addEventListener("submit", (e) => {
    e.preventDefault();
    const text = input.value.trim();
    if (!text) return;
    input.value = "";
    sendMessage(text);
  });

  // Allow Shift+Enter for newline, plain Enter to submit
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
