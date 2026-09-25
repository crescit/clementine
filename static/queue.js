/**
 * Queue page behavior for /static/index.html.
 *
 * K0 skeleton: health check and persona header shell. Real queue rows, metrics,
 * filters, and completed view arrive in K7. All buttons must reach real endpoints
 * once those land — no mocked success or hardcoded counts.
 */

import { api, renderPersonaHeader, readParam, setText } from "./common.js";

const bootStatus = document.getElementById("boot-status");

async function checkHealth() {
  try {
    const res = await fetch("/api/health");
    if (!res.ok) {
      setText(bootStatus, `API health returned ${res.status}`);
      return;
    }
    const data = await res.json();
    setText(
      bootStatus,
      data.status === "ok" ? "API healthy · queue skeleton ready" : `API status: ${data.status}`
    );
  } catch (err) {
    setText(bootStatus, "API unreachable");
  }
}

renderPersonaHeader(document.getElementById("header-actions"));
// Queue filters are query parameters; persist the last queue query for Back to queue.
if (readParam("view") || readParam("filter") || readParam("status") || readParam("search")) {
  sessionStorage.setItem("clearpath_queue_query", window.location.search);
}
checkHealth();
