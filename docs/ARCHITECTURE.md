# Architecture

One FastAPI process serves three HTML pages and the same-origin JSON API. SQLite holds users, submission metadata, immutable copy versions, and append-only audit events. Native JavaScript modules call the API; there is no frontend build or client-side router.

## Request and mutation flow

The browser resolves a demo persona from `/api/users`, remembers its ID, and includes `X-Demo-User-Id` on business requests. The server resolves that ID independently. Reviewers can read all work; submitters only their own. Only the assigned reviewer can request changes, approve, or reject.

Pydantic validates request shapes and rejects extra fields. The workflow service acquires `BEGIN IMMEDIATE` before reading state, checking permissions and expected record version, choosing a reviewer, or writing. Every mutation commits metadata, content versions, and audit events together; a failure rolls back the whole transaction. Lock timeouts produce retryable errors. Conflicting writes never silently overwrite a newer decision.

Intake automatically selects the reviewer with the fewest `UNDER_REVIEW` records. Revision preserves the original deadline and owner, appends a content version, and returns directly to review. Approval recomputes the frozen policy scan inside the transaction. Terminal records cannot be reassigned or edited.

Reads apply ownership restrictions to list, detail, history, and metric endpoints. Historical versions include their own preflight results. Queue filters do not change the metric scope. Metrics use a single UTC timestamp per calculation: turnaround includes decisions in the last 30 days; completion volume covers the last 7 days.

## Boundaries

| Module | Responsibility |
|---|---|
| `api.py` | HTTP, persona lookup, visibility, errors, reads, static assets |
| `models.py` | Domain vocabulary and normalized input validation |
| `db.py` | Versioned schema, connection pragmas, WAL |
| `workflow.py` | Permission/transition rules and atomic write services |
| `preflight.py` | Frozen fictional policy rules and Unicode-aware findings |
| `metrics.py` | Deadline classification and operational aggregates |
| `seed.py` | Relative-time synthetic fixtures; transactional reset |
| `static/` | Queue, intake, and review pages; safe DOM text rendering |

## Configuration and storage

`DATABASE_PATH` defaults to `./data/clearpath.db`; `DEMO_MODE` defaults to `true`. Docker honors `PORT` (default 8000) and runs one worker as a non-root user. The health check honors that same port. Compose mounts a named volume at `/data`; Render free uses temporary local storage. See [Deployment](DEPLOYMENT.md).

Only a fresh database is seeded. Existing work survives application restarts when its filesystem persists. An incompatible/corrupt database is not silently deleted. Reset is explicit, reviewer-only, and allowed only in demo mode; new UUIDs prevent old tabs from changing newly seeded records.

The persona header is not authentication. The audit trail is application history, not a certified or cryptographically tamper-proof archive. National/jurisdictional legal coverage is outside the synthetic policy model. These boundaries are deliberate and documented in [Product decisions](PRODUCT_DECISIONS.md).
