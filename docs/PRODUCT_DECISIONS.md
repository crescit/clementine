# Product decisions

ClearPath Financial is fictional. Compliance rules are demo policies, not legal advice.

## In scope for this take-home

- Structured intake for text ad copy, automatic assignment, review workspace, immutable versions, SLA-ranked queue, explainable demo-policy preflight, persona switcher, demo reset.

## Explicitly out of scope

Production login/SSO, real email, file uploads, live site crawling, AI/LLM review, Postgres, React/TypeScript/Tailwind, workers/queues, configurable policy editors.

## Operating assumptions (hypotheses, not research)

- Incomplete intake, unclear ownership, scattered feedback, and repeated revision review contribute to the Excel/email bottleneck.
- A 72-hour elapsed-time review target and least-loaded reviewer assignment are reasonable demo policies.
- Text advertisements are the reviewed artifact; optional URLs are context only.

## Shared demo state

The public demo uses shared SQLite state and intentional persona impersonation via `X-Demo-User-Id`. This is not authentication. Reset clears all visitor changes.
