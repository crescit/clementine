# Deployment

## Option 1: Render free web service

1. Push this directory to a GitHub repo, including `uv.lock`, `Dockerfile`, `render.yaml`, `clearpath/`, and `static/`.
2. In [Render](https://dashboard.render.com/), choose **New → Blueprint**, connect the repo, and create the service. The included Blueprint chooses `plan: free`.
3. Wait for the build and health check to pass, then open the service's `https://…onrender.com` URL.
4. Run the post-deployment checks below. Put the public URL and your GitHub repo URL in the assignment submission.

If creating a service manually, choose **Web Service**, **Docker**, and **Free**. Leave Docker command empty (use the Dockerfile). Set `DEMO_MODE=true`, `DATABASE_PATH=/data/clearpath.db`, and health check path `/api/health`. The start command honors Render's injected `PORT` and binds `0.0.0.0`.

**Storage:** Render free services cannot attach a persistent disk. They sleep after 15 idle minutes, may take roughly a minute to wake, and lose local SQLite changes on sleep/restart/redeploy. A fresh start restores seven synthetic campaigns. Open the URL before presenting. This mode is suitable for evaluating a disposable demo, not preserving review records. Verified against [Render's free-service documentation](https://render.com/docs/free) on 2026-09-25. [Blueprint field reference](https://render.com/docs/blueprint-spec).

**Semantic review (optional).** The Blueprint ships with `CLEARPATH_SEMANTIC_MODE=false` and the inference vars declared with `sync: false`, so semantic analysis is off by default and no values are committed. To enable it on the hosted demo, open the service in the [Render dashboard](https://dashboard.render.com/) → **Environment**, and set:
`CLEARPATH_SEMANTIC_MODE=true`,
`CLEARPATH_INFERENCE_BASE_URL=<endpoint>`,
`CLEARPATH_INFERENCE_MODEL=<model>`,
`CLEARPATH_INFERENCE_API_KEY=<key>` (store as a secret, never in the blueprint), and
`CLEARPATH_INFERENCE_TIMEOUT_S=150`.
The base URL must be a **publicly reachable OpenAI-compatible chat endpoint** — a local `localhost` host is unreachable from Render's servers. See [Semantic review](EVALUATION.md).

## Option 2: ngrok with persistent local data

This is explicitly acceptable in the assignment and preserves the database on your machine.

```bash
docker compose up --build -d
ngrok http 8000
```

Authenticate ngrok with your own account using its setup instructions if needed. Share the HTTPS forwarding URL printed by ngrok. Keep Docker, ngrok, and your computer running for the evaluation. Never commit the ngrok token. The local service uses the named `clearpath-data` volume and preserves changes across container restarts.

Without Docker, run `uv sync --frozen`, then `uv run uvicorn clearpath.api:app --host 0.0.0.0 --port 8000`, and start ngrok in a second terminal.

## Persistent hosting later

Use the same Docker image with a writable persistent volume mounted at `/data` and one Uvicorn worker. On Render this needs a paid instance/disk. SQLite is appropriate for this small demo; a production rollout needs real identity, a managed database/backup plan, policy ownership, and access controls before real marketing data is entered.

`DEMO_MODE=false` only disables seeding/reset; it does not add authentication. Do not mistake it for a production security mode.

## Post-deployment check (about five minutes)

- Open `/api/health` and confirm `{"status":"ok"}`.
- Open the root and each of `/static/index.html`, `/static/submit.html`, and a submission detail link directly.
- Follow the README's request-changes → revision → approval walkthrough across Sarah and Jessica.
- Confirm the approved campaign appears under Completed, its old version still exists, and metrics update.
- Create an affiliate submission; confirm required partner, automatic assignment, and policy findings.
- Reload and check saved work. On persistent hosting, restart and verify it again. On Render free, a lost filesystem intentionally returns to the seeded baseline.
- Verify the public URL in a private window and check GitHub accessibility before sending both links.

No public deployment or GitHub publication has been performed by the assistant. Those final link checks remain with the owner.
