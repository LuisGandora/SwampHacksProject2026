# Deploying Planet Control for judging

Goal: a **stable public link** that keeps running, plus a **separate demo instance** for the live walkthrough. [render.yaml](../render.yaml) describes both stacks as a Render Blueprint.

| Service | What it runs | Notes |
|---|---|---|
| `planet-control` | the dashboard (static site) | the link judges get |
| `planet-control-api` | FastAPI, `PLANIT_DEV` **off** | 1 GB disk at `/var/data`, so crew photos survive restarts |
| `planet-control-worker` | `python -m backend.worker` | **exactly one** instance: two would double-send alerts and briefings |
| `planet-control-demo` + `planet-control-demo-api` | the same app with `PLANIT_DEV=1` | for the live demo: ▶ Demo, fake strike, Reset. Supervisors only. |

Both stacks share the one Tiger database, so a demo scenario also shows up on the public dashboard while it runs. Press **■ Reset** after the demo.

## Before you deploy
1. **Secrets.** Rotate any password that was ever committed. The old Tiger password (commit `a2ecf3b`) is already dead. See the hygiene checklist in [JUDGING.md](JUDGING.md).
2. **A least-privilege database user for the public API:** run `python -m db.create_api_role`, which writes `.env.api`. Use that `DATABASE_URL` for `planet-control-api`. The worker and the demo API use the admin URL from `.env`, because they write forecasts and scores and run jobs.
3. **Auth0.** Add the deployed frontend URLs (public and demo) to the Auth0 application's Allowed Callback URLs, Logout URLs, Web Origins and CORS origins.
4. **Supervisors.** Demo endpoints need `role = 'supervisor'`. After the presenter signs in once, run this in the Tiger SQL editor:
   ```sql
   UPDATE app_users SET role = 'supervisor' WHERE email = 'presenter@example.com';
   ```

## Deploy
1. Go to render.com → **New → Blueprint** and pick this repository and branch.
2. Fill in every value marked `sync: false`:
   - the database URLs
   - the Auth0 settings and `WEBHOOK_KEY` (the same key for the API and the worker)
   - `GEMINI_API_KEY`
   - `NWS_USER_AGENT` (a team contact)
   - `CORS_ORIGINS` (each API's own frontend URL)
   - `VITE_API_URL` (each frontend's own API URL)
3. Apply it. Then check `https://planet-control-api.onrender.com/health`: it should return `"dev": false`. The demo API should return `"dev": true`.

**Plans:** a persistent disk needs a paid Render instance. Starter costs about $7 a month per service, so this setup runs about $28 a month (the static sites are free). To save money, skip the demo stack and run the demo from a laptop with `PLANIT_DEV=1`.

## Limits to keep an eye on

| Limit | Now | What to do |
|---|---|---|
| Tiger free tier: 750 MiB | **467 MB (62%)** | Forecasts and observations are kept 30 days, NWS alerts 180, lightning 90 (compressed after 2). Check with `SELECT pg_size_pretty(pg_database_size(current_database()));`. |
| Photo disk: 1 GB | photos are shrunk to ≤1600 px on the phone (about 300 KB each) | about 3,000 photos. For more, move uploads to S3 or Cloudflare R2. |
| One worker | `numInstances: 1` | don't scale it. Scale the API instead. |
| Render free/starter cold starts | the API may sleep when idle on the free plan | starter plans don't sleep. Open the link once before judging. |

## Why the demo is separate
`PLANIT_DEV=1` turns on endpoints that write to the shared database: a fake lightning strike, the scenario, running worker jobs. On the public URL, anyone could trigger a stop-work alert. With the separate instance:
- the public link never has them
- the demo instance's endpoints need a signed-in supervisor once Auth0 is on (`backend/auth.py: require_supervisor`)
