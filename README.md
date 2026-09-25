# ClearPath Compliance Review

Demo workspace · fictional data · shared state.

Internal marketing compliance review queue for fictional ClearPath Financial. Marketers submit text ad copy; reviewers assign, request changes, approve, or reject. Not legal advice — demo policies only.

**Live demo:** _pending deployment (K15)_  
**GitHub repo:** _pending publication (K15)_

## Quickstart (local)

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync --frozen
uv run pytest
uv run uvicorn clearpath.api:app --reload
```

Open http://localhost:8000. No credentials or external services required.

### Environment

Export variables in your shell (a `.env` file is **not** loaded automatically):

```bash
export DATABASE_PATH=./data/clearpath.db
export DEMO_MODE=true
export PORT=8000
```

See `.env.example` for documentation.

## Quickstart (Docker)

```bash
docker compose up --build -d
```

Maps port 8000, stores SQLite on a named volume at `/data`, enables demo mode. Restart preserves data; use the in-app reset (when available) to return to baseline.

## Personas (after seed — K1+)

| Name | Role | Notes |
|---|---|---|
| Sarah T. | Reviewer · Compliance Lead | Default persona |
| Mark Davis | Reviewer · Compliance Analyst | Same review permissions as Sarah |
| Jessica Lin | Submitter · Partnerships | Owns baseline submissions |

## Status

**K0 skeleton.** Health API, static shell, test harness, and Docker Compose boot. Domain schema, workflow, full API, and UI arrive in subsequent cards.

## Corrected demo copy (golden walkthrough)

Used when revising CP-8904 (available after seed/UI cards):

> You may be pre-qualified for ClearRewards. Explore rewards for everyday purchases. Subject to credit approval.

## Docs

- [Architecture](docs/ARCHITECTURE.md)
- [Product decisions](docs/PRODUCT_DECISIONS.md)
- [Verification](docs/VERIFICATION.md)
- [Implementation plan](clearpath_takehome_build_plan.md)
