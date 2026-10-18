#!/usr/bin/env bash
# Planet Control on one Ubuntu 24.04 server (Vultr Cloud Compute): dashboard + API + exactly one worker, https by Caddy.
#
#   bash setup.sh <domain>      e.g. 45-76-12-34.sslip.io (free, points at the server's IP) or your own domain
#
# First run: installs everything and clones the repo, then stops and asks for the secret files (.env, .env.api,
# frontend/.env). Copy them over, run it again, and it builds and starts the app. Re-run any time to update to the
# latest main. Never set PLANIT_DEV here: it's the public site. Private repo: REPO=https://<token>@github.com/... bash setup.sh <domain>
set -euo pipefail
DOMAIN=${1:?usage: bash setup.sh <domain>}
APP=/srv/planet/app
REPO=${REPO:-https://github.com/LuisGandora/SwampHacksProject2026.git}
export UV_PYTHON_INSTALL_DIR=/opt/uv/python   # Python where the app's user can read it (not under /root)

if ! command -v caddy >/dev/null; then                      # ---- first run: system packages ----
  apt-get update
  apt-get install -y git curl gnupg ufw debian-keyring debian-archive-keyring apt-transport-https
  curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/gpg.key | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt > /etc/apt/sources.list.d/caddy-stable.list
  curl -fsSL https://deb.nodesource.com/setup_22.x | bash -   # Node 22, to build the dashboard
  apt-get update && apt-get install -y caddy nodejs
  curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin sh   # uv: Python 3.13 + packages
  if [ ! -f /swapfile ]; then                                  # the dashboard build and the model want > 1 GB of RAM
    fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
    echo '/swapfile none swap sw 0 0' >> /etc/fstab
  fi
  ufw allow OpenSSH && ufw allow 80 && ufw allow 443 && ufw --force enable
  id planet >/dev/null 2>&1 || useradd --system --home /srv/planet --shell /usr/sbin/nologin planet
fi

git config --global --add safe.directory "$APP"              # the repo belongs to the app's user; root updates it
if [ -d "$APP/.git" ]; then                                   # ---- code: latest main (history may be rewritten) ----
  git -C "$APP" fetch --depth 1 origin main && git -C "$APP" reset --hard origin/main   # keeps the git-ignored .env files
else
  mkdir -p /srv/planet && git clone --depth 1 "$REPO" "$APP"
fi

for f in .env .env.api frontend/.env; do
  if [ ! -f "$APP/$f" ]; then
    echo; echo "Missing $APP/$f. From your laptop (PowerShell, in the project folder):"
    echo "  scp .env .env.api root@<server-ip>:$APP/"
    echo "  scp frontend/.env root@<server-ip>:$APP/frontend/"
    echo "then run this script again."; exit 1
  fi
done

cd "$APP"                                                     # ---- build ----
[ -d .venv ] || uv venv --python 3.13 .venv
uv pip install --python .venv -q -r backend/requirements.txt
(cd frontend && npm ci --no-audit --no-fund && VITE_API_URL="https://$DOMAIN/api" npm run build)
mkdir -p /srv/planet/uploads
chown -R planet:planet /srv/planet
chmod 600 .env .env.api frontend/.env

cat > /etc/systemd/system/planet-api.service <<EOF
[Unit]
Description=Planet Control API
After=network-online.target
[Service]
User=planet
WorkingDirectory=$APP
# the least-privilege database user (.env.api); the rest of the settings come from .env
EnvironmentFile=$APP/.env.api
Environment=UPLOADS_DIR=/srv/planet/uploads CORS_ORIGINS=https://$DOMAIN
ExecStart=$APP/.venv/bin/uvicorn backend.app:app --host 127.0.0.1 --port 8000 --proxy-headers --forwarded-allow-ips 127.0.0.1 --root-path /api
Restart=always
[Install]
WantedBy=multi-user.target
EOF

cat > /etc/systemd/system/planet-worker.service <<EOF
[Unit]
Description=Planet Control worker (exactly one: two would double-send alerts and briefings)
After=network-online.target
[Service]
User=planet
WorkingDirectory=$APP
ExecStart=$APP/.venv/bin/python -m backend.worker
Restart=always
RestartSec=10
[Install]
WantedBy=multi-user.target
EOF

cat > /etc/caddy/Caddyfile <<EOF
$DOMAIN {
    encode gzip
    handle_path /api/* {
        reverse_proxy 127.0.0.1:8000
    }
    handle {
        root * $APP/frontend/dist
        try_files {path} /index.html
        file_server
    }
}
EOF

systemctl daemon-reload
systemctl enable --now planet-api planet-worker caddy
systemctl restart planet-api planet-worker
systemctl reload caddy
sleep 8
echo; curl -fsS "https://$DOMAIN/api/health" && echo && echo "Planet Control is up at https://$DOMAIN" \
  || echo "Not answering yet: check 'journalctl -u planet-api -n 50' and 'journalctl -u caddy -n 50'"
