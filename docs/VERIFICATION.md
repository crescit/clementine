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
| Preferred host | Persistent container chosen | Named Docker volume persists SQLite across restarts; ngrok is the allowed fallback if a reachable container host is not available |
| GitHub credentials / repo | Local Git only; remote not yet created | No `gh` CLI installed; push to a public (or explicitly granted) repo at K15 |
| Container check | `docker build` + run passes | Root and `/static/{index,submission,submit}.html` plus JS/CSS all return 200 in the container |
| Blockers | GitHub remote only | Hosting route settled; remote publication still pending at K15 |

## Known limitations

- Skeleton only until later cards land domain behavior.
- Shared demo state; persona switcher is not auth.
