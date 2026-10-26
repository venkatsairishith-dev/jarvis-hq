#!/usr/bin/env bash
# OmniRoute 3.8.51 -> 3.8.52 on hermes-agent-vm (owner, 2026-10-26: OmniRoute is the server endpoint). Run ON THE VM as a sudoer.
#   bash omniroute-upgrade.sh            dry run: inspect only, prints what it would do, changes nothing
#   bash omniroute-upgrade.sh --apply    backup, upgrade, restart, verify, roll back by itself on any failed check
#   bash omniroute-upgrade.sh --rollback restore the last backup and restart
# Facts (read 2026-10-26): npm package `omniroute`, latest 3.8.52 (published 2026-10-19), node >=24 ok; the unit is
# jarvis/deploy/jarvis-omniroute.service (user jarvis-router, app in ~/app, env in ~/.omniroute/.env, port 20128, loopback only).
# Never prints the env file or any key. Oracle Always Free only: nothing here creates or resizes a cloud resource.
set -euo pipefail
WANT="${OMNIROUTE_VERSION:-3.8.52}"
USER_R=jarvis-router
HOME_R=/home/$USER_R
APP=$HOME_R/app
DATA=$HOME_R/.omniroute
UNIT=jarvis-omniroute
PORT=20128
BK=/var/backups/jarvis-omniroute
NPM=/opt/node24/bin/npm
MODE="${1:-}"

say() { printf '%s\n' "$*"; }
run_r() { sudo -u "$USER_R" env HOME="$HOME_R" PATH=/opt/node24/bin:/usr/bin:/bin "$@"; }
cur_version() { run_r "$APP/node_modules/.bin/omniroute" --version 2>/dev/null | head -1 || echo unknown; }

inspect() {
  say "== inspect (read only) =="
  say "service: $(systemctl is-active $UNIT || true), enabled: $(systemctl is-enabled $UNIT || true)"
  say "installed version: $(cur_version)"
  say "latest on npm:     $(run_r $NPM view omniroute version 2>/dev/null || echo unreachable)"
  say "memory cap in unit: $(systemctl show $UNIT -p MemoryMax --value)  now: $(systemctl show $UNIT -p MemoryCurrent --value)"
  say "free RAM (MB): $(free -m | awk '/Mem:/ {print $7}')  free disk /home (MB): $(df -m $HOME_R | awk 'NR==2 {print $4}')"
  say "listening sockets for :$PORT (must be 127.0.0.1 or the tailscale address only):"
  ss -ltn "sport = :$PORT" | tail -n +2 || true
  say "unit hardening present: $(grep -c -E 'NoNewPrivileges|ProtectSystem|MemoryMax' /etc/systemd/system/$UNIT.service 2>/dev/null || echo 0) of 3 directives"
}

health() {            # $1 = key-less probe expecting 401/403 on an API route, and the service up on loopback
  local code
  code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 -H 'Authorization: Bearer wrong-key-on-purpose' "http://127.0.0.1:$PORT/v1/models" || echo 000)
  say "wrong key -> HTTP $code (want 401 or 403)"
  [[ "$code" == 401 || "$code" == 403 ]]
}

public_check() {      # the port must not answer on any non-loopback, non-tailscale address
  local bad=0
  while read -r ip; do
    case "$ip" in 127.*|100.6[4-9].*|100.[7-9]?.*|100.1[0-1]?.*|100.12[0-7].*|"") continue;; esac
    if curl -s -o /dev/null --max-time 3 "http://$ip:$PORT/" 2>/dev/null; then say "REACHABLE on $ip"; bad=1; fi
  done < <(hostname -I | tr ' ' '\n')
  [[ $bad == 0 ]]
}

backup() {
  sudo mkdir -p "$BK" && sudo chmod 700 "$BK"
  local stamp; stamp=$(date -u +%Y%m%dT%H%M%SZ)
  say "backup -> $BK/omniroute-$stamp.tar.gz (contains provider keys: never copy it off this VM, never print it)"
  sudo tar -czf "$BK/omniroute-$stamp.tar.gz" -C "$HOME_R" app .omniroute
  sudo chmod 600 "$BK/omniroute-$stamp.tar.gz"
  echo "$BK/omniroute-$stamp.tar.gz" | sudo tee "$BK/LAST" >/dev/null
}

rollback() {
  local f; f=$(sudo cat "$BK/LAST")
  say "rolling back from $f"
  sudo systemctl stop $UNIT || true
  sudo rm -rf "$APP"
  sudo tar -xzf "$f" -C "$HOME_R" app
  sudo chown -R $USER_R:$USER_R "$APP"
  sudo systemctl start $UNIT
  sleep 5
  health && say "rollback ok, version $(cur_version)"
}

case "$MODE" in
  "" ) inspect; say "dry run only. Next: bash $0 --apply"; exit 0;;
  --rollback ) rollback; exit 0;;
  --apply ) ;;
  * ) say "unknown option $MODE"; exit 2;;
esac

inspect
[[ $(free -m | awk '/Mem:/ {print $7}') -gt 800 ]] || { say "less than 800 MB RAM available: stop"; exit 3; }
[[ $(df -m $HOME_R | awk 'NR==2 {print $4}') -gt 1500 ]] || { say "less than 1.5 GB disk free: stop"; exit 3; }
backup
say "== upgrade to $WANT =="
sudo systemctl stop $UNIT
if ! run_r $NPM install --prefix "$APP" "omniroute@$WANT" --no-audit --no-fund; then say "npm install failed"; rollback; exit 4; fi
sudo systemctl start $UNIT
sleep 8
say "now running: $(cur_version)"
if ! { health && public_check; }; then say "verification failed"; rollback; exit 5; fi
say "OK: OmniRoute $(cur_version) answers on loopback, a wrong key is refused, the port is not reachable on a public address."
say "Next (manual, nothing printed): enter the Dahl key in OmniRoute's dashboard (http://127.0.0.1:$PORT via the SSH/Tailscale tunnel), create a gateway API key for the laptop, and store it on the laptop in D:\\jarvis-secrets\\providers\\omniroute.key."
