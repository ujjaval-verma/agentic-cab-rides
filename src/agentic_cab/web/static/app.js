/**
 * Agentic Cab Rides — vanilla JS frontend (~150 lines).
 *
 * Security: all JSON-derived text is rendered via .textContent (not .innerHTML)
 * to prevent XSS injection from server-controlled content.
 */

"use strict";

// ---- Session ID ----
const sessionId = crypto.randomUUID();

// ---- DOM refs ----
const chatMessages   = document.getElementById("chat-messages");
const logEntries     = document.getElementById("log-entries");
const chatForm       = document.getElementById("chat-form");
const messageInput   = document.getElementById("message-input");
const sessionInfo    = document.getElementById("session-info");
const modalOverlay   = document.getElementById("modal-overlay");
const modalSummary   = document.getElementById("modal-summary");
const modalFpEl      = document.getElementById("modal-fingerprint");
const modalFpShortEl = document.getElementById("modal-fingerprint-short");
const btnCopyFp      = document.getElementById("btn-copy-fingerprint");
const btnApprove     = document.getElementById("btn-approve");
const btnDecline     = document.getElementById("btn-decline");

let pendingFingerprint = "";

// ---- State ----
let pendingSubjectId = null;

// ---- Session display ----
sessionInfo.textContent = "session: " + sessionId;

// ---- Helpers ----

function addChatMessage(text, role) {
  const div = document.createElement("div");
  div.className = "msg " + role;
  div.textContent = text;            // textContent — no XSS risk
  chatMessages.appendChild(div);
  chatMessages.scrollTop = chatMessages.scrollHeight;
}

function addLogEntry(row) {
  const div = document.createElement("div");
  const kind = typeof row.kind === "string" ? row.kind : "unknown";
  div.className = "log-entry " + kind;
  // Render as JSON text so the content is always escaped.
  div.textContent = JSON.stringify(row, null, 2);
  logEntries.appendChild(div);
  logEntries.scrollTop = logEntries.scrollHeight;
}

// ---- Confirmation modal ----

function showModal(event) {
  pendingSubjectId = event.subject_id;
  // Use textContent for all user-visible fields to prevent XSS.
  modalSummary.textContent = event.summary || "(no summary)";
  pendingFingerprint = typeof event.fingerprint === "string" ? event.fingerprint : "";
  modalFpShortEl.textContent = pendingFingerprint
    ? pendingFingerprint.slice(0, 12) + "…"
    : "—";
  btnCopyFp.textContent = "copy";
  modalOverlay.classList.add("visible");
  btnApprove.focus();
}

btnCopyFp.addEventListener("click", async () => {
  if (!pendingFingerprint) return;
  try {
    await navigator.clipboard.writeText(pendingFingerprint);
    btnCopyFp.textContent = "copied";
    setTimeout(() => { btnCopyFp.textContent = "copy"; }, 1200);
  } catch {
    btnCopyFp.textContent = "copy failed";
  }
});

function hideModal() {
  modalOverlay.classList.remove("visible");
  pendingSubjectId = null;
  pendingFingerprint = "";
}

async function resolveConfirmation(approved) {
  const subjectId = pendingSubjectId;
  hideModal();
  if (!subjectId) return;

  try {
    const resp = await fetch("/confirmations/" + encodeURIComponent(subjectId), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ approved, approver: "web-user" }),
    });
    if (!resp.ok) {
      const text = await resp.text();
      addChatMessage("[system] confirmation failed: " + text, "error");
    }
  } catch (err) {
    addChatMessage("[system] network error: " + String(err), "error");
  }
}

btnApprove.addEventListener("click", () => resolveConfirmation(true));
btnDecline.addEventListener("click", () => resolveConfirmation(false));

// Close modal on Escape key.
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && pendingSubjectId) {
    resolveConfirmation(false);
  }
});

// ---- SSE event stream ----

function connectEventStream() {
  const es = new EventSource("/events?session_id=" + encodeURIComponent(sessionId));

  es.onmessage = (ev) => {
    let row;
    try {
      row = JSON.parse(ev.data);
    } catch {
      return;
    }

    if (row.kind === "pending_confirmation") {
      addLogEntry(row);
      showModal(row);
    } else if (row.kind === "agent_reply") {
      addChatMessage(row.text || "", "agent");
      addLogEntry(row);
    } else if (row.kind === "error") {
      addChatMessage("[error] " + (row.message || "unknown error"), "error");
      addLogEntry(row);
    } else {
      addLogEntry(row);
    }
  };

  es.onerror = () => {
    // Reconnect automatically — EventSource does this by default.
    addChatMessage("[system] connection lost, reconnecting…", "error");
  };
}

connectEventStream();

// ---- Chat form submission ----

chatForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const text = messageInput.value.trim();
  if (!text) return;

  messageInput.value = "";
  addChatMessage(text, "user");

  try {
    const resp = await fetch("/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: sessionId, message: text }),
    });
    if (!resp.ok) {
      const errText = await resp.text();
      addChatMessage("[system] error: " + errText, "error");
    }
    // Agent reply arrives via SSE — no need to read the body.
  } catch (err) {
    addChatMessage("[system] network error: " + String(err), "error");
  }
});
