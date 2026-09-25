/**
 * ClearPath frontend shell.
 * Hash routes, personas, queue, and review workspace land in K7–K11.
 */

(function () {
  "use strict";

  const statusEl = document.getElementById("boot-status");

  async function checkHealth() {
    if (!statusEl) return;
    try {
      const res = await fetch("/api/health");
      if (!res.ok) {
        statusEl.textContent = `API health returned ${res.status}`;
        return;
      }
      const data = await res.json();
      statusEl.textContent =
        data.status === "ok"
          ? "API healthy · skeleton ready"
          : `API status: ${data.status}`;
    } catch (err) {
      statusEl.textContent = "API unreachable";
    }
  }

  checkHealth();
})();
