#!/usr/bin/env bash
# Runs on the server; scripts/deploy.ps1 uploads it with the bundle and calls it.
#
#   bash ~/team128-upload/remote_setup.sh <site-address> [login|public]
#
# <site-address> is what Caddy serves: a domain or <ip>.sslip.io for automatic
# HTTPS, or http://<ip> for plain HTTP. "login" (the default) asks for the shared
# login in team128-upload/.auth; "public" serves the site to anyone.
set -euo pipefail
SITE="$1"
ACCESS="${2:-login}"
UP="$HOME/team128-upload"
APP="$HOME/team128"
COMPOSE=(sudo docker compose -f docker-compose.prod.yml)

echo "== 1/6 Docker"
if ! command -v docker >/dev/null 2>&1 || ! sudo docker compose version >/dev/null 2>&1 || ! sudo docker buildx version >/dev/null 2>&1; then
  sudo apt-get update -qq
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq docker.io docker-compose-v2 docker-buildx
  sudo systemctl enable --now docker
fi
sudo docker compose version

echo "== 2/6 Memory"
mem_mb=$(awk '/MemTotal/ {print int($2/1024)}' /proc/meminfo)
echo "RAM: ${mem_mb} MB"
# Policy Stance peaks near 2 GB while building its cache; all five services need ~4 GB.
if [ "$mem_mb" -lt 7500 ] && [ -z "$(swapon --show --noheadings)" ]; then
  echo "Adding a 4 GB swap file"
  sudo fallocate -l 4G /swapfile
  sudo chmod 600 /swapfile
  sudo mkswap /swapfile >/dev/null
  sudo swapon /swapfile
  grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null
fi

echo "== 3/6 Code"
mkdir -p "$APP"
tar -xzf "$UP/code.tar.gz" -C "$APP"

echo "== 4/6 Data"
if [ -f "$UP/data.tar.gz" ]; then
  tar -xzf "$UP/data.tar.gz" -C "$APP"
  rm -f "$UP/data.tar.gz"
fi
missing=0
for need in services/trade_intelligence/cache/meta.json services/policy_stance/data services/event_summarization/data/dyad_geopolitical_scores.csv; do
  if [ ! -e "$APP/$need" ]; then echo "MISSING: $need (see docs/DATA.md)"; missing=1; fi
done
[ "$missing" -eq 0 ] && echo "data present"

echo "== 5/6 Access and HTTPS"
if [ "$ACCESS" = "public" ]; then
  AUTH_BLOCK=""
  echo "serving $SITE to anyone, no login"
else
  # Older .auth files have no trailing newline, so read returns 1 at end of file
  # even though it filled both variables; check the values instead of the status.
  read -r AUTH_USER AUTH_HASH < "$UP/.auth" || true
  if [ -z "${AUTH_USER:-}" ] || [ -z "${AUTH_HASH:-}" ]; then
    echo "deploy/.auth is empty or malformed; run scripts/set_password.ps1 again"; exit 1
  fi
  AUTH_BLOCK=$(printf '\tbasic_auth {\n\t\t%s %s\n\t}' "$AUTH_USER" "$AUTH_HASH")
  echo "serving $SITE for user $AUTH_USER"
fi
cat > "$APP/deploy/Caddyfile" <<EOF
$SITE {
	encode gzip
$AUTH_BLOCK
	reverse_proxy orchestrator:8000
}
EOF

echo "== 6/6 Build and start (the first build takes 10-20 minutes)"
cd "$APP"
"${COMPOSE[@]}" up -d --build --remove-orphans
# Caddy reads its config only when it starts, and compose does not recreate it when
# just the mounted Caddyfile changed; restart it so a new login or access mode applies.
"${COMPOSE[@]}" restart caddy
"${COMPOSE[@]}" ps

echo "Waiting for the agents (Policy Stance builds its cache on first start, ~10 minutes)..."
for i in $(seq 1 120); do
  health=$("${COMPOSE[@]}" exec -T orchestrator python -c "import urllib.request,json; print(json.load(urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=5))['agents'])" 2>/dev/null || true)
  echo "  $health"
  if [ -n "$health" ] && ! echo "$health" | grep -q -E "unavailable|not_ready|error"; then
    echo "All four agents are up."
    break
  fi
  sleep 15
done
echo "DONE: https://${SITE#http://}  (or ${SITE} if you chose plain HTTP)"
