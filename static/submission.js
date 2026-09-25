/**
 * Submission detail page behavior for /static/submission.html?id=<uuid>.
 *
 * K0 skeleton: read the id from URLSearchParams and render a shell. Real detail
 * (copy, preflight, timeline, decisions, inline revision) arrives in K8-K9.
 */

import { api, readParam, renderPersonaHeader, setText } from "./common.js";

const idEl = document.getElementById("submission-id");
const statusEl = document.getElementById("detail-status");

const id = readParam("id");
if (id) {
  setText(idEl, id);
} else {
  setText(idEl, "(no id provided)");
}

renderPersonaHeader(document.getElementById("header-actions"));

if (id) {
  (async () => {
    try {
      const res = await api(`/api/submissions/${encodeURIComponent(id)}`);
      if (!res.ok) {
        setText(statusEl, `Detail returned ${res.status}. This record may have been reset.`);
        return;
      }
      const data = await res.json();
      setText(statusEl, `Detail loaded for ${data.submission?.external_id || id}`);
    } catch (err) {
      setText(statusEl, "Detail unreachable");
    }
  })();
} else {
  setText(statusEl, "Missing id — open a record from the queue.");
}
