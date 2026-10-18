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

## DigitalOcean App Platform (alternative to Render)
[.do/app.yaml](../.do/app.yaml) runs the public stack as one app at one https URL. The dashboard is at `/`, the API at `/api` and there's exactly one worker. Create it under **Apps → Create App → Import from app spec** (or run `doctl apps create --spec .do/app.yaml`), then type the secrets into each component's environment variables.
- **Auth0:** add `https://<your-app>.ondigitalocean.app` to the Allowed Callback URLs, Logout URLs, Web Origins and CORS origins.
- **Photos:** App Platform has no persistent disk, so uploaded crew photos are lost on each redeploy. That's fine for judging; move uploads to DigitalOcean Spaces for real use.
- **Demo instance:** run the demo from a laptop with `PLANIT_DEV=1`. Never set `PLANIT_DEV` on the public app.
- **Cost:** two 1 GB instances (API + worker) at about $12 a month each; the static site is free.

## Vultr (one server, MLH credits) — the current plan
[deploy/vultr/setup.sh](../deploy/vultr/setup.sh) turns a fresh **Ubuntu 24.04** Vultr Cloud Compute server (2 GB RAM recommended) into the whole app:
- **Caddy:** https (automatic certificate), serves the dashboard at `/` and the API at `/api`
- **Two services that restart on crash or reboot:** the API (`planet-api`) and exactly one worker (`planet-worker`)
- **Photos** are kept on the server's own disk, so they survive restarts
- **Running it:** `bash setup.sh <domain>`. A free `<ip-with-dashes>.sslip.io` name works with no DNS setup.
- **First run:** it stops and asks for `.env`, `.env.api` and `frontend/.env` (copy them with `scp`). Run it again to build and start.
- **Updating:** run it again any time to pull the latest `main`.
- **Logs:** `journalctl -u planet-worker -f`, `journalctl -u planet-api -f`
- **Auth0:** add `https://<domain>` to the Allowed Callback URLs, Logout URLs and Web Origins.
- **After judging:** destroy the server in Vultr. Stopping it isn't enough; it keeps billing.
