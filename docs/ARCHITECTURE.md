# Architecture

ClearPath is a single FastAPI process serving the JSON API and same-origin static assets. SQLite stores all state. One Uvicorn worker; no remote services required for local evaluation.

## Layout

| Path | Role |
|---|---|
| `clearpath/api.py` | HTTP routes, identity header, error mapping, static root |
| `clearpath/models.py` | Enums and Pydantic request/response models |
| `clearpath/db.py` | Connections, pragmas, schema, transactions |
| `clearpath/workflow.py` | Permissions, transitions, assignment, mutations |
| `clearpath/preflight.py` | Frozen demo policy scanner |
| `clearpath/metrics.py` | Queue urgency and operational metrics |
| `clearpath/seed.py` | Deterministic relative-time fixtures and reset |
| `static/` | Vanilla HTML/CSS/JS (no bundler) |

## Runtime configuration

| Variable | Default | Notes |
|---|---|---|
| `DATABASE_PATH` | `./data/clearpath.db` | Must be writable; mounted volume in Docker |
| `DEMO_MODE` | `true` | Controls seed-on-fresh-DB and `/api/demo/reset` |
| `PORT` | `8000` | Honored at process start |

Export variables in the shell; a `.env` file is not loaded automatically.

## Status

Skeleton (K0). Domain schema, workflow, API surface, and UI arrive in K1–K11.
