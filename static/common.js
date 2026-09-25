/**
 * ClearPath shared browser helpers.
 *
 * Shared by the three HTML pages: fetch wrapper with the demo identity header,
 * persona header rendering, and safe text/DOM helpers. No framework, no bundler.
 * Real queue, detail, and intake behavior lands in later cards (K7-K11); these
 * helpers are the common seam every page uses.
 */

"use strict";

export const API_HEADER = "X-Demo-User-Id";

/** Same-origin fetch wrapper that always sends the demo persona header. */
export async function api(path, { method = "GET", body = null } = {}) {
  const userId = localStorage.getItem("clearpath_user_id") || "";
  const headers = { "X-Demo-User-Id": userId };
  let payload;
  if (body !== null) {
    headers["Content-Type"] = "application/json";
    payload = JSON.stringify(body);
  }
  const res = await fetch(path, { method, headers, body: payload });
  return res;
}

/** Render submitted copy/user text via textContent — never innerHTML. */
export function setText(el, value) {
  if (!el) return;
  el.textContent = String(value == null ? "" : value);
}

export function readParam(name) {
  return new URLSearchParams(window.location.search).get(name);
}

/**
 * Render the persona header actions. Real switcher behavior lands in K7; this
 * only renders the shell so each page has the same header seam.
 */
export function renderPersonaHeader(container) {
  if (!container) return;
  container.innerHTML = "";
  const status = document.createElement("span");
  status.className = "persona-slot";
  status.textContent = "Persona: loading…";
  container.appendChild(status);
}

/** Escape a string for a text node (safe default). */
export function text(value) {
  return String(value == null ? "" : value);
}
