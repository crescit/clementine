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

## K0–K2 QA regression (after K3–K4 landed)

Checked on committed HEAD `bd0cbff` (K3–K4 checkpoint). The K3–K4 commit touched
only `preflight.py`, `workflow.py`, `test_preflight.py`; the K0–K2 committed
files (`api.py`, `db.py`, `seed.py`, `models.py`, `test_api.py`, `test_db.py`)
are byte-for-byte unchanged (`git diff 447fd85..bd0cbff` empty for those paths),
so this is a genuine regression on committed K0–K2 code.

| Check | Command / step | Result | Date |
|---|---|---|---|
| Full suite | `.venv/bin/pytest` | pass — 109 collected (api 4, db 12, metrics 1, preflight 13, workflow 79) | 2026-09-25 |
| Scaffold pages + health (container-free) | TestClient in-process: `GET /api/health` → 200 `{"status":"ok"}`; `GET /` → 200 HTML "Review Queue"; `GET /static/{index,submission,submit}.html` and `styles.css`, `common.js`, `queue.js`, `submission.js`, `submit.js` all 200 | pass | 2026-09-25 |
| SQLite seed invariants | Fresh temp DB: `initialize_schema` + `seed_database` → 4 tables present, `user_version=1`, users=3, submissions=7, versions=8, events=16 | pass | 2026-09-25 |
| Domain/validation rules | `tests/test_workflow.py` (79) + `tests/test_preflight.py` (13) — transition matrix, permission, visibility, content conditions, §9 request validation | pass | 2026-09-25 |

Deployment/Docker intentionally not touched per task scope.

## Final implementation verification — 2026-09-25 (Pacific)

The earlier sections above are historical checkpoints. These checks cover the completed working tree, including the previously uncommitted API work. This is not a claim that a public deployment has been tested.

| Check | Evidence | Result |
|---|---|---|
| Unit/domain/API/integration | `.venv/bin/pytest -q` | 135 tests passed |
| Full browser lifecycle | `uv run --with playwright python scripts/browser_check.py http://127.0.0.1:8019` against the final Docker image | Passed: request changes, Jessica revision, historical version, Sarah approval, Completed view, metrics |
| Browser permissions and intake | Same script | Reviewer intake disabled; Mark cannot decide Sarah's work; affiliate partner required; valid intake assigned and scanned |
| Browser filters and reset | Same script | Search/empty state/clear; reset cancellation and confirmed reset; persona recovery |
| Desktop/mobile visual review | Chrome at 1440×1100 and 390×844; six screenshots under `docs/screenshots/` | Inspected; mobile document overflow fixed; table scrolls within its panel |
| Failure recovery / safe rendering | Final Docker browser run | Failed request retains text and retries; stale second tab preserves feedback and offers reload; HTML-like comments render as text |
| JavaScript runtime | Browser `pageerror` collection | No uncaught JavaScript errors in acceptance flow |
| Atomicity and races | `tests/test_transactions.py` | Concurrent creates unique; concurrent decisions produce one success/one conflict; injected audit failure rolls back; lock timeout is retryable |
| Seed consistency | `tests/test_db.py`, `tests/test_metrics.py` | Seven campaigns; expected baseline metrics; approved and clean examples pass their applicable policies |
| Docker build | `docker build -t clearpath:review .` | Passed with frozen dependencies and pinned uv version |
| Container runtime | Disposable container on host port 8018, injected container `PORT=8123` | Health, root, HTML, JS/CSS, persona and queue API passed |
| Persistent restart | Restart disposable container with named volume; compare submission UUID | Same UUID before/after; database was preserved |
| Syntax/patch hygiene | Python compile and `git diff --check` | Passed |

Final Docker image manifest: `sha256:c332d46675e6928d2b4dece5d98980525226bb3513b769b968d36b808bd25e58`. Local preview container: `clearpath-final-check` at `http://localhost:8019`; it remains running for review. Stop it with `docker stop clearpath-final-check` when finished.

### Repeat the browser check

Use a dedicated database: the script resets shared demo state before and after its flow.

```bash
DATABASE_PATH=/tmp/clearpath-acceptance.db DEMO_MODE=true uv run uvicorn clearpath.api:app --port 8017
# In another terminal (requires installed Google Chrome):
uv run --with playwright python scripts/browser_check.py http://127.0.0.1:8017
```

### Remaining delivery steps and limitations

- Owner will publish GitHub and deploy; neither public link has been verified yet. Follow `docs/DEPLOYMENT.md`.
- Render free mode loses local data on sleep/restart/redeploy and reseeds the demo. Docker with a persistent volume retains it.
- Persona switching is demo impersonation. No production SSO, email delivery/import, attachments, or jurisdictional legal coverage.
- Automated browser evidence uses Chrome. Safari/Firefox and a screen-reader audit have not been performed.
- The installed Starlette test client emits one upstream httpx deprecation warning; all tests pass. It does not affect runtime serving.
- No measured throughput improvement is claimed; the pilot measures and assumptions are documented in `docs/PRODUCT_DECISIONS.md`.

## Synthetic scale benchmark — 2026-09-25 (Pacific)

The follow-up performance investigation added isolated corpus generation, real HTTP load profiles, SQL tracing, and Chrome network emulation. The complete suite now passes **137 tests** (135 application tests plus 2 benchmark-tool checks). Application hashes match the baseline throughout the investigation; no performance optimizations were applied.

See [Performance baseline and implementation handoff](PERFORMANCE.md) for 1k/10k/100k results, network measurements, limitations, and the prioritized work for the next implementation pass. All 100 tested review lifecycles preserved their expected versions/events; three contested-approval tests each produced exactly one committed approval. The large-queue and metrics latency findings are documented as shortcomings, not scalability successes.
