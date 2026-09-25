# Verification

Record commands, results, date, tested commit, tested URL, and known limitations here as cards complete. Do not mark manual/browser/deployed checks passed unless actually performed.

## K0 — Bootstrap

| Check | Command / step | Result | Date |
|---|---|---|---|
| Dependencies | `uv sync` (generates `uv.lock`) | pass — 28 packages | 2026-09-25 |
| Unit/API smoke | `uv run pytest` | pass — 9 tests | 2026-09-25 |
| Local root | `GET /` → 200 HTML with ClearPath | pass | 2026-09-25 |
| Local health | `GET /api/health` → `{"status":"ok"}` | pass | 2026-09-25 |
| Docker boot | `docker compose up --build -d` | pass — image builds, container starts | 2026-09-25 |
| Container bind | listens on `0.0.0.0` | pass — logs `Uvicorn running on http://0.0.0.0:8000`; host `0.0.0.0:8000->8000/tcp` | 2026-09-25 |
| Container health | `GET /api/health` via published port | pass — `{"status":"ok"}` | 2026-09-25 |
| Container root/static | `GET /`, `GET /static/styles.css` | pass — 200 | 2026-09-25 |
| Persistent volume | named volume → `/data/clearpath.db` | pass — DB created, `user_version=1` | 2026-09-25 |

### Hosting / GitHub preflight (K0)

| Item | Status | Notes |
|---|---|---|
| Preferred host | _choose before hour 12_ | Persistent container volume preferred; local + ngrok fallback allowed by brief |
| GitHub credentials / repo | Git initialized locally; remote not yet created | Push by K15; confirm public or evaluator access |
| Blockers | Hosting account + GitHub remote still open | Do not defer discovery to K15 |

## Known limitations

- Skeleton only until later cards land domain behavior.
- Shared demo state; persona switcher is not auth.
