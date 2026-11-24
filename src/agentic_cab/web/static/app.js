/**
 * Agentic Cab Rides — vanilla JS frontend (~150 lines).
 *
 * Security: all JSON-derived text is rendered via .textContent (not .innerHTML)
 * to prevent XSS injection from server-controlled content.
 */

"use strict";

// ---- Session ID ----
// Minted by the server (POST /sessions) so it is always a canonical ULID.
let sessionId = null;

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
const connStatusEl   = document.getElementById("conn-status");

let pendingFingerprint = "";

// ---- Connection-status pill (single source of truth, never writes to chat) ----
//
// EventSource auto-reconnects on transient drops. We surface the *current*
// state via a pill in the header rather than appending stale messages into the
// chat thread on every flap.
//
//   onopen  → state "connected", briefly visible, then auto-hide
//   onerror + readyState=CONNECTING (0) → state "reconnecting" (debounced 500ms
//     to avoid flicker when reconnect is near-instant)
//   onerror + readyState=CLOSED (2)     → state "disconnected" (sticky, asks
//     for a refresh)
let connState = "init";
let reconnectingTimer = null;
let connectedTimer = null;

function setConnStatus(state) {
  if (connState === state) return;
  connState = state;
  clearTimeout(connectedTimer);
  if (state === "connected") {
    connStatusEl.textContent = "connected";
    connStatusEl.className = "conn-status conn-status--connected";
    connectedTimer = setTimeout(() => {
      connStatusEl.className = "conn-status conn-status--hidden";
    }, 1500);
  } else if (state === "reconnecting") {
    connStatusEl.textContent = "reconnecting…";
    connStatusEl.className = "conn-status conn-status--reconnecting";
  } else if (state === "disconnected") {
    connStatusEl.textContent = "disconnected — refresh the page";
    connStatusEl.className = "conn-status conn-status--disconnected";
  }
}

// ---- State ----
let pendingSubjectId = null;

// ---- Session display ----
sessionInfo.textContent = "session: starting…";

function setFormEnabled(enabled) {
  for (const el of chatForm.elements) el.disabled = !enabled;
}

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
  if (!subjectId || !sessionId) return;

  // Session-scoped route; the HttpOnly session cookie authenticates the call and
  // the server derives who approved — the body carries the decision only.
  const url =
    "/sessions/" + encodeURIComponent(sessionId) +
    "/confirmations/" + encodeURIComponent(subjectId);
  try {
    const resp = await fetch(url, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ approved }),
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

  es.onopen = () => {
    clearTimeout(reconnectingTimer);
    reconnectingTimer = null;
    setConnStatus("connected");
  };

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
    } else if (row.kind === "confirmation_resolved") {
      // Decided elsewhere (another tab, or timed out): drop the stale modal.
      if (row.subject_id === pendingSubjectId) hideModal();
      addLogEntry(row);
    } else if (row.kind === "agent_reply") {
      addChatMessage(row.text || "", "agent");
      addLogEntry(row);
    } else if (row.kind === "error") {
      // Generic message plus an opaque ref the user can quote to an operator.
      const ref = typeof row.correlation_id === "string" ? " (ref " + row.correlation_id + ")" : "";
      addChatMessage("[error] " + (row.message || "unknown error") + ref, "error");
      addLogEntry(row);
    } else {
      addLogEntry(row);
    }
  };

  es.onerror = () => {
    // EventSource auto-reconnects when readyState is CONNECTING (0). Only
    // when it gives up (CLOSED == 2) do we ask the user to refresh.
    if (es.readyState === EventSource.CLOSED) {
      clearTimeout(reconnectingTimer);
      setConnStatus("disconnected");
      return;
    }
    // Debounce the "reconnecting" pill so a near-instant reconnect doesn't
    // flicker the header.
    if (!reconnectingTimer) {
      reconnectingTimer = setTimeout(() => {
        if (es.readyState !== EventSource.OPEN) setConnStatus("reconnecting");
      }, 500);
    }
  };
}

// ---- Session bootstrap: server mints the ULID, then stream + form come alive ----

async function startSession() {
  setFormEnabled(false);
  try {
    // The response sets the session's HttpOnly token cookie; same-origin fetch
    // and EventSource then send it automatically on every session route.
    const resp = await fetch("/sessions", { method: "POST", credentials: "same-origin" });
    if (!resp.ok) throw new Error("HTTP " + resp.status);
    const data = await resp.json();
    sessionId = data.session_id;
  } catch (err) {
    addChatMessage("[system] could not start a session: " + String(err), "error");
    return;
  }
  sessionInfo.textContent = "session: " + sessionId;
  connectEventStream();
  setFormEnabled(true);
}

startSession();

// ---- Chat form submission ----

chatForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  if (!sessionId) return;
  const text = messageInput.value.trim();
  if (!text) return;

  messageInput.value = "";
  addChatMessage(text, "user");

  try {
    const resp = await fetch("/chat", {
      method: "POST",
      credentials: "same-origin",
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
